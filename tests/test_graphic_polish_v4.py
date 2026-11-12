"""Design Polish v4 keeps typography smooth and YASNO periods truthful."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram_card_png import (
    AMBER,
    GREEN,
    HEIGHT,
    INK,
    MUTED,
    PANEL,
    RED,
    WIDTH,
    draw_hour_tiles,
    label,
    render_card_png,
)
from telegram_smooth_type import draw_voltage_type

KYIV = ZoneInfo("Europe/Kyiv")
BARS = "O" * 17 + "X" * 31


def clock(hour: int, minute: int) -> float:
    """Produce one local half-hour clock time for Nov 12."""

    return datetime(2026, 11, 12, hour, minute, tzinfo=KYIV).timestamp()


def pixel(buffer: bytearray, x: int, y: int, width: int = WIDTH) -> bytes:
    """Read one unchanged RGB triple from raw render memory."""

    start = (y * width + x) * 3
    return bytes(buffer[start : start + 3])


class GraphicPolishV4Tests(unittest.TestCase):
    """Enforce past, present, future and clean baseline typography."""

    def test_unit_stays_on_big_number_baseline(self):
        """The Cyrillic unit is no longer a lowered bitmap label."""

        canvas = bytearray(PANEL * (480 * 310))
        x, y = draw_voltage_type(canvas, 480, "213.0", 60, 199, INK, GREEN)
        self.assertEqual((x, y), (330, 219))
        digit_bottom = 199 + int(0.91 * 64)
        unit_bottom = 219 + int(0.91 * 44)
        self.assertLessEqual(abs(digit_bottom - unit_bottom), 2)
        self.assertNotEqual(pixel(canvas, 335, 230, 480), PANEL)

    def test_large_digits_have_fractional_antialias_coverage(self):
        """Smooth glyph edges must include blended, non-square pixels."""

        canvas = bytearray(PANEL * (480 * 310))
        draw_voltage_type(canvas, 480, "213.0", 60, 199, INK, GREEN)
        colors = {
            pixel(canvas, x, y, 480)
            for y in range(199, 263)
            for x in range(60, 325)
        }
        self.assertIn(INK, colors)
        self.assertTrue(
            any(c not in (INK, PANEL) and c[0] > PANEL[0] for c in colors)
        )

    def test_ui_label_edges_are_softly_antialiased(self):
        """Cyrillic UI labels blend partial pixels instead of hard squares."""

        canvas = bytearray(PANEL * WIDTH * HEIGHT)
        label(canvas, "ГРАФІК YASNO", 60, 54, INK, 4)
        colors = {
            pixel(canvas, x, y) for y in range(54, 83) for x in range(60, 300)
        }
        self.assertIn(INK, colors)
        self.assertTrue(any(c not in (INK, PANEL) for c in colors))

    def test_completed_slot_is_dimmed_and_diagonally_struck(self):
        """Past half-hours are crossed but do not lose forecast meaning."""

        canvas = bytearray(PANEL * WIDTH * HEIGHT)
        draw_hour_tiles(canvas, BARS, clock(8, 45))
        x = 60 + 2 * 140
        y = 383 + 45
        faded = bytes(
            (part * 42 + panel * 58) // 100
            for part, panel in zip(GREEN, PANEL)
        )
        self.assertEqual(pixel(canvas, x + 18, y + 35), faded)
        self.assertEqual(pixel(canvas, x + 6, y + 34), MUTED)

    def test_current_slot_has_accent_but_future_is_unmarked(self):
        """Current half-hour gets an amber border; future stays bright."""

        canvas = bytearray(PANEL * WIDTH * HEIGHT)
        draw_hour_tiles(canvas, BARS, clock(8, 45))
        y = 383 + 45
        current_x = 60 + 2 * 140 + 66
        future_x = 60 + 3 * 140
        self.assertEqual(pixel(canvas, current_x + 20, y + 2), AMBER)
        self.assertEqual(pixel(canvas, future_x + 20, y + 2), RED)
        self.assertEqual(pixel(canvas, future_x + 20, y + 34), RED)

    def test_current_half_switches_exactly_at_0830(self):
        """A new half-hour changes visual period without changing bars."""

        before = bytearray(PANEL * WIDTH * HEIGHT)
        after = bytearray(PANEL * WIDTH * HEIGHT)
        draw_hour_tiles(before, BARS, clock(8, 29))
        draw_hour_tiles(after, BARS, clock(8, 30))
        x = 60 + 2 * 140
        y = 383 + 45
        self.assertEqual(pixel(before, x + 20, y + 2), AMBER)
        self.assertEqual(pixel(before, x + 86, y + 2), RED)
        faded = bytes(
            (part * 42 + panel * 58) // 100
            for part, panel in zip(GREEN, PANEL)
        )
        self.assertEqual(pixel(after, x + 20, y + 2), faded)
        self.assertEqual(pixel(after, x + 86, y + 2), AMBER)

    def test_new_day_does_not_mark_tomorrow_as_past(self):
        """At midnight only the first half-hour is current."""

        canvas = bytearray(PANEL * WIDTH * HEIGHT)
        draw_hour_tiles(canvas, BARS, clock(0, 0))
        self.assertEqual(pixel(canvas, 80, 385), AMBER)
        self.assertEqual(pixel(canvas, 60 + 66 + 20, 385), GREEN)

    def test_offline_card_remains_without_live_voltage(self):
        """Typography polish must not revive historical voltage readings."""

        now = clock(8, 45)
        state = {"schema": 1, "s": "offline", "v": 213.0}
        with tempfile.TemporaryDirectory(prefix="polish-v4-") as folder:
            target = Path(folder) / "offline.png"
            report = render_card_png(state, target, now)
            self.assertEqual(report["status"], "offline")
            self.assertFalse(report["live_voltage_visible"])
            self.assertFalse(report["forecast_visible"])
            self.assertGreater(target.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
