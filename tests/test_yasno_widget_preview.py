"""Private YASNO preview remains source-labelled, fresh and read-only."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_app import APP, config
from test_compact_preview import make_monitor
from test_yasno_schedule import response

from telegram_widget_data import widget_public_data
from telegram_widget_fallback import widget_fallback_text
from yasno_cache import atomic_write, build_cache

KYIV = ZoneInfo("Europe/Kyiv")


def write_yasno(path: Path, status: str = "ScheduleApplies") -> None:
    """Write one test-only response with today's local calendar date."""

    data = response(datetime.now(KYIV))
    data["10.1"]["today"]["status"] = status
    path.write_text(json.dumps(data), encoding="utf-8")


def settings(path: str) -> dict[str, str]:
    """Make an isolated, explicitly opted-in local cache configuration."""

    return {
        "YASNO_CACHE_FILE": path,
        "YASNO_TRUSTED_CACHE_FILE": "",
        "YASNO_GROUP": "10.1",
        "COMMUNITY_OUTAGE_CACHE_FILE": "",
        "COMMUNITY_OUTAGE_REGION": "",
        "COMMUNITY_OUTAGE_GROUP": "",
    }


class YasnoWidgetPreviewTests(unittest.TestCase):
    """Do not confuse a source forecast with measured mains voltage."""

    def test_valid_yasno_cache_uses_explicit_source(self):
        """Present a direct-YASNO-format forecast only when opted in."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            write_yasno(path)
            monitor = make_monitor("uk")
            monitor.state.success(222.1, 50.0, 4.0, 1)
            with patch.dict(os.environ, settings(str(path))):
                caption = APP.private_widget_preview_text(monitor)
        self.assertIn("Напруга: 222.1 В", caption)
        self.assertIn("04:00–11:30", caption)
        self.assertIn("14:30–22:00", caption)
        self.assertIn("Джерело: YASNO · DTEK Kyiv", caption)
        self.assertNotIn("outage-data-ua", caption)

    def test_waiting_for_schedule_hides_forecast(self):
        """Unpublished YASNO data must not imply electricity is on."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            write_yasno(path, "WaitingForSchedule")
            monitor = make_monitor("ru")
            monitor.state.success(220.0, 50.0, 3.0, 1)
            with patch.dict(os.environ, settings(str(path))):
                caption = APP.private_widget_preview_text(monitor)
        self.assertIn("Напряжение: 220.0 В", caption)
        self.assertNotIn("Прогноз ·", caption)
        self.assertNotIn("YASNO", caption)

    def test_stale_yasno_falls_back_to_meter(self):
        """An old file modification time cannot certify fresh data."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            write_yasno(path)
            old = time.time() - 7201
            os.utime(path, (old, old))
            monitor = make_monitor("en")
            monitor.state.success(224.2, 50.0, 3.0, 1)
            with patch.dict(os.environ, settings(str(path))):
                caption = APP.private_widget_preview_text(monitor)
        self.assertIn("224.2 V", caption)
        self.assertNotIn("YASNO", caption)
        self.assertNotIn("04:00", caption)

    def test_expired_yasno_can_use_separate_community_cache(self):
        """Keep source attribution when an alternate cache is trusted."""

        monitor = make_monitor("ru")
        monitor.state.success(223.0, 50.0, 2.0, 1)
        config = settings("/tmp/does-not-exist-yasno.json")
        config.update(
            {
                "COMMUNITY_OUTAGE_CACHE_FILE": "/tmp/community.json",
                "COMMUNITY_OUTAGE_REGION": "kyiv",
                "COMMUNITY_OUTAGE_GROUP": "10.1",
            }
        )
        bars = "X" * 2 + "O" * 46
        with (
            patch.dict(os.environ, config),
            patch.object(APP, "read_snapshot", return_value={}),
            patch.object(
                APP,
                "slots_from_community",
                return_value=(
                    bars,
                    datetime.now(KYIV).date().isoformat(),
                    time.time() - 20,
                ),
            ),
        ):
            caption = APP.private_widget_preview_text(monitor)
        self.assertIn("Неофициальный источник: outage-data-ua", caption)
        self.assertNotIn("YASNO", caption)
        self.assertIn("223.0 В", caption)

    def test_pro3em_phases_remain_independent(self):
        """A YASNO plan must not overwrite any physical phase reading."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "yasno.json"
            write_yasno(path)
            monitor = APP.Monitor(
                config(shelly_meter_type="pro3em", telegram_language="en")
            )
            monitor.state.success(221.1, 50.0, 3.0, 1)
            monitor.phase_states["L2"].success(222.2, 50.0, 2.0, 1)
            monitor.phase_states["L3"].error("disconnected", 1)
            with patch.dict(os.environ, settings(str(path))):
                caption = APP.private_widget_preview_text(monitor)
        self.assertIn("Voltage: 221.1 V", caption)
        self.assertIn("L2: 222.2 V", caption)
        self.assertIn("L3: —", caption)
        self.assertIn("Source: YASNO", caption)

    def test_trusted_cache_takes_priority_over_raw(self):
        """An explicit sealed cache is preferred over legacy raw JSON."""

        with tempfile.TemporaryDirectory() as tmp:
            trusted = Path(tmp) / "verified.json"
            raw = Path(tmp) / "raw.json"
            raw.write_text("broken json", encoding="utf-8")
            now = datetime.now(KYIV)
            atomic_write(str(trusted), build_cache(response(now), now))
            env = settings(str(raw))
            env["YASNO_TRUSTED_CACHE_FILE"] = str(trusted)
            monitor = make_monitor("ru")
            monitor.state.success(221.7, 50.0, 3.0, 1)
            with patch.dict(os.environ, env):
                text = APP.private_widget_preview_text(monitor)
        self.assertIn("Источник: YASNO", text)
        self.assertIn("04:00–11:30", text)

    def test_fresh_verified_retrieval_keeps_older_daily_publication(self):
        """Display a published today's plan using its verified fetch clock."""

        with tempfile.TemporaryDirectory() as tmp:
            now = datetime.now(KYIV)
            source = response(now)
            source["10.1"]["updatedOn"] = (
                now - timedelta(hours=4)
            ).isoformat()
            trusted = Path(tmp) / "yasno.json"
            atomic_write(str(trusted), build_cache(source, now))
            env = settings("")
            env["YASNO_TRUSTED_CACHE_FILE"] = str(trusted)
            with patch.dict(os.environ, env):
                options = APP.cached_yasno_preview_args()
            self.assertIsNotNone(options)
            self.assertAlmostEqual(
                options["forecast_updated"], now.timestamp(), delta=5
            )
            state = json.loads(
                widget_public_data(
                    {"connectivity": "offline"},
                    now=now.timestamp(),
                    language="uk",
                    **options,
                )
            )
        self.assertEqual(state["src"], "yasno")
        self.assertEqual(state["d"], now.date().isoformat())
        self.assertEqual(len(state["b"]), 48)

    def test_corrupt_trusted_cache_does_not_downgrade_to_raw(self):
        """A configured trusted source cannot silently become raw data."""

        with tempfile.TemporaryDirectory() as tmp:
            trusted = Path(tmp) / "verified.json"
            raw = Path(tmp) / "raw.json"
            now = datetime.now(KYIV)
            write_yasno(raw)
            data = build_cache(response(now), now)
            data["source_url"] = "https://spoofed.example/"
            atomic_write(str(trusted), data)
            env = settings(str(raw))
            env["YASNO_TRUSTED_CACHE_FILE"] = str(trusted)
            monitor = make_monitor("en")
            monitor.state.success(226.0, 50.0, 3.0, 1)
            with patch.dict(os.environ, env):
                text = APP.private_widget_preview_text(monitor)
        self.assertIn("Voltage: 226.0 V", text)
        self.assertNotIn("YASNO", text)
        self.assertNotIn("04:00", text)

    def test_trusted_read_does_not_fetch_in_telegram_handler(self):
        """No live provider request is permitted during private preview."""

        with tempfile.TemporaryDirectory() as tmp:
            trusted = Path(tmp) / "verified.json"
            now = datetime.now(KYIV)
            atomic_write(str(trusted), build_cache(response(now), now))
            env = settings("")
            env["YASNO_TRUSTED_CACHE_FILE"] = str(trusted)
            monitor = make_monitor("en")
            monitor.state.success(219.9, 50.0, 2.0, 1)
            with (
                patch.dict(os.environ, env),
                patch("yasno_cache.fetch_live") as fetcher,
            ):
                text = APP.private_widget_preview_text(monitor)
            fetcher.assert_not_called()
        self.assertIn("Source: YASNO", text)

    def test_unknown_source_is_rejected(self):
        """Prevent arbitrary untrusted forecast source labels."""

        now = time.time()
        with self.assertRaisesRegex(ValueError, "source"):
            widget_public_data(
                {"connectivity": "offline"},
                group="10.1",
                bars="X" * 48,
                day=datetime.now(KYIV).date().isoformat(),
                forecast_updated=now - 10,
                forecast_source="attacker",
                now=now,
            )

    def test_direct_yasno_widget_state_and_caption(self):
        """Encode YASNO separately from the unofficial mirror."""

        now = time.time()
        payload = widget_public_data(
            {"connectivity": "offline"},
            group="10.1",
            bars="X" * 4 + "O" * 44,
            day=datetime.now(KYIV).date().isoformat(),
            forecast_updated=now - 10,
            forecast_source="yasno",
            language="uk",
            now=now,
        )
        self.assertEqual(json.loads(payload)["src"], "yasno")
        text = widget_fallback_text(payload, now=now)
        self.assertIn("Джерело: YASNO", text)
        self.assertIn("Немає зв'язку", text)


if __name__ == "__main__":
    unittest.main()
