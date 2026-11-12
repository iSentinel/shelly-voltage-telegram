"""Check actual Nov 12 YASNO slots against independent Shelly telemetry."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram_card_png import render_card_png
from telegram_widget_data import widget_public_data
from telegram_widget_fallback import widget_fallback_text

KYIV = ZoneInfo("Europe/Kyiv")
# Verified 12 Nov 2026 from YASNO group 10.1 at 02:49 Kyiv.
VERIFIED_BARS = "XXXXXXXXOOOOOOOOOXXXXXXXXXXXXOOOOXXXXXXXXXXXOOOO"


def local_time(hour: int, minute: int, second: int) -> float:
    """Make the requested time on Nov 12 in Kyiv."""
    return datetime(
        2026, 11, 12, hour, minute, second, tzinfo=KYIV
    ).timestamp()


class VerifiedNov12ScheduleTests(unittest.TestCase):
    """Separate verified planned outages from physical meter evidence."""

    def setUp(self):
        """Keep rendered images confined to disposable files."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def widget(self, clock: float, online: bool, voltage: float) -> str:
        """Create a source-only widget state without Telegram calls."""
        return widget_public_data(
            {
                "connectivity": "online" if online else "offline",
                "voltage": voltage,
                "frequency": 50.0,
                "last_success": clock - (1 if online else 300),
            },
            group="10.1",
            bars=VERIFIED_BARS,
            day="2026-11-12",
            forecast_updated=clock - 30,
            forecast_source="yasno",
            now=clock,
            language="uk",
        )

    def test_verified_windows_match_real_schedule(self):
        """Protect the verified half-hour boundaries for group 10.1."""
        self.assertEqual(len(VERIFIED_BARS), 48)
        for hour, minute, expected in (
            (0, 0, "X"),
            (3, 30, "X"),
            (4, 0, "O"),
            (8, 0, "O"),
            (8, 30, "X"),
            (14, 30, "O"),
            (16, 30, "X"),
            (21, 30, "X"),
            (22, 0, "O"),
        ):
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(
                    VERIFIED_BARS[hour * 2 + (minute // 30)], expected
                )

    def test_real_online_voltage_survives_planned_off(self):
        """An X forecast never hides a fresh physical Shelly voltage."""
        for hour, minute, second, voltage in (
            (0, 0, 7, 219.2),
            (2, 50, 46, 224.8),
        ):
            with self.subTest(hour=hour, minute=minute):
                clock = local_time(hour, minute, second)
                state = self.widget(clock, True, voltage)
                png = self.folder / f"online-{hour}-{minute}.png"
                result = render_card_png(json.loads(state), png, now=clock)
                caption = widget_fallback_text(state, now=clock)
                self.assertEqual(result["status"], "online")
                self.assertTrue(result["live_voltage_visible"])
                self.assertTrue(result["forecast_visible"])
                self.assertEqual(
                    result["forecast_off_slots"], VERIFIED_BARS.count("X")
                )
                self.assertIn(f"{voltage:.1f}", caption)
                self.assertIn("Прогноз", caption)
                self.assertNotIn("Немає зв'язку", caption)

    def test_planned_on_does_not_revive_offline_voltage(self):
        """An O forecast never makes historical voltage appear live."""
        clock = local_time(4, 0, 5)
        self.assertEqual(VERIFIED_BARS[8], "O")
        state = self.widget(clock, False, 219.2)
        data = json.loads(state)
        result = render_card_png(data, self.folder / "offline.png", now=clock)
        caption = widget_fallback_text(state, now=clock)
        self.assertEqual(result["status"], "offline")
        self.assertFalse(result["live_voltage_visible"])
        self.assertTrue(result["forecast_visible"])
        self.assertTrue({"v", "f", "t"}.isdisjoint(data))
        self.assertNotIn("219.2", caption)
        self.assertIn("Немає зв'язку", caption)

    def test_0400_planned_off_to_on_keeps_live_voltage(self):
        """YASNO X-to-O at 04:00 cannot change a live Shelly reading."""

        before = local_time(3, 59, 59)
        after = local_time(4, 0, 0)
        self.assertEqual(VERIFIED_BARS[7:9], "XO")
        for clock, expected_slot in ((before, "X"), (after, "O")):
            with self.subTest(expected_slot=expected_slot):
                state = self.widget(clock, True, 223.7)
                card = render_card_png(
                    json.loads(state),
                    self.folder / f"transition-{expected_slot}.png",
                    now=clock,
                )
                caption = widget_fallback_text(state, now=clock)
                self.assertEqual(card["status"], "online")
                self.assertTrue(card["live_voltage_visible"])
                self.assertTrue(card["forecast_visible"])
                self.assertEqual(
                    card["forecast_off_slots"], VERIFIED_BARS.count("X")
                )
                self.assertIn("223.7", caption)
                self.assertIn("Прогноз", caption)

    def test_0400_planned_on_cannot_resurrect_offline_voltage(self):
        """A planned YASNO restoration never revives last known voltage."""

        before = local_time(3, 59, 59)
        after = local_time(4, 0, 0)
        for clock, expected_slot in ((before, "X"), (after, "O")):
            with self.subTest(expected_slot=expected_slot):
                state = self.widget(clock, False, 213.4)
                data = json.loads(state)
                card = render_card_png(
                    data,
                    self.folder / f"lost-{expected_slot}.png",
                    now=clock,
                )
                caption = widget_fallback_text(state, now=clock)
                self.assertEqual(card["status"], "offline")
                self.assertFalse(card["live_voltage_visible"])
                self.assertTrue(card["forecast_visible"])
                self.assertTrue({"v", "f", "t"}.isdisjoint(data))
                self.assertNotIn("213.4", caption)
                self.assertIn("Немає зв'язку", caption)

    def test_0830_planned_on_to_off_keeps_fresh_shelly_reading(self):
        """A forecast O-to-X transition cannot conceal live voltage."""

        before = local_time(8, 29, 59)
        after = local_time(8, 30, 0)
        self.assertEqual(VERIFIED_BARS[16:18], "OX")
        for clock, state_letter in ((before, "O"), (after, "X")):
            with self.subTest(forecast=state_letter):
                state = self.widget(clock, True, 222.4)
                card = render_card_png(
                    json.loads(state),
                    self.folder / f"0830-online-{state_letter}.png",
                    now=clock,
                )
                caption = widget_fallback_text(state, now=clock)
                self.assertEqual(card["status"], "online")
                self.assertTrue(card["live_voltage_visible"])
                self.assertTrue(card["forecast_visible"])
                self.assertIn("222.4", caption)
                self.assertIn("Прогноз", caption)

    def test_0830_forecast_change_never_creates_physical_outage(self):
        """If the meter is offline, no YASNO state reveals old volts."""

        before = local_time(8, 29, 59)
        after = local_time(8, 30, 0)
        for clock, state_letter in ((before, "O"), (after, "X")):
            with self.subTest(forecast=state_letter):
                state = self.widget(clock, False, 222.4)
                data = json.loads(state)
                card = render_card_png(
                    data,
                    self.folder / f"0830-offline-{state_letter}.png",
                    now=clock,
                )
                caption = widget_fallback_text(state, now=clock)
                self.assertEqual(card["status"], "offline")
                self.assertFalse(card["live_voltage_visible"])
                self.assertTrue(card["forecast_visible"])
                self.assertTrue({"v", "f", "t"}.isdisjoint(data))
                self.assertNotIn("222.4", caption)
                self.assertIn("Немає зв'язку", caption)

    def test_1043_meter_loss_is_independent_of_0830_forecast(self):
        """Physical monitoring can turn offline long after YASNO becomes X."""

        online_clock = local_time(10, 42, 44)
        offline_clock = local_time(10, 43, 26)
        self.assertEqual(VERIFIED_BARS[21], "X")
        for clock, online in ((online_clock, True), (offline_clock, False)):
            with self.subTest(meter_online=online):
                state = self.widget(clock, online, 214.1)
                data = json.loads(state)
                file = self.folder / f"late-outage-{online}.png"
                rendered = render_card_png(data, file, now=clock)
                caption = widget_fallback_text(state, now=clock)
                self.assertTrue(rendered["forecast_visible"])
                self.assertEqual(
                    rendered["forecast_off_slots"], VERIFIED_BARS.count("X")
                )
                self.assertEqual(rendered["live_voltage_visible"], online)
                self.assertEqual(
                    rendered["status"], "online" if online else "offline"
                )
                if online:
                    self.assertIn("214.1", caption)
                    self.assertNotIn("Немає зв'язку", caption)
                else:
                    self.assertTrue({"v", "f", "t"}.isdisjoint(data))
                    self.assertNotIn("214.1", caption)
                    self.assertIn("Немає зв'язку", caption)

    def test_stale_then_fresh_recovery_with_planned_yasno_x(self):
        """Only a new physical sample permits voltage after the outage."""

        now = local_time(11, 21, 0)
        self.assertEqual(VERIFIED_BARS[22], "X")
        stale = widget_public_data(
            {
                "connectivity": "online",
                "voltage": 214.1,
                "frequency": 50.0,
                "last_success": local_time(10, 42, 44),
            },
            group="10.1",
            bars=VERIFIED_BARS,
            day="2026-11-12",
            forecast_updated=now - 30,
            forecast_source="yasno",
            now=now,
            language="uk",
        )
        fresh = self.widget(now, True, 216.8)
        for name, state, visible in (
            ("stale-physical", stale, False),
            ("fresh-physical", fresh, True),
        ):
            with self.subTest(stage=name):
                data = json.loads(state)
                output = self.folder / f"recovery-{name}.png"
                card = render_card_png(data, output, now=now)
                caption = widget_fallback_text(state, now=now)
                self.assertTrue(card["forecast_visible"])
                self.assertEqual(card["live_voltage_visible"], visible)
                if visible:
                    self.assertEqual(card["status"], "online")
                    self.assertIn("216.8", caption)
                else:
                    self.assertNotEqual(card["status"], "online")
                    self.assertTrue({"v", "f", "t"}.isdisjoint(data))
                    self.assertNotIn("214.1", caption)


if __name__ == "__main__":
    unittest.main()
