"""Realistic Unix-midnight day-key cases for the DTEK cache adapter."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dtek_schedule as DTEK

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 8, 1, 0, tzinfo=KYIV)


def unix_fact(day: datetime) -> dict:
    """Construct the day format observed in the community mirror."""

    local = day.astimezone(KYIV)
    midnight = datetime(local.year, local.month, local.day, tzinfo=KYIV)
    stamp = int(midnight.timestamp())
    hours = {str(i): "yes" for i in range(1, 25)}
    hours["1"] = "no"
    hours["2"] = "first"
    return {"today": stamp, "data": {str(stamp): {"GPV10.1": hours}}}


class DtekUnixDayTests(unittest.TestCase):
    """Accept valid DTEK time keys without weakening outage safety."""

    def test_real_unix_day_key_is_accepted(self):
        """The mirror's midnight timestamp is valid for Kyiv today."""

        fact = unix_fact(NOW)
        groups = DTEK.published_groups(fact, NOW)
        self.assertEqual(
            DTEK.outage_periods(groups["GPV10.1"]),
            [("00:00", "01:30")],
        )

    def test_today_timestamp_can_be_numeric_string(self):
        """Support JSON numeric and string forms without timezone guessing."""

        fact = unix_fact(NOW)
        fact["today"] = str(fact["today"])
        self.assertIn("GPV10.1", DTEK.published_groups(fact, NOW))

    def test_old_date_is_not_todays_schedule(self):
        """Preserved yesterday's data must not display as current."""

        fact = unix_fact(NOW - timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "source date"):
            DTEK.published_groups(fact, NOW)

    def test_timestamp_not_midnight_is_rejected(self):
        """Reject arbitrary timestamps rather than treating hours as days."""

        fact = unix_fact(NOW)
        fact["today"] += 3600
        with self.assertRaisesRegex(ValueError, "midnight"):
            DTEK.published_groups(fact, NOW)

    def test_duplicate_forms_for_same_day_are_rejected(self):
        """Two conflicting records for today cannot be silently selected."""

        fact = unix_fact(NOW)
        groups = next(iter(fact["data"].values()))
        fact["data"][NOW.date().isoformat()] = groups
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            DTEK.published_groups(fact, NOW)

    def test_numeric_booleans_and_bad_format_are_rejected(self):
        """Refuse ambiguous Unix milliseconds or boolean pseudo-dates."""

        for value in (True, False, 1794088800000, "1794088800000", 3.14):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DTEK.parse_source_day(value)

    def test_dst_midnight_is_correctly_localized(self):
        """Kyiv's DST transitions must not produce the wrong calendar day."""

        for day in (
            datetime(2026, 3, 29, 12, tzinfo=KYIV),
            datetime(2026, 10, 25, 12, tzinfo=KYIV),
        ):
            with self.subTest(day=day.date()):
                fact = unix_fact(day)
                self.assertIn("GPV10.1", DTEK.published_groups(fact, day))

    def test_uncertain_outage_codes_still_fail_closed(self):
        """Possible outages cannot be reported as certain power loss."""

        hours = DTEK.published_groups(unix_fact(NOW), NOW)["GPV10.1"]
        hours["1"] = "maybe"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            DTEK.outage_periods(hours)


if __name__ == "__main__":
    unittest.main()
