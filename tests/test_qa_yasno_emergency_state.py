"""End-to-end offline acceptance of explicit YASNO schedule cancellation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_app import APP, config
from test_yasno_handoff import fixture
from test_yasno_refresh import REFRESH
from test_yasno_schedule import response
from test_yasno_widget_preview import settings

import yasno_handoff as HANDOFF
from telegram_channel_yasno import append_yasno_forecast
from telegram_widget_data import (
    valid_emergency_notice,
    widget_public_data,
)
from telegram_widget_fallback import widget_fallback_text
from yasno_cache import atomic_write, build_cache, fetch_to_cache
from yasno_schedule import YasnoEmergency, parse_provider_state, parse_schedule

KYIV = ZoneInfo("Europe/Kyiv")


def emergency_response(now: datetime) -> dict:
    """Match the provider status without inventing intervals."""
    data = response(now)
    record = data["10.1"]
    record["updatedOn"] = (now - timedelta(seconds=10)).isoformat()
    for key in ("today", "tomorrow"):
        record[key]["status"] = "EmergencyShutdowns"
        record[key]["slots"] = []
    return data


class YasnoEmergencyStateTests(unittest.TestCase):
    """Keep cancellation separate from power and schedule availability."""

    def test_valid_emergency_is_not_a_plan(self):
        """Only the new provider-state parser can accept an emergency."""
        now = datetime.now(KYIV)
        data = emergency_response(now)
        state = parse_provider_state(data, "10.1", now, now)
        self.assertIsInstance(state, YasnoEmergency)
        with self.assertRaisesRegex(ValueError, "not published"):
            parse_schedule(data, "10.1", now, now)

    def test_reject_emergency_with_intervals_wrong_day_or_unknown_status(self):
        """Malformed or arbitrary source states cannot cancel a schedule."""
        now = datetime.now(KYIV)
        for reason in ("slots", "date", "status"):
            with self.subTest(reason=reason):
                data = emergency_response(now)
                today = data["10.1"]["today"]
                if reason == "slots":
                    today["slots"] = response(now)["10.1"]["today"]["slots"]
                elif reason == "date":
                    today["date"] = (now - timedelta(days=1)).isoformat()
                else:
                    today["status"] = "WaitingForSchedule"
                with self.assertRaises(ValueError):
                    parse_provider_state(data, "10.1", now, now)

    def test_refresh_and_handoff_cancel_plan(self):
        """An explicit notice replaces the active forecast in both caches."""
        now = datetime.now(KYIV)
        with tempfile.TemporaryDirectory() as directory:
            source, target, gid = fixture(directory, age_s=1000)
            HANDOFF.handoff(str(source), str(target), "10.1", gid, "install")
            old = target.read_bytes()
            with patch(
                "yasno_cache.fetch_live",
                return_value=(emergency_response(now), now),
            ):
                result = REFRESH.refresh_once(str(source), "10.1", 900)
            self.assertIn("status=EmergencyShutdowns", result)
            HANDOFF.handoff(str(source), str(target), "10.1", gid, "install")
            self.assertNotEqual(old, target.read_bytes())
            self.assertIn(
                "status=EmergencyShutdowns",
                HANDOFF.handoff(
                    str(source), str(target), "10.1", gid, "status"
                ),
            )
            text = append_yasno_forecast(
                "Shelly 220.1 V", str(target), "10.1", "en"
            )
            self.assertIn("Emergency shutdowns", text)
            self.assertIn("220.1 V", text)
            self.assertNotIn("04:00", text)

    def test_older_or_equal_plan_cannot_revive_after_emergency(self):
        """Only a genuinely newer provider publication clears cancellation."""
        now = datetime.now(KYIV)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            notice = emergency_response(now)
            atomic_write(str(path), build_cache(notice, now))
            before = path.read_bytes()
            old = response(now)
            for published in (
                old["10.1"]["updatedOn"],
                notice["10.1"]["updatedOn"],
            ):
                with self.subTest(published=published):
                    old["10.1"]["updatedOn"] = published
                    with (
                        patch(
                            "yasno_cache.fetch_live", return_value=(old, now)
                        ),
                        self.assertRaisesRegex(
                            ValueError, "publication|newer"
                        ),
                    ):
                        fetch_to_cache(str(path), "10.1")
                    self.assertEqual(path.read_bytes(), before)
            old["10.1"]["updatedOn"] = now.isoformat()
            with patch("yasno_cache.fetch_live", return_value=(old, now)):
                restored = fetch_to_cache(str(path), "10.1")
            self.assertEqual(restored.status, "ScheduleApplies")
            self.assertEqual(len(restored.bars), 48)

    def test_emergency_handoff_blocks_older_plan_even_with_new_capture(self):
        """Replaying old provider content cannot replace a newer notice."""
        with tempfile.TemporaryDirectory() as directory:
            source, target, gid = fixture(directory)
            now = datetime.now(KYIV)
            atomic_write(
                str(source), build_cache(emergency_response(now), now)
            )
            HANDOFF.handoff(str(source), str(target), "10.1", gid, "install")
            original = target.read_bytes()
            atomic_write(
                str(source), build_cache(response(now), datetime.now(KYIV))
            )
            with self.assertRaisesRegex(ValueError, "publication"):
                HANDOFF.handoff(
                    str(source), str(target), "10.1", gid, "install"
                )
            self.assertEqual(target.read_bytes(), original)

    def test_channel_localization_without_fake_intervals(self):
        """All supported languages distinguish notice from telemetry."""
        now = datetime.now(KYIV)
        labels = {
            "uk": "Аварійні відключення",
            "ru": "Аварийные отключения",
            "en": "Emergency shutdowns",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notice.json"
            atomic_write(str(path), build_cache(emergency_response(now), now))
            for lang, label in labels.items():
                with self.subTest(lang=lang):
                    text = append_yasno_forecast(
                        "Shelly 220.1", str(path), "10.1", lang
                    )
                    self.assertIn(label, text)
                    self.assertIn("Shelly 220.1", text)
                    self.assertNotIn("04:00", text)


class EmergencyWidgetTests(unittest.TestCase):
    """Exercise actual graphic and text builders, never Telegram or HTTP."""

    def test_notice_blocks_community_fallback_and_keeps_all_phases(self):
        """Official emergency state takes precedence over community plans."""
        now = datetime.now(KYIV)
        monitor = APP.Monitor(
            config(shelly_meter_type="pro3em", telegram_language="en")
        )
        monitor.state.success(221.0, 50.0, 1.0)
        monitor.phase_states["L2"].success(222.0, 50.0, 1.0)
        monitor.phase_states["L3"].success(223.0, 50.0, 1.0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notice.json"
            atomic_write(str(path), build_cache(emergency_response(now), now))
            env = settings("")
            env["YASNO_TRUSTED_CACHE_FILE"] = str(path)
            with (
                patch.dict(os.environ, env),
                patch.object(APP, "cached_community_preview_args") as fallback,
            ):
                text = APP.private_widget_preview_text(monitor)
                state = json.loads(APP.private_graphic_preview_state(monitor))
                fallback.assert_not_called()
            self.assertIn("Emergency shutdowns", text)
            self.assertEqual(state["n"], "emergency")
            self.assertNotIn("b", state)
            self.assertEqual(state["v"], 221.0)
            self.assertEqual(state["p"]["L2"]["v"], 222.0)
            self.assertEqual(state["p"]["L3"]["v"], 223.0)
            for style in ("v4", "v5"):
                with self.subTest(style=style):
                    image = path.with_name(style + ".png")
                    with patch.dict(
                        os.environ, {"TELEGRAM_GRAPHIC_STYLE": style}
                    ):
                        result = APP.render_card_png(state, image)
                    self.assertFalse(result["forecast_visible"])
                    self.assertTrue(result["live_voltage_visible"])
                    self.assertTrue(image.read_bytes().startswith(b"\x89PNG"))

    def test_stale_or_conflicting_notice_cannot_show_current_forecast(self):
        """A replay or forged simultaneous bars/notice is hidden."""
        now = datetime.now(KYIV).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        stamp = now.timestamp()
        state = json.loads(
            widget_public_data(
                {},
                group="10.1",
                day=now.date().isoformat(),
                forecast_updated=stamp,
                forecast_source="yasno",
                forecast_notice="emergency",
                now=stamp,
                language="en",
            )
        )
        self.assertTrue(valid_emergency_notice(state, stamp))
        self.assertIn(state["e"] - state["a"], (82800, 86400, 90000))
        wrong_start = {**state, "a": state["a"] + 1}
        self.assertFalse(valid_emergency_notice(wrong_start, stamp))
        self.assertFalse(valid_emergency_notice(state, stamp + 7201))
        expired = widget_fallback_text(json.dumps(state), now=stamp + 7201)
        self.assertNotIn("Emergency shutdowns", expired)
        state["b"] = "O" * 48
        self.assertFalse(valid_emergency_notice(state, stamp))
        text = widget_fallback_text(json.dumps(state), now=stamp)
        self.assertNotIn("Emergency shutdowns", text)
        self.assertNotIn("Forecast ·", text)


if __name__ == "__main__":
    unittest.main()
