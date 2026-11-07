"""Prevent artificial Shelly connectivity changes at local midnight."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from test_app import APP

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/soak-report.py"
SPEC = importlib.util.spec_from_file_location("midnight_soak", SCRIPT)
SOAK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SOAK)


def when(value: str) -> float:
    """Use explicit Kyiv offsets for each natural-style event."""

    return datetime.fromisoformat(value).timestamp()


class MidnightConnectivityStatisticsTests(unittest.TestCase):
    """Keep a continuous Shelly outage as one network gap."""

    def setUp(self):
        """Create disposable SQLite and one open incident across midnight."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "shelly.sqlite3"
        self.storage = APP.Storage(self.path, "Europe/Kyiv")
        self.storage.initialize()
        self.started = when("2026-11-06T20:31:07+02:00")
        self.midnight = when("2026-11-07T00:00:00+02:00")
        self.assertTrue(self.storage.begin_outage(self.started))

    def test_one_event_remains_open_on_next_day(self):
        """A scheduled midnight must not split a natural connectivity gap."""

        after = self.storage.outage_stats(self.midnight + 60)
        self.assertEqual(after["count"], 1)
        self.assertEqual(after["duration"], 60.0)
        self.assertEqual(self.storage.outage_total(), 1)
        self.assertEqual(self.storage.open_outage_started(), self.started)

    def test_last_second_of_november_six(self):
        """The previous day's interval counts up to the observation time."""

        now = self.midnight - 1
        stats = self.storage.outage_stats(now)
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["duration"], now - self.started)

    def test_exact_midnight_does_not_invent_an_elapsed_interval(self):
        """A zero-second boundary is not yet a current-day interval."""

        exact = self.storage.outage_stats(self.midnight)
        self.assertEqual(exact["count"], 0)
        self.assertEqual(exact["duration"], 0.0)
        next_second = self.storage.outage_stats(self.midnight + 1)
        self.assertEqual(next_second["count"], 1)
        self.assertEqual(next_second["duration"], 1.0)
        self.assertEqual(self.storage.outage_total(), 1)

    def test_next_day_only_counts_elapsed_seconds(self):
        """Reported daily duration must not include yesterday's gap time."""

        now = when("2026-11-07T02:30:00+02:00")
        stats = self.storage.outage_stats(now)
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["duration"], 9000.0)

    def test_future_closed_timestamp_cannot_inflate_duration(self):
        """A bad future end time must not count seconds yet to occur."""

        future = when("2026-11-07T00:30:00+02:00")
        now = when("2026-11-07T00:10:00+02:00")
        self.assertIsNotNone(self.storage.end_outage(future))
        stats = self.storage.outage_stats(now)
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["duration"], 600.0)

    def test_future_start_is_not_counted_as_current_event(self):
        """Do not count network loss that starts later than report time."""

        future = when("2026-11-07T00:30:00+02:00")
        self.assertIsNotNone(self.storage.end_outage(self.midnight))
        self.assertTrue(self.storage.begin_outage(future))
        now = when("2026-11-07T00:10:00+02:00")
        stats = self.storage.outage_stats(now)
        self.assertEqual(stats["count"], 0)
        self.assertEqual(stats["duration"], 0.0)

    def test_soak_report_uses_one_open_gap_crossing_midnight(self):
        """A one-hour report must match the continuous SQLite event."""

        now = when("2026-11-07T00:10:00+02:00")
        options = SimpleNamespace(
            db=str(self.path),
            hours=1.0,
            expected_interval=10.0,
            timezone="Europe/Kyiv",
            json=True,
        )
        report = SOAK.build_report(options, now=now)
        self.assertEqual(report["connectivity_losses"], 1)
        self.assertEqual(report["open_connectivity_losses"], 1)
        self.assertEqual(report["connectivity_loss_seconds"], 3600.0)
        self.assertIn(
            "not proof of mains", report["connectivity_loss_semantics"]
        )

    def test_midnight_queries_do_not_close_stored_gap(self):
        """Daily report is read-only and never fabricates a recovery."""

        for now in (self.midnight - 1, self.midnight, self.midnight + 1):
            self.storage.outage_stats(now)
        self.assertEqual(self.storage.outage_total(), 1)
        self.assertEqual(self.storage.open_outage_started(), self.started)


if __name__ == "__main__":
    unittest.main()
