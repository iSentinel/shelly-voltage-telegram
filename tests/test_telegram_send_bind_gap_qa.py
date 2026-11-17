"""Characterize the remote-send / local-SQLite uncertainty gap offline."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

import test_text_status_identity_qa as fixture
from test_app import APP

CHAT = fixture.CHAT
KEY = fixture.KEY
MARKER = fixture.MARKER


class RemoteSendBindGapTests(unittest.TestCase):
    """Use only fake Telegram and disposable SQLite fixtures."""

    def setUp(self) -> None:
        """Reuse the known-good text-status identity fixture."""
        self.fixture = fixture.TextStatusIdentityQaTests(
            "test_new_status_text_is_bound_atomically"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.monitor = self.fixture.monitor
        self.storage = self.fixture.storage

    @staticmethod
    def reject_binding(*_args, **_kwargs) -> None:
        """Simulate SQLite refusing a write after a confirmed send."""
        raise sqlite3.OperationalError("synthetic post-send failure")

    def test_new_post_retry_is_blocked_after_binding_failure(self) -> None:
        """An accepted send followed by a failed bind must block retries."""
        self.storage.delete_meta(KEY)
        self.storage.delete_meta(MARKER)
        self.monitor.telegram.send.side_effect = [102, 103]
        with patch.object(self.storage, "bind_text_post", self.reject_binding):
            for _ in range(2):
                self.assertFalse(
                    self.monitor.upsert_text_status_message("QA", KEY, CHAT)
                )
        self.assertEqual(self.monitor.telegram.send.call_count, 1)
        self.assertIsNone(self.storage.get_meta(KEY))
        self.assertTrue(
            self.storage.get_meta(
                "telegram_legacy_text_confirm_pending"
            ).startswith("send:")
        )

    def test_voltage_replacement_binding_failure_blocks_replay(self) -> None:
        """A remotely accepted replacement keeps old ID and blocks replay."""
        with (
            patch.object(self.storage, "bind_text_post", self.reject_binding),
            patch.object(APP, "multiphase_status_text", return_value="QA"),
        ):
            self.assertFalse(
                APP.Monitor.replace_voltage_status(self.monitor, False, 123.0)
            )
            self.assertFalse(
                APP.Monitor.replace_voltage_status(self.monitor, False, 123.0)
            )
        self.assertEqual(self.monitor.telegram.send.call_count, 1)
        self.fixture.assert_text_identity(101)
        self.assertTrue(
            self.storage.get_meta(
                "telegram_legacy_text_confirm_pending"
            ).startswith("send:")
        )

    def test_send_timeout_leaves_durable_restart_block(self) -> None:
        """A simulated timeout must block another process's retry."""
        self.storage.delete_meta(KEY)
        self.storage.delete_meta(MARKER)
        self.monitor.telegram.send.side_effect = RuntimeError("timeout")
        self.assertFalse(
            self.monitor.upsert_text_status_message("QA", KEY, CHAT)
        )
        restarted_storage = APP.Storage(
            self.fixture.root / "test.sqlite3", "Europe/Kyiv"
        )
        restarted = self.fixture.make_monitor()
        restarted.storage = restarted_storage
        self.assertFalse(
            APP.Monitor.upsert_status_message(restarted, "QA", KEY, CHAT)
        )
        restarted.telegram.send.assert_not_called()
        self.assertTrue(
            restarted_storage.get_meta(
                "telegram_legacy_text_confirm_pending"
            ).startswith("send:")
        )

    def test_failed_marker_commit_keeps_exact_send_intent(self) -> None:
        """Do not clear a reservation when media marker commit fails."""
        token = self.storage.reserve_text_post()
        self.assertIsInstance(token, str)
        original = self.storage.set_meta

        def fail_marker(key: str, value: str, db=None) -> None:
            """Raise after the tentative message ID was written."""
            if key == MARKER:
                raise sqlite3.OperationalError("synthetic marker failure")
            original(key, value, db)

        with (
            patch.object(self.storage, "set_meta", side_effect=fail_marker),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.storage.bind_text_post(KEY, 102, CHAT, pending_token=token)
        self.fixture.assert_text_identity(101)
        self.assertEqual(
            self.storage.get_meta("telegram_legacy_text_confirm_pending"),
            "send:" + token,
        )

    def test_existing_graphic_intent_blocks_text_send(self) -> None:
        """A pending PHOTO operation cannot race a text send."""
        self.storage.set_meta("telegram_graphic_publish_pending", "QA")
        self.assertIsNone(self.storage.reserve_text_post())
        self.assertIsNone(
            self.storage.get_meta("telegram_legacy_text_confirm_pending")
        )
        self.assertFalse(
            APP.Monitor.upsert_status_message(self.monitor, "QA", KEY, CHAT)
        )
        self.storage.delete_meta(KEY)
        self.storage.delete_meta(MARKER)
        self.assertFalse(
            self.monitor.upsert_text_status_message("QA", KEY, CHAT)
        )
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()

    def test_manual_uncertain_marker_blocks_text_publish(self) -> None:
        """A manually reconciled marker blocks all subsequent sends."""
        self.storage.set_meta("telegram_legacy_text_confirm_pending", "QA")
        self.assertFalse(
            APP.Monitor.upsert_status_message(self.monitor, "QA", KEY, CHAT)
        )
        self.monitor.telegram.send.assert_not_called()
        self.fixture.assert_text_identity(101)


if __name__ == "__main__":
    unittest.main()
