"""Deterministic timing checks for Shelly and DTEK schedule comparison."""

from __future__ import annotations

import math
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import dtek_correlation as correlation

KYIV = ZoneInfo("Europe/Kyiv")
DAY = "2026-11-06"
WINDOWS = [("14:00", "18:00")]


def epoch(clock: str, day: str = DAY) -> float:
    """Construct a real Europe/Kyiv timestamp without host TZ ambiguity."""

    return (
        datetime.fromisoformat(f"{day}T{clock}")
        .replace(tzinfo=KYIV)
        .timestamp()
    )


class CorrelationTests(unittest.TestCase):
    """Validate fail-closed semantics without devices or HTTP requests."""

    def result(self, start: str, stop: str, windows=None):
        """Run the standard one-day comparison with fixed clocks."""

        return correlation.correlate_outage(
            epoch(start),
            epoch(stop),
            DAY,
            WINDOWS if windows is None else windows,
        )

    def test_outage_starts_inside_window(self):
        match = self.result("14:30", "15:00")
        self.assertEqual(match.status, "start_within_window")
        self.assertEqual(match.matching_window, WINDOWS[0])
        self.assertEqual(match.overlap_seconds, 1800)
        self.assertFalse(match.cause_verified)

    def test_outage_starts_at_window_open(self):
        self.assertEqual(
            self.result("14:00", "14:30").status,
            "start_within_window",
        )

    def test_outage_starts_at_window_close(self):
        self.assertEqual(
            self.result("18:00", "18:30").status,
            "outside_published_windows",
        )

    def test_early_start_partially_overlaps(self):
        match = self.result("13:50", "14:10")
        self.assertEqual(match.status, "partial_overlap")
        self.assertEqual(match.overlap_seconds, 600)
        self.assertFalse(match.cause_verified)

    def test_early_start_ends_at_boundary(self):
        self.assertEqual(
            self.result("13:45", "14:00").status,
            "outside_published_windows",
        )

    def test_no_overlap_after_schedule(self):
        self.assertEqual(
            self.result("19:00", "20:00").status,
            "outside_published_windows",
        )

    def test_explicit_empty_plan_means_outside(self):
        self.assertEqual(
            self.result("15:00", "16:00", []).status,
            "outside_published_windows",
        )

    def test_missing_schedule_is_unknown(self):
        self.assertEqual(
            correlation.correlate_outage(
                epoch("15:00"), epoch("16:00"), DAY, None
            ).status,
            "unknown",
        )

    def test_wrong_calendar_day_is_unknown(self):
        self.assertEqual(
            correlation.correlate_outage(
                epoch("15:00"), epoch("16:00"), "2026-11-05", WINDOWS
            ).status,
            "unknown",
        )

    def test_bad_date_is_unknown(self):
        self.assertEqual(
            correlation.correlate_outage(
                epoch("15:00"), epoch("16:00"), "bad", WINDOWS
            ).status,
            "unknown",
        )

    def test_open_event_without_boundary_unknown(self):
        self.assertEqual(
            correlation.correlate_outage(
                epoch("15:00"), None, DAY, WINDOWS
            ).status,
            "unknown",
        )

    def test_open_event_with_observation_boundary(self):
        match = correlation.correlate_outage(
            epoch("15:00"),
            None,
            DAY,
            WINDOWS,
            observed_until=epoch("16:00"),
        )
        self.assertEqual(match.status, "start_within_window")
        self.assertEqual(match.overlap_seconds, 3600)

    def test_open_event_invalid_observation_end(self):
        match = correlation.correlate_outage(
            epoch("15:00"),
            None,
            DAY,
            WINDOWS,
            observed_until=epoch("14:00"),
        )
        self.assertEqual(match.status, "unknown")

    def test_event_end_precedes_start(self):
        self.assertEqual(self.result("15:00", "14:00").status, "unknown")

    def test_nan_start_is_unknown(self):
        self.assertEqual(
            correlation.correlate_outage(
                math.nan, epoch("16:00"), DAY, WINDOWS
            ).status,
            "unknown",
        )

    def test_infinite_end_is_unknown(self):
        self.assertEqual(
            correlation.correlate_outage(
                epoch("15:00"), math.inf, DAY, WINDOWS
            ).status,
            "unknown",
        )

    def test_bad_hours_unknown(self):
        self.assertEqual(
            self.result("15:00", "16:00", [("25:00", "26:00")]).status,
            "unknown",
        )

    def test_reversed_window_unknown(self):
        self.assertEqual(
            self.result("15:00", "16:00", [("18:00", "14:00")]).status,
            "unknown",
        )

    def test_overlapping_windows_unknown(self):
        invalid = [("14:00", "16:00"), ("15:30", "18:00")]
        self.assertEqual(
            self.result("15:00", "16:00", invalid).status,
            "unknown",
        )

    def test_unsorted_windows_unknown(self):
        invalid = [("19:00", "20:00"), ("14:00", "16:00")]
        self.assertEqual(
            self.result("15:00", "16:00", invalid).status,
            "unknown",
        )

    def test_midnight_end_is_next_day(self):
        match = self.result("23:45", "23:59", [("23:30", "24:00")])
        self.assertEqual(match.status, "start_within_window")
        self.assertEqual(match.overlap_seconds, 840)

    def test_midnight_crossing_observation(self):
        match = correlation.correlate_outage(
            epoch("23:45"),
            epoch("00:10", "2026-11-07"),
            DAY,
            [("23:30", "24:00")],
        )
        self.assertEqual(match.status, "start_within_window")
        self.assertEqual(match.overlap_seconds, 900)

    def test_fall_dst_ambiguous_hour_is_unknown(self):
        start = epoch("03:15", "2026-10-25")
        end = start + 1200
        match = correlation.correlate_outage(
            start, end, "2026-10-25", [("03:00", "04:00")]
        )
        self.assertEqual(match.status, "unknown")

    def test_spring_dst_missing_hour_is_unknown(self):
        start = epoch("04:15", "2026-03-29")
        match = correlation.correlate_outage(
            start, start + 60, "2026-03-29", [("03:00", "05:00")]
        )
        self.assertEqual(match.status, "unknown")

    def test_more_than_48_windows_is_unknown(self):
        invalid = [("00:00", "00:30")] * 49
        self.assertEqual(
            self.result("15:00", "16:00", invalid).status,
            "unknown",
        )


if __name__ == "__main__":
    unittest.main()
