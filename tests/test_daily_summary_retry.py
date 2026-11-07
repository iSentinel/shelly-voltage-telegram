"""Daily Telegram retries must not overwhelm API or block SQLite jobs."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class DailySummaryRetryTests(unittest.TestCase):
    """Observe only mocked sends and isolated SQLite metadata."""

    def setUp(self):
        """Create a disposable bot, never a real Telegram connection."""

        scope = tempfile.TemporaryDirectory()
        self.addCleanup(scope.cleanup)
        self.monitor = APP.Monitor(
            config(
                database_path=Path(scope.name) / "retry.sqlite3",
                telegram_token="fake-token",
                telegram_chat_id="fake-id",
                daily_summary_hour=0,
                daily_summary_minute=0,
            )
        )
        self.monitor.storage.initialize()
        self.monitor.telegram.send = Mock(return_value=101)

    def attempt(self, monotonic_time: float):
        """Drive one daily retry at a controlled monotonic timestamp."""

        with patch.object(APP.time, "monotonic", return_value=monotonic_time):
            self.monitor.maybe_daily_summary()

    def test_success_persists_day_and_prevents_repeat(self):
        """Once accepted, further polling must not resend today."""

        self.attempt(1000.0)
        self.attempt(1301.0)
        self.assertEqual(self.monitor.telegram.send.call_count, 1)
        self.assertEqual(
            self.monitor.storage.get_meta("daily_summary_day"),
            self.monitor.last_summary_day,
        )

    def test_first_failure_does_not_mark_sent(self):
        """Transport failures must never create a successful day record."""

        self.monitor.telegram.send.side_effect = RuntimeError("offline")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.attempt(1000.0)
        self.assertIsNone(self.monitor.last_summary_day)
        self.assertIsNone(self.monitor.storage.get_meta("daily_summary_day"))

    def test_failed_send_is_throttled_before_five_minutes(self):
        """An outage must not cause one API attempt per poll."""

        self.monitor.telegram.send.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            self.attempt(1000.0)
        for timestamp in (1000.1, 1001.0, 1100.0, 1299.9):
            self.attempt(timestamp)
        self.assertEqual(self.monitor.telegram.send.call_count, 1)

    def test_retry_after_five_minutes_records_acknowledgement(self):
        """A later accepted API response may mark the summary date."""

        self.monitor.telegram.send.side_effect = [
            RuntimeError("network offline"),
            110,
        ]
        with self.assertRaises(RuntimeError):
            self.attempt(1000.0)
        self.attempt(1299.0)
        self.assertEqual(self.monitor.telegram.send.call_count, 1)
        self.attempt(1300.0)
        self.assertEqual(self.monitor.telegram.send.call_count, 2)
        self.assertIsNotNone(
            self.monitor.storage.get_meta("daily_summary_day")
        )

    def test_no_daily_summary_when_disabled(self):
        """Disabled options cannot start retries or deliveries."""

        self.monitor.daily_summary_enabled = False
        self.attempt(1000.0)
        self.assertIsNone(self.monitor.storage.get_meta("daily_summary_day"))
        self.monitor.telegram.send.assert_not_called()

    def test_failed_daily_send_does_not_block_weekly_or_database(self):
        """Independent jobs run even if a daily Telegram call fails."""

        self.monitor.maybe_daily_summary = Mock(
            side_effect=RuntimeError("test-token-must-stay-private")
        )
        self.monitor.maybe_weekly_private_reports = Mock()
        self.monitor.maybe_database_maintenance = Mock()
        with patch.object(APP.logger, "warning") as warning:
            self.monitor.run_periodic_tasks()
        self.monitor.maybe_weekly_private_reports.assert_called_once()
        self.monitor.maybe_database_maintenance.assert_called_once()
        self.assertEqual(warning.call_count, 1)
        self.assertNotIn(
            "test-token-must-stay-private", str(warning.call_args)
        )

    def test_weekly_failure_does_not_block_sqlite(self):
        """Weekly Telegram exceptions cannot starve SQLite maintenance."""

        self.monitor.maybe_daily_summary = Mock()
        self.monitor.maybe_weekly_private_reports = Mock(
            side_effect=RuntimeError("unreachable")
        )
        self.monitor.maybe_database_maintenance = Mock()
        with patch.object(APP.logger, "warning"):
            self.monitor.run_periodic_tasks()
        self.monitor.maybe_daily_summary.assert_called_once()
        self.monitor.maybe_database_maintenance.assert_called_once()

    def test_database_failure_cannot_undo_daily_delivery(self):
        """Later maintenance errors do not roll back a sent date."""

        self.monitor.maybe_weekly_private_reports = Mock()
        self.monitor.maybe_database_maintenance = Mock(
            side_effect=RuntimeError("sqlite unavailable")
        )
        with (
            patch.object(APP.time, "monotonic", return_value=1000.0),
            patch.object(APP.logger, "warning"),
        ):
            self.monitor.run_periodic_tasks()
        self.monitor.telegram.send.assert_called_once()
        self.assertEqual(
            self.monitor.storage.get_meta("daily_summary_day"),
            self.monitor.last_summary_day,
        )


if __name__ == "__main__":
    unittest.main()
