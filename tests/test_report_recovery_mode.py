"""Check that the startup pause cannot be bypassed by report preferences."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from test_app import APP, config


class ReportRecoveryTests(unittest.TestCase):
    """Exercise a paused report runtime with real disposable SQLite."""

    def setUp(self):
        """Preserve a pending report and an opted-in weekly stream."""
        scope = tempfile.TemporaryDirectory()
        self.addCleanup(scope.cleanup)
        self.monitor = APP.Monitor(
            config(
                database_path=Path(scope.name) / "history.sqlite3",
                scheduled_reports_mode="paused",
                telegram_token="fake-token",
                telegram_chat_id="fake-chat",
                telegram_admin_user_ids=("7",),
                daily_summary_hour=0,
                daily_summary_minute=0,
            )
        )
        self.monitor.storage.initialize()
        self.monitor.storage.set_meta("telegram_weekly_private_enabled_7", "1")
        self.monitor.storage.set_meta("daily_enabled", "1")
        self.pending = "telegram_report_pending:daily_summary_day"
        self.raw = "2026-11-26:" + "a" * 32
        self.monitor.storage.set_meta(self.pending, self.raw)
        self.monitor.telegram.send = Mock(return_value=42)

    def test_direct_delivery_does_not_reserve(self):
        """Even a direct scheduled delivery call cannot start another send."""
        self.assertFalse(
            self.monitor.deliver_scheduled_report(
                "telegram_weekly_private_sent_7", "2026-W48", "report", "7"
            )
        )
        self.assertIsNone(
            self.monitor.storage.get_meta(
                "telegram_report_pending:telegram_weekly_private_sent_7"
            )
        )
        self.monitor.telegram.send.assert_not_called()

    def test_daily_and_weekly_preferences_cannot_override_pause(self):
        """Keep normal preferences and pending evidence intact."""
        self.monitor.daily_summary_enabled = True
        self.monitor.maybe_daily_summary()
        self.monitor.maybe_weekly_private_reports(
            datetime(2026, 11, 29, 23, tzinfo=ZoneInfo("Europe/Kyiv"))
        )
        self.monitor.send_due_private_weekly("7", "2026-W48")
        self.monitor.telegram.send.assert_not_called()
        self.assertEqual(self.monitor.storage.get_meta(self.pending), self.raw)
        self.assertEqual(self.monitor.storage.get_meta("daily_enabled"), "1")
        self.assertEqual(
            self.monitor.storage.get_meta("telegram_weekly_private_enabled_7"),
            "1",
        )

    def test_paused_run_keeps_monitor_and_commands(self):
        """Do not spawn a report thread in recovery mode."""
        self.monitor.initialize = Mock()
        self.monitor.run_monitor_loop = Mock()
        self.monitor.command_loop = Mock()
        with patch.object(APP.threading, "Thread") as thread:
            self.monitor.run()
        thread.assert_called_once_with(
            target=self.monitor.command_loop,
            name="telegram-commands",
            daemon=True,
        )
        self.monitor.run_monitor_loop.assert_called_once_with()

    def test_invalid_environment_mode_refused(self):
        """A typo is not permission to start scheduled delivery."""
        with (
            patch.dict(
                os.environ,
                {
                    "SHELLY_HOST": "192.0.2.10",
                    "SCHEDULED_REPORTS_MODE": "pause",
                },
                clear=False,
            ),
            self.assertRaisesRegex(ValueError, "SCHEDULED_REPORTS_MODE"),
        ):
            APP.load_config()

    def test_explicit_paused_environment_mode(self):
        """The actual config loader recognizes the recovery setting."""
        with patch.dict(
            os.environ,
            {
                "SHELLY_HOST": "192.0.2.10",
                "SCHEDULED_REPORTS_MODE": "paused",
            },
            clear=False,
        ):
            self.assertEqual(
                APP.load_config().scheduled_reports_mode, "paused"
            )
