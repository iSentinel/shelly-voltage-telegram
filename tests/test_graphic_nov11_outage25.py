"""Regression for natural outage #25 and 11 Nov YASNO group 10.1.

No real Telegram calls, power claims, or production DB modifications.
"""

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
DAY = "2026-11-11"
BARS = "XXOOOOOOOXXXXXXXXXXXOOOOOOXXXXXXXXXXXXOOOOOOXXXX"


def moment(hour: int, minute: int, second: int = 0) -> float:
    """Return exact Kyiv-local Nov 11 wall-clock timestamp."""

    return datetime(
        2026, 11, 11, hour, minute, second, tzinfo=KYIV
    ).timestamp()


def public_state(
    now: float,
    connection: str,
    voltage: float,
    age_seconds: float = 5,
    forecast: bool = True,
) -> dict:
    """Encode independent meter and approved daily forecast sources."""

    health = {
        "connectivity": connection,
        "voltage": voltage,
        "frequency": 50.0,
        "last_success": now - age_seconds,
    }
    kwargs = {"now": now, "group": "10.1", "language": "uk"}
    if forecast:
        kwargs.update(
            bars=BARS,
            day=DAY,
            forecast_source="yasno",
            forecast_updated=now - 300,
        )
    return json.loads(widget_public_data(health, **kwargs))


class NaturalOutage25GraphicTests(unittest.TestCase):
    """Test actual outage timing with reproducible synthetic readings."""

    def setUp(self) -> None:
        """Use isolated images without persistent source or Telegram writes."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def render(self, state: dict, timepoint: float, filename: str):
        """Render candidate PNG and return the status, bytes and metadata."""

        path = self.folder / filename
        result = render_card_png(state, path, now=timepoint)
        pixels = path.read_bytes()
        self.assertTrue(pixels.startswith(bytes.fromhex("89504e470d0a1a0a")))
        self.assertTrue(len(pixels) > 1024)
        return result, pixels

    def test_verified_day_is_48_independent_half_hour_slots(self) -> None:
        """Count the actual trusted November 11 plan separately from meter."""

        self.assertEqual(len(BARS), 48)
        self.assertEqual(BARS.count("X"), 29)
        self.assertEqual(BARS.count("O"), 19)
        self.assertEqual(BARS[25], "O")
        self.assertEqual(BARS[26], "X")
        self.assertEqual(BARS[37], "X")
        self.assertEqual(BARS[38], "O")

    def test_1259_is_planned_on_and_voltage_is_live(self) -> None:
        """An online sensor is online even at the schedule boundary."""

        clock = moment(12, 59, 50)
        state = public_state(clock, "online", 220.0)
        self.assertEqual(state["b"][25], "O")
        report, _ = self.render(state, clock, "before-1300.png")
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["live_voltage_visible"])

    def test_1300_scheduled_off_does_not_imply_shelly_offline(self) -> None:
        """A planned power cut is not a physical no-voltage measurement."""

        clock = moment(13, 0, 4)
        state = public_state(clock, "online", 214.9)
        self.assertEqual(state["b"][26], "X")
        report, _ = self.render(state, clock, "planned-off-only.png")
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["live_voltage_visible"])

    def test_130955_actual_offline_hides_historical_volts_and_hz(self):
        """At outage #25 start, 216.5V is history, not a live value."""

        clock = moment(13, 9, 55)
        state = public_state(clock, "offline", 216.5, age_seconds=41)
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))
        self.assertEqual(state["b"][26], "X")
        report, _ = self.render(state, clock, "offline-outage25.png")
        self.assertEqual(report["status"], "offline")
        self.assertTrue(report["forecast_visible"])
        self.assertEqual(report["forecast_off_slots"], 29)
        self.assertFalse(report["live_voltage_visible"])

    def test_offline_image_is_independent_from_last_good_voltage(self):
        """Changing historical volts must not change the offline PNG."""

        clock = moment(13, 15, 0)
        low = public_state(clock, "offline", 171.1, 400)
        high = public_state(clock, "offline", 237.5, 400)
        _, first = self.render(low, clock, "offline-old-low.png")
        _, second = self.render(high, clock, "offline-old-high.png")
        self.assertEqual(first, second)

    def test_offline_missing_forecast_does_not_invent_schedule(self):
        """No trusted forecast must mean no grid, not guessed 0V or OFF."""

        clock = moment(13, 20, 0)
        state = public_state(clock, "offline", 216.5, 600, False)
        self.assertNotIn("b", state)
        report, _ = self.render(state, clock, "offline-no-schedule.png")
        self.assertFalse(report["forecast_visible"])
        self.assertFalse(report["live_voltage_visible"])

    def test_fresh_recovery_remains_online_during_planned_off(self):
        """A restored live meter value overrides forecast assumptions."""

        clock = moment(13, 40, 15)
        state = public_state(clock, "online", 209.8)
        self.assertEqual(state["b"][27], "X")
        report, _ = self.render(state, clock, "recovery-planned-off.png")
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["live_voltage_visible"])

    def test_stale_online_flag_does_not_claim_live_measurement(self):
        """Even a cached online flag must hide outdated readings."""

        clock = moment(13, 20, 0)
        state = public_state(clock, "online", 216.5, 150)
        report, _ = self.render(state, clock, "stale.png")
        self.assertEqual(report["status"], "stale")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])


if __name__ == "__main__":
    unittest.main()
