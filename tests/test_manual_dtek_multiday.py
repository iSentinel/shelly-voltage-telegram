"""Two-day DTEK forecasts must never mix days or imply mains loss."""

from __future__ import annotations

import unittest
from datetime import date

from manual_dtek_forecast import (
    local_stamp,
    parse_manual_forecast,
    parse_manual_forecast_days,
    preview_manual_forecast,
)

TODAY = (
    "🗓 Прогнозований графік відключень на сьогодні, "
    "06.11 (П'ятниця), група 10.1:\n"
    "🪫00:00 - 05:30 (≈5.5 год.)\n"
    "🪫10:30 - 18:00 (≈7.5 год.)\n"
    "🪫20:00 - 24:00 (≈4 год.)\n"
    "УВАГА! Це офіційні графіки з сайту ДТЕК, та можуть "
    "відрізнятися від реальних відключень/включень.\n"
)
TOMORROW = (
    "🗓 Прогнозований графік відключень на завтра, "
    "07.11 (Субота), група 10.1:\n"
    "🪫00:00 - 02:30 (≈2.5 год.)\n"
    "🪫07:00 - 14:30 (≈7.5 год.)\n"
    "🪫17:30 - 24:00 (≈6.5 год.)\n"
)
BOTH = "-\n" + TODAY + "---\n" + TOMORROW


class ManualDtekTwoDayTests(unittest.TestCase):
    """Preserve two dates as separate approximate forecasts."""

    def test_exact_today_and_tomorrow(self):
        """Read two separate unmodified user-reported day schedules."""

        today, tomorrow = parse_manual_forecast_days(BOTH, 2026)
        self.assertEqual(today.day, "2026-11-06")
        self.assertEqual(tomorrow.day, "2026-11-07")
        self.assertEqual(today.group, tomorrow.group)
        self.assertEqual(today.group, "10.1")
        self.assertEqual(
            today.periods,
            (
                ("00:00", "05:30"),
                ("10:30", "18:00"),
                ("20:00", "24:00"),
            ),
        )
        self.assertEqual(
            tomorrow.periods,
            (
                ("00:00", "02:30"),
                ("07:00", "14:30"),
                ("17:30", "24:00"),
            ),
        )

    def test_official_label_not_independently_verified(self):
        """Relayed official labels are not provider-verified API evidence."""

        for item in parse_manual_forecast_days(BOTH, 2026):
            self.assertFalse(item.provider_verified)
            self.assertEqual(item.source, "user_supplied_unverified")
            self.assertIn("не перевірено", preview_manual_forecast(item, "uk"))

    def test_single_day_parser_rejects_combined_content(self):
        """A 6 November parser cannot swallow the next day's windows."""

        with self.assertRaisesRegex(ValueError, "multiple"):
            parse_manual_forecast(BOTH, date(2026, 11, 6))

    def test_tomorrow_parses_independently(self):
        """An isolated day seven cannot inherit day six's 20:00 window."""

        item = parse_manual_forecast(TOMORROW, date(2026, 11, 7))
        self.assertEqual(len(item.periods), 3)
        self.assertNotIn(("20:00", "24:00"), item.periods)

    def test_2400_today_is_midnight_tomorrow(self):
        """Explicit day-boundary clock handling must remain exact."""

        self.assertEqual(
            local_stamp(date(2026, 11, 6), "24:00"),
            local_stamp(date(2026, 11, 7), "00:00"),
        )

    def test_reversed_dates_rejected(self):
        """A two-day message cannot silently reverse calendar days."""

        with self.assertRaisesRegex(ValueError, "ordered"):
            parse_manual_forecast_days(TOMORROW + TODAY, 2026)

    def test_duplicate_dates_rejected(self):
        """An accidentally repeated day must not double the schedule."""

        with self.assertRaisesRegex(ValueError, "ordered"):
            parse_manual_forecast_days(TODAY + TODAY, 2026)

    def test_different_groups_rejected(self):
        """Mixed consumer groups are not one person's forecast."""

        updated = TOMORROW.replace("група 10.1", "група 10.2")
        with self.assertRaisesRegex(ValueError, "mixed groups"):
            parse_manual_forecast_days(TODAY + updated, 2026)

    def test_bad_tomorrow_time_rejected(self):
        """Malformed second-day intervals cannot be hidden by day one."""

        updated = TOMORROW.replace("17:30 - 24:00", "17:35 - 24:00")
        with self.assertRaises(ValueError):
            parse_manual_forecast_days(TODAY + updated, 2026)

    def test_wrong_year_rejected(self):
        """Year must not silently shift to the wrong calendar year."""

        updated = TOMORROW.replace("07.11", "07.11.2027")
        with self.assertRaisesRegex(ValueError, "year"):
            parse_manual_forecast_days(TODAY + updated, 2026)

    def test_three_days_rejected(self):
        """Bound the batch to the two relevant days."""

        with self.assertRaisesRegex(ValueError, "one or two"):
            parse_manual_forecast_days(BOTH + TODAY, 2026)

    def test_oversized_text_rejected(self):
        """Bound the size of untrusted pasted schedules."""

        with self.assertRaisesRegex(ValueError, "size limit"):
            parse_manual_forecast_days(BOTH + "X" * 9000, 2026)

    def test_orphaned_first_window_rejected(self):
        """Do not discard unlabelled windows preceding a date header."""

        with self.assertRaisesRegex(ValueError, "orphaned"):
            parse_manual_forecast_days("🪫00:00 - 01:00\n" + BOTH, 2026)

    def test_unrecognized_second_header_rejected(self):
        """A second schedule needs an explicitly recognized date label."""

        updated = TOMORROW.replace("Прогнозований графік", "Планувальні дані")
        with self.assertRaises(ValueError):
            parse_manual_forecast_days(TODAY + updated, 2026)


if __name__ == "__main__":
    unittest.main()
