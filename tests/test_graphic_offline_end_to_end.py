"""Accept stale-safe Telegram cards during a power outage."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_card_png import (
    FONT,
    OFFLINE_HINT,
    draw_header,
    label,
    render_card_png,
)
from telegram_smooth_type import draw_voltage_type
from telegram_widget_data import widget_public_data

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 10, 10, 25, tzinfo=KYIV).timestamp()


def make_card_state(
    connectivity: str,
    voltage: float = 216.9,
    age_seconds: float = 6500,
    bars: str | None = "O" * 48,
) -> dict:
    """Create a synthetic health snapshot with optional fresh YASNO slots."""

    health = {
        "connectivity": connectivity,
        "voltage": voltage,
        "frequency": 50.0,
        "last_success": NOW - age_seconds,
    }
    options = {"now": NOW, "language": "uk"}
    if bars is not None:
        options.update(
            {
                "group": "10.1",
                "bars": bars,
                "day": datetime.fromtimestamp(NOW, KYIV).date().isoformat(),
                "forecast_updated": NOW - 30,
                "forecast_source": "yasno",
            }
        )
    return json.loads(widget_public_data(health, **options))


class OfflineGraphicAcceptanceTests(unittest.TestCase):
    """Confirm offline status never exposes a stale or invented voltage."""

    def setUp(self):
        """Create a throwaway PNG directory, never touching user files."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name) / "graphic.png"

    def render(self, state: dict) -> tuple[dict, bytes]:
        """Return renderer metadata and the exact PNG bytes."""

        result = render_card_png(state, self.target, now=NOW)
        return result, self.target.read_bytes()

    def test_outage_hides_old_voltage_despite_forecast_power_on(self):
        """A green YASNO plan cannot override offline Shelly telemetry."""

        state = make_card_state("offline", bars="O" * 48)
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))
        report, image = self.render(state)
        self.assertEqual(report["status"], "offline")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])
        state_without_history = make_card_state("offline", voltage=0)
        _, zero_image = self.render(state_without_history)
        self.assertEqual(image, zero_image)

    def test_offline_without_yasno_hides_both_unverified_sources(self):
        """Missing schedule data cannot make an outage look normal."""

        report, _ = self.render(make_card_state("offline", bars=None))
        self.assertEqual(report["status"], "offline")
        self.assertFalse(report["live_voltage_visible"])
        self.assertFalse(report["forecast_visible"])

    def test_offline_no_forecast_mentions_likely_not_proven_outage(self):
        """An unreachable meter suggests a blackout without proving it."""

        state = make_card_state("offline", bars=None)
        with mock.patch("telegram_card_png.label", wraps=label) as painter:
            self.render(state)
        labels = [call.args[1] for call in painter.call_args_list]
        self.assertIn(OFFLINE_HINT, labels)
        self.assertNotIn("CHECK SHELLY FOR ACTUAL POWER", labels)

    def test_offline_with_yasno_does_not_claim_mains_confirmed(self):
        """A fresh forecast remains independent of live power telemetry."""

        state = make_card_state("offline", bars="X" * 48)
        with mock.patch("telegram_card_png.label", wraps=label) as painter:
            self.render(state)
        labels = [call.args[1] for call in painter.call_args_list]
        self.assertIn("ЛИШЕ ПРОГНОЗ / " + OFFLINE_HINT, labels)

    def test_online_card_has_no_offline_warning(self):
        """Fresh measurements override the outage suspicion."""

        state = make_card_state("online", age_seconds=5, bars="O" * 48)
        with mock.patch("telegram_card_png.label", wraps=label) as painter:
            self.render(state)
        labels = [call.args[1] for call in painter.call_args_list]
        self.assertFalse(any(OFFLINE_HINT in item for item in labels))
        self.assertIn("ЛИШЕ ПРОГНОЗ / ФАКТ ЗА SHELLY", labels)

    def test_ukrainian_all_labels_have_nonblank_bitmap_glyphs(self):
        """Every Ukrainian label has real bitmap pixels."""

        phrases = (
            "SHELLY / СТАН МЕРЕЖІ",
            "НАПРУГА МЕРЕЖІ / L1",
            "ДАНІ НЕДОСТУПНІ",
            "ГРАФІК YASNO / ДТЕК",
            "НЕМАЄ АКТУАЛЬНОГО ГРАФІКА",
            "ГРУПА 10.1",
            "ЛИШЕ ПРОГНОЗ / ФАКТ ЗА SHELLY",
            "ОНОВЛЕНО 12:34:56",
            OFFLINE_HINT,
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                for glyph in phrase:
                    self.assertIn(glyph, FONT)
                    if not glyph.isspace():
                        self.assertIn("1", "".join(FONT[glyph]))

    def test_offline_main_card_uses_ukrainian_status_and_hint(self):
        """An outage reports likely loss of mains, not a measured zero."""

        with mock.patch("telegram_card_png.label", wraps=label) as painter:
            result, _ = self.render(make_card_state("offline", bars=None))
        captions = [call.args[1] for call in painter.call_args_list]
        self.assertEqual(result["status"], "offline")
        self.assertIn("ОФЛАЙН", captions)
        self.assertIn("ДАНІ НЕДОСТУПНІ", captions)
        self.assertIn("--- В", captions)
        self.assertIn(OFFLINE_HINT, captions)
        self.assertNotIn("CURRENT VALUE UNKNOWN", captions)

    def test_ukrainian_status_badge_never_overflows(self):
        """All translated statuses stay inside the top-right badge."""

        background = bytearray(b"\x00" * (960 * 560 * 3))
        for status in ("online", "offline", "stale", "unknown"):
            with self.subTest(status=status):
                with mock.patch(
                    "telegram_card_png.label", wraps=label
                ) as painter:
                    draw_header(background, {"status": status})
                right = [
                    call.args[2] + len(call.args[1]) * call.args[5] * 6
                    for call in painter.call_args_list
                    if call.args[2] >= 700
                ]
                self.assertTrue(right)
                self.assertLessEqual(max(right), 900)

    def test_online_main_card_uses_ukrainian_labels(self):
        """Fresh voltage/frequency and source labels stay legible."""

        state = make_card_state("online", age_seconds=5, bars="O" * 48)
        with (
            mock.patch("telegram_card_png.label", wraps=label) as painter,
            mock.patch(
                "telegram_card_png.draw_voltage_type",
                wraps=draw_voltage_type,
            ) as vector,
        ):
            result, _ = self.render(state)
        captions = [call.args[1] for call in painter.call_args_list]
        self.assertEqual(result["status"], "online")
        self.assertIn("ОНЛАЙН", captions)
        self.assertIn("НАПРУГА МЕРЕЖІ / L1", captions)
        vector.assert_called_once()
        self.assertEqual(vector.call_args.args[2], f"{state['v']:.1f}")
        self.assertIn("ГРАФІК YASNO / ДТЕК", captions)
        self.assertIn("ГРУПА 10.1", captions)

    def test_stale_online_flag_is_not_accepted_as_fresh(self):
        """Old successful reads are unknown even before offline detection."""

        report, _ = self.render(make_card_state("online", age_seconds=6500))
        self.assertEqual(report["status"], "stale")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])

    def test_recovering_without_fresh_reading_is_not_power_restored(self):
        """Recovery connectivity is not proof of fresh mains voltage."""

        state = make_card_state("recovering", age_seconds=6500)
        self.assertNotIn("v", state)
        report, _ = self.render(state)
        self.assertNotEqual(report["status"], "online")
        self.assertFalse(report["live_voltage_visible"])

    def test_valid_new_measurement_restores_online_independently_of_yasno(
        self,
    ):
        """Fresh power data wins over a schedule forecasting no electricity."""

        state = make_card_state(
            "online", voltage=224.7, age_seconds=5, bars="X" * 48
        )
        report, _ = self.render(state)
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["live_voltage_visible"])
        self.assertEqual(report["forecast_off_slots"], 48)

    def test_fresh_but_zero_online_value_cannot_be_a_live_card(self):
        """An online flag plus zero volts is not physical supply proof."""

        state = make_card_state("online", voltage=0, age_seconds=1)
        self.assertEqual(state["s"], "unknown")
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))
        report, _ = self.render(state)
        self.assertEqual(report["status"], "unknown")
        self.assertFalse(report["live_voltage_visible"])

    def test_offline_zero_never_leaks_into_public_widget_state(self):
        """Synthetic zero is not substituted for a disconnected meter."""

        state = make_card_state("offline", voltage=0, age_seconds=0)
        self.assertEqual(state["s"], "offline")
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))
        report, _ = self.render(state)
        self.assertFalse(report["live_voltage_visible"])


if __name__ == "__main__":
    unittest.main()
