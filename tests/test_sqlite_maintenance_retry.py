"""Bound expensive SQLite maintenance retries during an outage."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class SQLiteMaintenanceRetryTests(unittest.TestCase):
    """Exercise maintenance scheduling without touching real storage."""

    def setUp(self):
        """Create a disposable monitor and fake maintenance operations."""

        scope = tempfile.TemporaryDirectory()
        self.addCleanup(scope.cleanup)
        self.monitor = APP.Monitor(
            config(database_path=Path(scope.name) / "qa.sqlite3")
        )
        self.monitor.storage.rollup_hourly = Mock(return_value=0)
        self.monitor.storage.database_maintenance = Mock(
            return_value={"pages": 10, "free_pages": 0, "vacuumed": False}
        )
        self.origin = 1800000000.0

    def tick(self, elapsed: float, monotonic: float) -> None:
        """Advance mocked process time and run a single maintenance cycle."""

        with (
            patch.object(APP.time, "time", return_value=self.origin + elapsed),
            patch.object(APP.time, "monotonic", return_value=monotonic),
        ):
            self.monitor.maybe_database_maintenance()

    def test_first_run_records_success_once(self):
        """Healthy maintenance preserves its existing daily cadence."""

        self.tick(0, 1000)
        self.assertEqual(self.monitor.storage.rollup_hourly.call_count, 1)
        self.assertEqual(
            self.monitor.storage.database_maintenance.call_count, 1
        )
        self.assertEqual(self.monitor.last_db_maintenance, self.origin)
        self.assertEqual(self.monitor.last_db_maintenance_attempt, 1000)

    def test_daily_cadence_ignores_unnecessary_repeat(self):
        """An intact database is not maintained on every polling cycle."""

        self.tick(0, 1000)
        for elapsed, monotonic in ((2, 1002), (400, 1400), (86399, 90000)):
            self.tick(elapsed, monotonic)
        self.assertEqual(self.monitor.storage.rollup_hourly.call_count, 1)

    def test_next_24_hour_maintenance_is_still_due(self):
        """The failure backoff never alters the normal once-a-day work."""

        self.tick(0, 1000)
        self.tick(86400, 90000)
        self.assertEqual(self.monitor.storage.rollup_hourly.call_count, 2)
        self.assertEqual(
            self.monitor.storage.rollup_hourly.call_args.args[0],
            self.origin + 86400 - 3600,
        )

    def test_rollup_failure_does_not_mark_success(self):
        """An error cannot falsely satisfy the daily completion gate."""

        self.monitor.storage.rollup_hourly.side_effect = OSError("no space")
        with self.assertRaises(OSError):
            self.tick(0, 1000)
        self.assertEqual(self.monitor.last_db_maintenance, 0)
        self.assertEqual(self.monitor.last_db_maintenance_attempt, 1000)

    def test_failed_rollup_retries_no_faster_than_300_seconds(self):
        """Repeated polls avoid expensive writes while storage is failing."""

        self.monitor.storage.rollup_hourly.side_effect = OSError("no space")
        with self.assertRaises(OSError):
            self.tick(0, 1000)
        for elapsed, monotonic in ((1, 1001), (20, 1020), (299, 1299)):
            self.tick(elapsed, monotonic)
        self.assertEqual(self.monitor.storage.rollup_hourly.call_count, 1)

    def test_maintenance_failure_throttles_and_recovers(self):
        """Checkpoint errors are throttled; a later pass marks completion."""

        self.monitor.storage.database_maintenance.side_effect = [
            OSError("busy"),
            {"pages": 10, "free_pages": 0},
        ]
        with self.assertRaises(OSError):
            self.tick(0, 1000)
        self.tick(299, 1299)
        self.assertEqual(
            self.monitor.storage.database_maintenance.call_count, 1
        )
        self.tick(300, 1300)
        self.assertEqual(
            self.monitor.storage.database_maintenance.call_count, 2
        )
        self.assertEqual(self.monitor.last_db_maintenance, self.origin + 300)

    def test_failed_attempt_does_not_block_later_daily_run(self):
        """A recovered database continues normal scheduled maintenance."""

        self.monitor.storage.rollup_hourly.side_effect = [
            OSError("busy"),
            0,
            0,
        ]
        with self.assertRaises(OSError):
            self.tick(0, 1000)
        self.tick(300, 1300)
        self.tick(86400 + 300, 90000)
        self.assertEqual(self.monitor.storage.rollup_hourly.call_count, 3)


if __name__ == "__main__":
    unittest.main()
