"""Keep planned battery windows exact, chronological and source-labelled."""

from __future__ import annotations

import unittest

from telegram_channel_yasno import forecast_section
from telegram_schedule_periods import (
    duration_label,
    format_schedule_rows,
    schedule_periods,
)

EXAMPLE = "X" * 8 + "O" * 9 + "X" * 15 + "O" * 3 + "X" * 13


class SchedulePeriodTests(unittest.TestCase):
    """Never confuse forecasted electricity with measured Shelly power."""

    def test_user_requested_ukrainian_windows(self):
        """Reproduce all five intervals and exact displayed durations."""

        self.assertEqual(
            format_schedule_rows(EXAMPLE),
            [
                "🪫00:00 - 04:00 (≈4 год.)",
                "🔋04:00 - 08:30 (≈4.5 год.)",
                "🪫08:30 - 16:00 (≈7.5 год.)",
                "🔋16:00 - 17:30 (≈1.5 год.)",
                "🪫17:30 - 24:00 (≈6.5 год.)",
            ],
        )

    def test_full_day_duration_and_terminal_midnight(self):
        """A forecast must end at 24:00, never the next day's 00:00."""

        self.assertEqual(
            format_schedule_rows("O" * 48),
            ["🔋00:00 - 24:00 (≈24 год.)"],
        )
        self.assertEqual(
            format_schedule_rows("X" * 48),
            ["🪫00:00 - 24:00 (≈24 год.)"],
        )

    def test_half_hour_and_one_hour_boundaries(self):
        """Merge exact 30-minute slots, never round into whole hours."""

        bars = "X" + "O" * 2 + "X" * 45
        rows = format_schedule_rows(bars)
        self.assertEqual(rows[0], "🪫00:00 - 00:30 (≈0.5 год.)")
        self.assertEqual(rows[1], "🔋00:30 - 01:30 (≈1 год.)")

    def test_possible_outages_remain_ambiguous(self):
        """Unknown intervals show a warning, not a green battery."""

        rows = format_schedule_rows("?" * 3 + "O" * 45)
        self.assertEqual(rows[0], "⚠️00:00 - 01:30 (≈1.5 год.)")
        self.assertEqual(rows[1], "🔋01:30 - 24:00 (≈22.5 год.)")

    def test_valid_ranges_cover_exactly_one_day(self):
        """Every minute belongs to exactly one expected interval."""

        periods = schedule_periods(EXAMPLE)
        self.assertEqual(sum(row[3] for row in periods), 1440)
        self.assertEqual(sum(row[3] for row in periods if row[0] == "X"), 1080)
        self.assertEqual(sum(row[3] for row in periods if row[0] == "O"), 360)

    def test_no_period_is_silently_removed(self):
        """Twenty-four back-and-forth states yield 48 complete ranges."""

        periods = schedule_periods("XO" * 24)
        self.assertEqual(len(periods), 48)
        self.assertTrue(all(period[3] == 30 for period in periods))

    def test_text_overflow_is_explicit(self):
        """Long alternating plans have a compact overflow marker."""

        result = forecast_section("10.1", "XO" * 24, "uk")
        self.assertIn("(+40)", result)
        self.assertIn("🪫00:00 - 00:30", result)
        self.assertIn("🔋00:30 - 01:00", result)
        self.assertLess(len(result), 500)

    def test_error_for_missing_or_invalid_slot(self):
        """Never infer a full-day plan from incomplete or malformed input."""

        for value in ("", "X" * 47, "X" * 49, "O" * 47 + "Z", 0, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                schedule_periods(value)

    def test_duration_rejects_invalid_value(self):
        """Reject bools, negatives and out-of-range day lengths."""

        for value in (0, -1, 1441, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                duration_label(value)

    def test_english_and_russian_support(self):
        """Existing language choices retain an understandable duration unit."""

        self.assertIn("(≈4 h)", format_schedule_rows(EXAMPLE, "en")[0])
        self.assertIn("(≈4 ч.)", format_schedule_rows(EXAMPLE, "ru")[0])

    def test_no_current_voltage_in_forecast_section(self):
        """Forecast formatting never invents a measured electrical reading."""

        output = forecast_section("10.1", EXAMPLE, "uk")
        self.assertIn("Прогноз, а не показання", output)
        self.assertNotIn("220 В", output)
        self.assertNotIn("Напруга мережі", output)
        self.assertIn("🔋", output)
        self.assertIn("🪫", output)


if __name__ == "__main__":
    unittest.main()
