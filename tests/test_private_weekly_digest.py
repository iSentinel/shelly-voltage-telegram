"""Private weekly reports are opt-in, isolated and week-deduplicated."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from test_app import APP, config


def make_monitor(path: Path) -> APP.Monitor:
    """Create an isolated monitor with two allowed administrators."""

    monitor = APP.Monitor(
        config(
            database_path=path,
            telegram_token="test-token",
            telegram_chat_id="123",
            telegram_admin_user_ids=("7", "8"),
            telegram_allowed_user_ids=("7", "8"),
            telegram_language="uk",
        )
    )
    monitor.storage.initialize()
    monitor.telegram.send = Mock(return_value=42)
    monitor.telegram.edit = Mock()
    monitor.telegram.pin = Mock()
    return monitor


def update(user: int, command: str, chat_type: str = "private") -> dict:
    """Create an incoming command without Telegram network access."""

    return {
        "message": {
            "chat": {
                "type": chat_type,
                "id": user if chat_type == "private" else 123,
            },
            "from": {"id": user},
            "text": command,
        }
    }


def sunday(hour: int = 20, minute: int = 1) -> datetime:
    """Return one stable Europe/Kyiv Sunday for schedule tests."""

    return datetime(2026, 11, 8, hour, minute, tzinfo=ZoneInfo("Europe/Kyiv"))


class PrivateWeeklyDigestTests(unittest.TestCase):
    """Validate opt-in, scheduling, localization and channel isolation."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "weekly.sqlite3"
        self.monitor = make_monitor(self.path)
        self.monitor.weekly_summary_text = Mock(return_value="weekly report")

    def subscribe(self, user: int = 7) -> None:
        """Enable exactly one administrator's private digest."""

        self.monitor.handle_update(update(user, "/week subscribe"))
        self.monitor.telegram.send.reset_mock()

    def test_default_does_not_schedule(self):
        """An absent subscription can never trigger an unsolicited send."""

        self.monitor.maybe_weekly_private_reports(sunday())
        self.monitor.telegram.send.assert_not_called()

    def test_subscribed_admin_gets_one_silent_report(self):
        """The scheduler must send only to an opted-in private chat."""

        self.subscribe()
        self.monitor.maybe_weekly_private_reports(sunday())
        call = self.monitor.telegram.send.call_args
        self.assertEqual(call.args[0], "weekly report")
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertIs(call.kwargs["notify"], False)
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()

    def test_deduplicates_same_week_and_after_restart(self):
        """Persist week delivery markers to survive process restarts."""

        self.subscribe()
        self.monitor.maybe_weekly_private_reports(sunday())
        self.monitor.maybe_weekly_private_reports(sunday(22, 0))
        self.monitor.telegram.send.assert_called_once()
        restarted = make_monitor(self.path)
        restarted.weekly_summary_text = Mock(return_value="weekly report")
        restarted.maybe_weekly_private_reports(sunday(22, 0))
        restarted.telegram.send.assert_not_called()

    def test_reports_not_due_before_sunday_evening(self):
        """No private digest on Sunday morning or during the week."""

        self.subscribe()
        self.monitor.maybe_weekly_private_reports(sunday(19, 59))
        self.monitor.maybe_weekly_private_reports(
            datetime(2026, 11, 9, 20, 1, tzinfo=ZoneInfo("Europe/Kyiv"))
        )
        self.monitor.telegram.send.assert_not_called()

    def test_iso_year_boundary_uses_iso_week(self):
        """A new calendar year does not necessarily mean a new ISO week."""

        at = datetime(2027, 1, 3, 20, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        self.assertEqual(APP.private_weekly_due_key(at), "2026-W53")
        self.assertIsNone(APP.private_weekly_due_key(sunday(19, 59)))

    def test_subscription_is_per_administrator(self):
        """One administrator cannot opt a different person into reports."""

        self.subscribe(7)
        self.monitor.maybe_weekly_private_reports(sunday())
        self.assertEqual(self.monitor.telegram.send.call_count, 1)
        self.assertEqual(
            self.monitor.telegram.send.call_args.kwargs["chat_id"], "7"
        )

    def test_unsubscribe_prevents_future_scheduled_delivery(self):
        """An opt-out is effective without changing the public channel."""

        self.subscribe()
        self.monitor.handle_update(update(7, "/week unsubscribe"))
        self.monitor.telegram.send.reset_mock()
        self.monitor.maybe_weekly_private_reports(sunday())
        self.monitor.telegram.send.assert_not_called()
        self.assertEqual(
            self.monitor.storage.get_meta("telegram_weekly_private_enabled_7"),
            "0",
        )

    def test_subscription_survives_restart(self):
        """The enabled marker must be saved in SQLite."""

        self.subscribe()
        restarted = make_monitor(self.path)
        self.assertEqual(
            restarted.storage.get_meta("telegram_weekly_private_enabled_7"),
            "1",
        )

    def test_failed_delivery_has_bounded_retry(self):
        """A failed API send must not repeat on every polling cycle."""

        self.subscribe()
        self.monitor.telegram.send.side_effect = RuntimeError("offline")
        self.monitor.maybe_weekly_private_reports(sunday())
        self.monitor.maybe_weekly_private_reports(sunday())
        self.assertEqual(self.monitor.telegram.send.call_count, 1)
        self.assertIsNone(
            self.monitor.storage.get_meta("telegram_weekly_private_sent_7")
        )

    def test_channel_and_non_admin_cannot_subscribe(self):
        """Ignore private subscription commands in all unauthorized contexts."""

        for message in (
            update(7, "/week subscribe", "channel"),
            update(9, "/week subscribe"),
        ):
            self.monitor.handle_update(message)
        self.monitor.telegram.send.assert_not_called()
        self.assertIsNone(
            self.monitor.storage.get_meta("telegram_weekly_private_enabled_7")
        )

    def test_invalid_command_does_not_change_storage(self):
        """Bad schedule options return usage without enabling any digest."""

        self.monitor.handle_update(update(7, "/week maybe"))
        self.assertIn(
            "/week subscribe", self.monitor.telegram.send.call_args.args[0]
        )
        self.assertIsNone(
            self.monitor.storage.get_meta("telegram_weekly_private_enabled_7")
        )

    def test_manual_weekly_report_does_not_subscribe(self):
        """A private /week request is separate from scheduled delivery."""

        self.monitor.handle_update(update(7, "/week"))
        self.assertEqual(self.monitor.telegram.send.call_args.args[0],
                         "weekly report")
        self.assertIsNone(
            self.monitor.storage.get_meta("telegram_weekly_private_enabled_7")
        )


if __name__ == "__main__":
    unittest.main()
