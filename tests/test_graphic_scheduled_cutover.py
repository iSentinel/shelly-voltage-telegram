"""Pin outage-forecast independence to the real November 10 transition."""

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
DAY = "2026-11-10"
PLAN = "O" * 35 + "X" * 13


def sample_time(hour: int, minute: int, second: int) -> float:
    """Return the actual Kyiv wall-clock instant for synthetic regression."""

    return datetime(
        2026, 11, 10, hour, minute, second, tzinfo=KYIV
    ).timestamp()


def card_state(
    clock: float,
    connectivity: str,
    voltage: float,
    age: float = 5,
    with_plan: bool = True,
) -> dict:
    """Build current-source data independent from a verified YASNO plan."""

    health = {
        "connectivity": connectivity,
        "voltage": voltage,
        "frequency": 50.0,
        "last_success": clock - age,
    }
    options = {"now": clock, "group": "10.1", "language": "uk"}
    if with_plan:
        options.update(
            bars=PLAN,
            day=DAY,
            forecast_source="yasno",
            forecast_updated=clock - 30,
        )
    return json.loads(widget_public_data(health, **options))


class ScheduledCutoverGraphicTests(unittest.TestCase):
    """Confirm forecast start does not imply physical loss of voltage."""

    def setUp(self):
        """Use only disposable PNG files, never production Telegram."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def render(self, state: dict, clock: float, name: str = "card.png"):
        """Render the exact 960x800 card and return its output metadata."""

        path = self.root / name
        result = render_card_png(state, path, now=clock)
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        return result, path.read_bytes()

    def test_before_1730_normal_grid_is_measured(self):
        """Scheduled power exists at 17:29, measured voltage is independent."""

        clock = sample_time(17, 29, 50)
        state = card_state(clock, "online", 220.0)
        self.assertEqual(state["b"][34:36], "OX")
        report, _ = self.render(state, clock)
        self.assertEqual(state["v"], 220.0)
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["live_voltage_visible"])

    def test_1730_forecast_off_does_not_mean_meter_offline(self):
        """At 17:30:04, a live 203.2 V reading defeats forecast inference."""

        clock = sample_time(17, 30, 4)
        state = card_state(clock, "online", 203.2, age=9)
        self.assertEqual(state["b"][35], "X")
        report, _ = self.render(state, clock)
        self.assertEqual(state["v"], 203.2)
        self.assertEqual(report["status"], "online")
        self.assertEqual(report["forecast_off_slots"], 13)
        self.assertTrue(report["live_voltage_visible"])

    def test_sag_at_1731_remains_a_measurement_not_an_outage(self):
        """Low but real voltage is not a synthetic zero or offline claim."""

        clock = sample_time(17, 31, 4)
        state = card_state(clock, "online", 197.4, age=9)
        report, _ = self.render(state, clock)
        self.assertEqual(state["v"], 197.4)
        self.assertEqual(state["f"], 50.0)
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["forecast_visible"])

    def test_173152_offline_hides_historic_voltage(self):
        """On loss of Shelly, 193.8 V becomes strictly historical."""

        clock = sample_time(17, 31, 52)
        state = card_state(clock, "offline", 193.8, age=46)
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))
        report, _ = self.render(state, clock)
        self.assertEqual(report["status"], "offline")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])
        self.assertEqual(report["forecast_off_slots"], 13)

    def test_offline_png_does_not_depend_on_last_good_voltage(self):
        """No historical voltage must leak into the offline card's pixels."""

        clock = sample_time(17, 32, 4)
        old = card_state(clock, "offline", 193.8, age=58)
        other = card_state(clock, "offline", 220.0, age=58)
        _, left = self.render(old, clock, "left.png")
        _, right = self.render(other, clock, "right.png")
        self.assertEqual(left, right)

    def test_loss_of_trusted_schedule_does_not_invent_48_slots(self):
        """Offline status is safe without a trustworthy current YASNO day."""

        clock = sample_time(17, 32, 4)
        state = card_state(clock, "offline", 193.8, with_plan=False)
        report, _ = self.render(state, clock)
        self.assertFalse(report["forecast_visible"])
        self.assertFalse(report["live_voltage_visible"])

    def test_stale_online_flag_does_not_restore_voltage(self):
        """A 2-minute-old sample may not be labelled current at 17:30."""

        clock = sample_time(17, 30, 4)
        state = card_state(clock, "online", 203.2, age=120)
        report, _ = self.render(state, clock)
        self.assertEqual(report["status"], "stale")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])

    def test_fresh_yasno_plan_never_implies_measured_zero(self):
        """Offline states carry no voltage key even with red X forecast."""

        clock = sample_time(17, 32, 4)
        state = card_state(clock, "offline", 0.0)
        report, _ = self.render(state, clock)
        self.assertNotIn("v", state)
        self.assertNotIn("t", state)
        self.assertFalse(report["live_voltage_visible"])


if __name__ == "__main__":
    unittest.main()
