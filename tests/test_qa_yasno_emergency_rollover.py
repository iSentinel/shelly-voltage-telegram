"""Exercise the API date lag during emergency and scheduled mode changes."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

from yasno_schedule import (
    YasnoDay,
    YasnoEmergency,
    parse_provider_state,
    parse_schedule,
)

KYIV = ZoneInfo("Europe/Kyiv")
BEFORE = datetime(2026, 11, 20, 23, 45, tzinfo=KYIV)
AFTER = datetime(2026, 11, 21, 0, 1, tzinfo=KYIV)


def scenario(prior: str, following: str, before: datetime = BEFORE) -> dict:
    """Produce known provider states using the two adjacent dates."""
    data = response(before)
    group = data["10.1"]
    plan = group["today"]["slots"]
    for day, status in (("today", prior), ("tomorrow", following)):
        group[day]["status"] = status
        group[day]["slots"] = (
            [dict(slot) for slot in plan]
            if status == "ScheduleApplies"
            else []
        )
    return data


class YasnoEmergencyRolloverTests(unittest.TestCase):
    """Use the correct Kyiv date regardless of the provider key names."""

    def test_plan_to_emergency_midnight(self):
        """A verified next-day emergency cannot become a green schedule."""
        data = scenario("ScheduleApplies", "EmergencyShutdowns")
        result = parse_provider_state(data, "10.1", AFTER, AFTER)
        self.assertIsInstance(result, YasnoEmergency)
        self.assertEqual(result.day, "2026-11-21")
        with self.assertRaisesRegex(ValueError, "not published"):
            parse_schedule(data, "10.1", AFTER, AFTER)

    def test_emergency_for_both_adjacent_days(self):
        """An active next-day emergency remains visible after midnight."""
        data = scenario("EmergencyShutdowns", "EmergencyShutdowns")
        result = parse_provider_state(data, "10.1", AFTER, AFTER)
        self.assertIsInstance(result, YasnoEmergency)
        self.assertEqual(result.day, "2026-11-21")

    def test_emergency_to_explicit_next_day_plan(self):
        """A verified new-day plan may follow an earlier emergency."""
        data = scenario("EmergencyShutdowns", "ScheduleApplies")
        result = parse_provider_state(data, "10.1", AFTER, AFTER)
        self.assertIsInstance(result, YasnoDay)
        self.assertEqual(result.day, "2026-11-21")
        self.assertEqual(len(result.bars), 48)
        self.assertEqual(parse_schedule(data, "10.1", AFTER, AFTER), result)

    def test_unpublished_next_day_cannot_reuse_emergency(self):
        """A notice from yesterday is not proof of today's provider state."""
        data = scenario("EmergencyShutdowns", "WaitingForSchedule")
        with self.assertRaisesRegex(ValueError, "not published"):
            parse_provider_state(data, "10.1", AFTER, AFTER)

    def test_nonadjacent_or_malformed_yesterday_is_rejected(self):
        """Don't trust a next-day key after an invalid previous record."""
        for bad in ("date", "slots", "status"):
            with self.subTest(bad=bad):
                data = scenario("EmergencyShutdowns", "EmergencyShutdowns")
                today = data["10.1"]["today"]
                if bad == "date":
                    today["date"] = (BEFORE - timedelta(days=1)).isoformat()
                elif bad == "slots":
                    today["slots"] = [{"start": 0, "end": 1440}]
                else:
                    today["status"] = "Unexpected"
                with self.assertRaises(ValueError):
                    parse_provider_state(data, "10.1", AFTER, AFTER)

    def test_dst_calendar_rollovers(self):
        """Emergency records use civil Kyiv dates on DST change weekends."""
        for month, day in ((3, 29), (10, 25)):
            with self.subTest(month=month, day=day):
                after = datetime(2026, month, day, 0, 1, tzinfo=KYIV)
                before = after - timedelta(minutes=16)
                data = scenario(
                    "EmergencyShutdowns", "EmergencyShutdowns", before
                )
                result = parse_provider_state(data, "10.1", after, after)
                self.assertEqual(result.day, after.date().isoformat())


if __name__ == "__main__":
    unittest.main()
