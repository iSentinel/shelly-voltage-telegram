"""Check restart-safe cleanup with SQLite and simulated Telegram."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class StatusCleanupTests(unittest.TestCase):
    """Cover cleanup boundaries without any network or live database."""

    def setUp(self):
        """Start with a verified, pinned status in a temporary database."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "monitor.sqlite3"
        self.chat = "-100999"
        self.bot = self.make_bot(self.chat)
        self.key = self.bot.status_message_key()
        self.bot.storage.bind_text_post(self.key, 101, self.chat)
        self.bot.storage.set_meta("telegram_status_pinned_id", "101")

    def make_bot(self, chat):
        """Create another monitor on the same disposable database."""

        bot = APP.Monitor(
            config(
                database_path=self.path,
                telegram_token="fake",
                telegram_chat_id="123",
                telegram_status_chat_id=chat,
            )
        )
        bot.storage.initialize()
        bot.telegram.send = Mock(return_value=102)
        bot.telegram.send_status_photo = Mock(return_value=102)
        bot.telegram.unpin = Mock()
        bot.telegram.delete = Mock()
        return bot

    def replace(self):
        """Send a fake replacement through the normal text binding path."""

        return self.bot.send_confirmed_text_post("status", self.key, self.chat)

    def test_crash_after_binding_retains_unpin_then_delete(self):
        """Resume committed cleanup if the sender stopped immediately."""

        self.replace()
        self.assertIsNone(
            self.bot.storage.get_meta("telegram_status_pinned_id")
        )
        restarted = self.make_bot(self.chat)
        calls = Mock()
        calls.attach_mock(restarted.telegram.unpin, "unpin")
        calls.attach_mock(restarted.telegram.delete, "delete")
        restarted.maybe_cleanup_status()
        self.assertEqual(
            [(call[0], call.args) for call in calls.mock_calls],
            [("unpin", (101, self.chat)), ("delete", (101, self.chat))],
        )
        self.assertIsNone(
            restarted.storage.next_status_cleanup(
                self.chat, APP.time.time(), None
            )
        )
        restarted.telegram.send.assert_not_called()

    def test_failed_unpin_blocks_delete_and_retries_after_restart(self):
        """Another failure can delay cleanup without losing the old pin."""

        self.replace()
        self.bot.telegram.unpin.side_effect = RuntimeError("temporary")
        with patch.object(APP.time, "time", return_value=1000):
            self.bot.delete_previous_status(101)
        self.bot.telegram.delete.assert_not_called()
        restarted = self.make_bot(self.chat)
        with patch.object(APP.time, "time", return_value=1030):
            restarted.delete_previous_status()
        restarted.telegram.unpin.assert_not_called()
        with patch.object(APP.time, "time", return_value=1061):
            restarted.delete_previous_status()
        restarted.telegram.unpin.assert_called_once_with(101, self.chat)
        restarted.telegram.delete.assert_called_once_with(101, self.chat)

    def test_delete_failure_retries_only_delete(self):
        """Successful unpin survives a delete failure and restart."""

        self.replace()
        self.bot.telegram.unpin.side_effect = RuntimeError(
            "message to unpin not found"
        )
        self.bot.telegram.delete.side_effect = RuntimeError("temporary")
        with patch.object(APP.time, "time", return_value=2000):
            self.bot.delete_previous_status()
        restarted = self.make_bot(self.chat)
        restarted.telegram.delete.side_effect = RuntimeError(
            "message to delete not found"
        )
        with patch.object(APP.time, "time", return_value=2061):
            restarted.delete_previous_status()
        restarted.telegram.unpin.assert_not_called()
        restarted.telegram.delete.assert_called_once_with(101, self.chat)
        self.assertIsNone(
            restarted.storage.next_status_cleanup(self.chat, 3000, None)
        )

    def test_cleanup_does_not_target_current_or_other_chat(self):
        """A restored current ID or a changed destination blocks cleanup."""

        self.replace()
        other = self.make_bot("-100333")
        other.storage.bind_text_post(self.key, 200, "-100333")
        other.delete_previous_status()
        other.telegram.delete.assert_not_called()
        self.bot.storage.bind_text_post(self.key, 101, self.chat)
        self.bot.delete_previous_status()
        self.bot.telegram.unpin.assert_not_called()
        self.bot.telegram.delete.assert_not_called()

    def test_unrelated_pin_is_preserved(self):
        """Delete an unpinned old status without modifying another pin."""

        self.bot.storage.set_meta("telegram_status_pinned_id", "77")
        self.replace()
        self.bot.delete_previous_status()
        self.bot.telegram.unpin.assert_not_called()
        self.bot.telegram.delete.assert_called_once_with(101, self.chat)
        self.assertEqual(
            self.bot.storage.get_meta("telegram_status_pinned_id"), "77"
        )

    def test_unverified_old_id_is_not_queued(self):
        """Legacy data cannot authorize deleting a post in the wrong chat."""

        self.bot.storage.delete_meta("telegram_status_media_identity")
        self.replace()
        self.bot.delete_previous_status()
        self.bot.telegram.unpin.assert_not_called()
        self.bot.telegram.delete.assert_not_called()

    def test_atomic_binding_failure_retains_current_and_send_intent(self):
        """Failure after queue insertion rolls back every part of the bind."""

        with closing(self.bot.storage.connect()) as db, db:
            db.execute(
                "CREATE TRIGGER reject_id BEFORE UPDATE ON meta "
                "WHEN NEW.key='telegram_status_message_id' "
                "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.replace()
        self.assertEqual(self.bot.storage.get_meta(self.key), "101")
        self.assertEqual(
            self.bot.storage.get_meta("telegram_status_pinned_id"), "101"
        )
        self.assertTrue(self.bot.legacy_confirmation_pending())
        self.assertIsNone(
            self.bot.storage.next_status_cleanup(
                self.chat, APP.time.time(), None
            )
        )

    def test_photo_cannot_reserve_over_an_uncertain_text_send(self):
        """A pending text send prevents an overlapping photo publication."""

        token = self.bot.storage.reserve_text_post()
        self.assertIsNotNone(token)
        self.assertFalse(self.bot.publish_graphic_status("status"))
        self.bot.telegram.send_status_photo.assert_not_called()
        self.assertEqual(self.bot.storage.get_meta(self.key), "101")

    def test_graphic_replacement_uses_same_queue(self):
        """Confirmed photo publication drives the same cleanup transaction."""

        self.bot.graphic_status_media = Mock(
            return_value=(Path("synthetic.png"), "test caption")
        )
        self.assertTrue(self.bot.publish_graphic_status("status"))
        self.assertEqual(self.bot.status_media_kind(), "photo")
        self.assertIsNone(
            self.bot.storage.get_meta("telegram_graphic_publish_pending")
        )
        self.bot.telegram.unpin.assert_called_once_with(101, self.chat)
        self.bot.telegram.delete.assert_called_once_with(101, self.chat)
