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


if __name__ == "__main__":
    unittest.main()
