"""Validate manually pasted forecasts without promoting them as facts."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date

from manual_dtek_forecast import (
    MANUAL_SOURCE,
    parse_manual_forecast,
    preview_manual_forecast,
)

DAY = date(2026, 11, 6)
MESSAGE = (
    "Прогнозований графік відключень на сьогодні, "
    "06.11 (П'ятниця), група 10.1:\n"
    "🪫00:00 - 05:30 (≈5.5 год.)\n"
    "🪫10:30 - 18:00 (≈7.5 год.)\n"
    "🪫20:00 - 24:00 (≈4 год.)\n"
)


class ManualDtekForecastTests(unittest.TestCase):
    """Treat an operator's pasted schedule as unverified forecast only."""

    def test_parse_exact_november_6_group_10_1(self):
        """Preserve all three user-supplied forecast windows."""

        forecast = parse_manual_forecast(MESSAGE, DAY)
        self.assertEqual(forecast.day, "2026-11-06")
        self.assertEqual(forecast.group, "10.1")
        self.assertEqual(
            forecast.periods,
            (
                ("00:00", "05:30"),
                ("10:30", "18:00"),
                ("20:00", "24:00"),
            ),
        )

    def test_never_claims_verified_source(self):
        """A successful manual parse never verifies official DTEK data."""

        forecast = parse_manual_forecast(MESSAGE, DAY)
        self.assertFalse(forecast.provider_verified)
        self.assertEqual(forecast.source, MANUAL_SOURCE)

    def test_ukrainian_preview_is_source_labeled(self):
        """Private preview is explicitly distinct from real measurements."""

        text = preview_manual_forecast(
            parse_manual_forecast(MESSAGE, DAY), "uk"
        )
        self.assertIn("введений вручну", text)
        self.assertIn("ДТЕК не перевірено", text)
        self.assertIn("Фактичне живлення невідоме", text)
        self.assertIn("20:00–24:00", text)

    def test_russian_and_english_are_unverified(self):
        """Keep the unverified disclaimer across supported locales."""

        forecast = parse_manual_forecast(MESSAGE, DAY)
        for language, required in (
            ("ru", "ДТЭК не проверен"),
            ("en", "DTEK not verified"),
        ):
            with self.subTest(language=language):
                text = preview_manual_forecast(forecast, language)
                self.assertIn(required, text)
                self.assertIn("10.1", text)

    def test_manual_source_cannot_be_promoted(self):
        """Reject a forged verified provider flag."""

        forecast = parse_manual_forecast(MESSAGE, DAY)
        with self.assertRaises(ValueError):
            preview_manual_forecast(replace(forecast, provider_verified=True))

    def test_wrong_metadata_source_is_rejected(self):
        """Never silently relabel an untrusted manual forecast."""

        forecast = parse_manual_forecast(MESSAGE, DAY)
        with self.assertRaises(ValueError):
            preview_manual_forecast(replace(forecast, source="official_dtek"))

    def test_wrong_calendar_day_rejected(self):
        """Outdated pasted schedules must not pass for a new day."""

        with self.assertRaisesRegex(ValueError, "date"):
            parse_manual_forecast(MESSAGE, date(2026, 11, 7))

    def test_header_without_group_rejected(self):
        """A forecast without a confirmed group is ambiguous."""

        with self.assertRaisesRegex(ValueError, "group"):
            parse_manual_forecast(MESSAGE.replace("група 10.1", "група"), DAY)

    def test_header_without_date_rejected(self):
        """A forecast cannot inherit the system clock accidentally."""

        with self.assertRaisesRegex(ValueError, "date"):
            parse_manual_forecast(MESSAGE.replace("06.11", "сегодня"), DAY)

    def test_overlapping_intervals_rejected(self):
        """An incorrect paste is not silently reinterpreted."""

        text = MESSAGE.replace("10:30 - 18:00", "05:00 - 18:00")
        with self.assertRaisesRegex(ValueError, "overlapping"):
            parse_manual_forecast(text, DAY)

    def test_reverse_intervals_rejected(self):
        """Reject an impossible day-local forecast range."""

        text = MESSAGE.replace("20:00 - 24:00", "21:00 - 20:00")
        with self.assertRaisesRegex(ValueError, "out-of-order"):
            parse_manual_forecast(text, DAY)

    def test_2400_cannot_start_window(self):
        """24:00 only denotes the end of an outage day."""

        text = MESSAGE.replace("20:00 - 24:00", "24:00 - 24:00")
        with self.assertRaises(ValueError):
            parse_manual_forecast(text, DAY)

    def test_invalid_half_hour_rejected(self):
        """Invalid clock granularity cannot be taken as a real schedule."""

        text = MESSAGE.replace("20:00 - 24:00", "20:15 - 24:00")
        with self.assertRaises(ValueError):
            parse_manual_forecast(text, DAY)

    def test_malformed_extra_outage_row_rejected(self):
        """Do not silently omit a bad row while retaining valid windows."""

        text = MESSAGE + "🪫невідомо - 23:00 (неверно)\n"
        with self.assertRaisesRegex(ValueError, "malformed"):
            parse_manual_forecast(text, DAY)

    def test_spring_dst_ambiguous_clock_rejected(self):
        """Reject local spring transition that cannot be interpreted safely."""

        text = "Прогноз 29.03, група 10.1:\n🪫03:00 - 04:00\n"
        with self.assertRaises(ValueError):
            parse_manual_forecast(text, date(2026, 3, 29))

    def test_oversized_message_rejected(self):
        """Bound untrusted message size before processing."""

        with self.assertRaises(ValueError):
            parse_manual_forecast(MESSAGE + "X" * 4100, DAY)

    def test_missing_windows_rejected(self):
        """Header alone is not a valid outage forecast."""

        with self.assertRaises(ValueError):
            parse_manual_forecast(MESSAGE.splitlines()[0], DAY)

    def test_ukrainian_label_does_not_assume_real_outage(self):
        """Do not say that Shelly is currently offline or mains is off."""

        text = preview_manual_forecast(
            parse_manual_forecast(MESSAGE, DAY), "uk"
        )
        self.assertNotIn("Світла немає", text)
        self.assertNotIn("Shelly offline", text)


if __name__ == "__main__":
    unittest.main()
