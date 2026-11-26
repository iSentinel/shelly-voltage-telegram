"""Daily Telegram retries must not overwhelm API or block SQLite jobs."""

from __future__ import annotations

import tempfile
import threading
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

        self.monitor.telegram.send.side_effect = APP.ScheduledReportRejected(
            "offline"
        )
        with self.assertRaisesRegex(RuntimeError, "offline"):
            self.attempt(1000.0)
        self.assertIsNone(self.monitor.last_summary_day)
        self.assertIsNone(self.monitor.storage.get_meta("daily_summary_day"))

    def test_failed_send_is_throttled_before_five_minutes(self):
        """An outage must not cause one API attempt per poll."""

        self.monitor.telegram.send.side_effect = APP.ScheduledReportRejected(
            "offline"
        )
        with self.assertRaises(RuntimeError):
            self.attempt(1000.0)
        for timestamp in (1000.1, 1001.0, 1100.0, 1299.9):
            self.attempt(timestamp)
        self.assertEqual(self.monitor.telegram.send.call_count, 1)

    def test_retry_after_five_minutes_records_acknowledgement(self):
        """A later accepted API response may mark the summary date."""

        self.monitor.telegram.send.side_effect = [
            APP.ScheduledReportRejected("network offline"),
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

    def test_ambiguous_send_survives_monitor_restart(self):
        """A lost acknowledgement must block retry, even after restart."""
        self.monitor.telegram.send.side_effect = RuntimeError("lost ACK")
        with self.assertRaisesRegex(RuntimeError, "lost ACK"):
            self.attempt(1000.0)
        pending = "telegram_report_pending:daily_summary_day"
        self.assertIsNotNone(self.monitor.storage.get_meta(pending))
        self.assertIsNone(self.monitor.storage.get_meta("daily_summary_day"))
        restarted = APP.Monitor(self.monitor.cfg)
        restarted.telegram.send = Mock(return_value=123)
        with patch.object(APP.time, "monotonic", return_value=2000.0):
            restarted.maybe_daily_summary()
        restarted.telegram.send.assert_not_called()
        self.assertIsNotNone(restarted.storage.get_meta(pending))

    def test_explicit_rejection_clears_pending(self):
        """A proven rejection may be retried after the existing throttle."""
        self.monitor.telegram.send.side_effect = APP.ScheduledReportRejected(
            "explicit rejection"
        )
        with self.assertRaises(APP.ScheduledReportRejected):
            self.attempt(1000.0)
        self.assertIsNone(
            self.monitor.storage.get_meta(
                "telegram_report_pending:daily_summary_day"
            )
        )

    def test_finish_requires_exact_period(self):
        """A wrong acknowledgement cannot clear another report intent."""
        storage = self.monitor.storage
        attempt = storage.reserve_report("daily_summary_day", "day1")
        self.assertIsNotNone(attempt)
        with self.assertRaisesRegex(ValueError, "intent changed"):
            storage.finish_report(
                "daily_summary_day", "day2", attempt, accepted=True
            )
        self.assertIsNone(storage.reserve_report("daily_summary_day", "day2"))
        self.assertIsNone(storage.get_meta("daily_summary_day"))
        storage.finish_report(
            "daily_summary_day", "day1", attempt, accepted=True
        )
        self.assertEqual(storage.get_meta("daily_summary_day"), "day1")
        self.assertFalse(storage.reserve_report("daily_summary_day", "day1"))

    def test_stale_attempt_cannot_resolve_same_period(self):
        """A rejected reservation cannot settle a later attempt."""
        storage = self.monitor.storage
        key, period = "daily_summary_day", "2026-11-26"
        first = storage.reserve_report(key, period)
        storage.finish_report(key, period, first, accepted=False)
        second = storage.reserve_report(key, period)
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(ValueError, "intent changed"):
            storage.finish_report(key, period, first, accepted=True)
        self.assertEqual(
            storage.get_meta(f"telegram_report_pending:{key}"),
            f"{period}:{second}",
        )
        self.assertIsNone(storage.get_meta(key))
        storage.finish_report(key, period, second, accepted=True)
        self.assertEqual(storage.get_meta(key), period)

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
        stopped = threading.Event()
        wakeup = threading.Event()
        wakeup.set()
        self.monitor.maybe_weekly_private_reports = Mock(
            side_effect=stopped.set
        )
        self.monitor.maybe_database_maintenance = Mock()
        with (
            patch.object(APP, "STOP", threading.Event()),
            patch.object(APP.logger, "warning") as warning,
        ):
            self.monitor.report_loop(wakeup, stopped)
            self.monitor.run_periodic_tasks()
        self.monitor.maybe_weekly_private_reports.assert_called_once()
        self.monitor.maybe_database_maintenance.assert_called_once()
        self.assertEqual(warning.call_count, 1)
        self.assertNotIn(
            "test-token-must-stay-private", str(warning.call_args)
        )

    def test_database_failure_does_not_block_status_cleanup(self):
        """A failed SQLite maintenance task must leave cleanup scheduled."""

        self.monitor.maybe_database_maintenance = Mock(
            side_effect=RuntimeError
        )
        self.monitor.maybe_cleanup_status = Mock()
        with patch.object(APP.logger, "warning"):
            self.monitor.run_periodic_tasks()
        self.monitor.maybe_cleanup_status.assert_called_once()

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
            self.monitor.maybe_daily_summary()
            self.monitor.run_periodic_tasks()
        self.monitor.telegram.send.assert_called_once()
        self.assertEqual(
            self.monitor.storage.get_meta("daily_summary_day"),
            self.monitor.last_summary_day,
        )


class ScheduledReportIsolationTests(unittest.TestCase):
    """Exercise sampling and real Telegram routing without network calls."""

    def setUp(self):
        """Build a monitor with test SQLite and fixed Shelly input."""
        scope = tempfile.TemporaryDirectory()
        self.addCleanup(scope.cleanup)
        self.monitor = APP.Monitor(
            config(
                database_path=Path(scope.name) / "isolated.sqlite3",
                poll_interval=0.01,
                telegram_token="fake-token",
                telegram_chat_id="fake-chat",
                daily_summary_hour=0,
                daily_summary_minute=0,
            )
        )
        self.monitor.storage.initialize()
        self.monitor.initialize = Mock()
        self.monitor.command_loop = Mock()
        self.monitor.update_status = Mock()
        self.monitor.run_periodic_tasks = Mock()
        self.monitor.maybe_weekly_private_reports = Mock()
        self.monitor.shelly.read = Mock(return_value=(230.0, 50.0, 1.0))
        self.report_started = threading.Event()
        self.release_report = threading.Event()
        self.sampled = threading.Event()
        self.stop = threading.Event()
        self.finished = threading.Event()
        stop_patch = patch.object(APP, "STOP", self.stop)
        stop_patch.start()
        self.addCleanup(stop_patch.stop)
        self.monitor.telegram.report_session.post = Mock(
            side_effect=self.hold_report
        )
        self.monitor.telegram.api_session.post = Mock(
            return_value=self.accepted()
        )
        self.monitor.poll_cycle = Mock(side_effect=self.record_sample)

    @staticmethod
    def accepted():
        """Return an accepted Telegram response without contacting the API."""
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 42}}
        return response

    def hold_report(self, *_args, **_kwargs):
        """Block the scheduled HTTP request until the test releases it."""
        self.report_started.set()
        if not self.release_report.wait(6):
            raise TimeoutError("test did not release the report")
        return self.accepted()

    def record_sample(self):
        """Persist a real monitor sample while the report is pending."""
        result = APP.Monitor.poll_cycle(self.monitor)
        if self.report_started.is_set():
            self.sampled.set()
        return result

    def run_monitor(self):
        """Signal when the monitor and its report worker have shut down."""
        self.monitor.run()
        self.finished.set()

    def test_shutdown_interrupts_scheduled_rate_limit_retry(self):
        """A stopped report must not resend or persist an unaccepted day."""
        rate_limited = Mock()
        rate_limited.json.return_value = {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 30},
        }

        def reject(*_args, **_kwargs):
            """Simulate shutdown when the first request gets a 429."""
            self.stop.set()
            return rate_limited

        self.monitor.last_summary_attempt = -1000.0
        self.monitor.telegram.report_session.post = Mock(side_effect=reject)
        with self.assertRaisesRegex(RuntimeError, "Scheduled report stopped"):
            self.monitor.maybe_daily_summary()
        self.monitor.telegram.report_session.post.assert_called_once()
        self.assertIsNone(self.monitor.storage.get_meta("daily_summary_day"))

    def test_shutdown_prevents_new_scheduled_send(self):
        """Do not begin a scheduled request after STOP is set."""
        self.stop.set()
        with self.assertRaisesRegex(RuntimeError, "Scheduled report stopped"):
            self.monitor.telegram.send("scheduled", scheduled=True)
        self.monitor.telegram.report_session.post.assert_not_called()

    def test_slow_daily_report_leaves_sampling_and_alert_transport_available(
        self,
    ):
        """Continue work during the send and retain ownership at shutdown."""
        runner = threading.Thread(target=self.run_monitor)
        alert_done = threading.Event()

        def send_alert():
            """Use the ordinary transport while the scheduled send waits."""
            self.monitor.telegram.send("critical alert")
            alert_done.set()

        alert = threading.Thread(target=send_alert)
        runner.start()
        try:
            self.assertTrue(self.report_started.wait(3))
            self.assertTrue(self.sampled.wait(3))
            self.assertIsNotNone(self.monitor.storage.latest("L1"))
            alert.start()
            self.assertTrue(alert_done.wait(3))
            self.stop.set()
            self.assertFalse(self.finished.wait(0.1))
        finally:
            self.stop.set()
            self.release_report.set()
            runner.join(timeout=6)
            if alert.ident is not None:
                alert.join(timeout=6)
        self.assertTrue(self.finished.is_set())
        self.assertIsNotNone(
            self.monitor.storage.get_meta("daily_summary_day")
        )
        self.monitor.telegram.report_session.post.assert_called_once()
        self.monitor.telegram.api_session.post.assert_called_once()
        self.monitor.maybe_weekly_private_reports.assert_not_called()


if __name__ == "__main__":
    unittest.main()
