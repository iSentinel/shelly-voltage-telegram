"""Accept stale-safe Telegram cards during a power outage."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram_card_png import render_card_png
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

    def test_offline_zero_never_leaks_into_public_widget_state(self):
        """Synthetic zero is not substituted for a disconnected meter."""

        state = make_card_state("offline", voltage=0, age_seconds=0)
        self.assertEqual(state["s"], "offline")
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))
        report, _ = self.render(state)
        self.assertFalse(report["live_voltage_visible"])


if __name__ == "__main__":
    unittest.main()
