"""Keep confirmed text Telegram status identity and ID in one transaction."""

from __future__ import annotations

import ast
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from test_app import APP

KEY = "telegram_status_message_id"
MARKER = "telegram_status_media_identity"
CHAT = "-100123"
NAMES = (
    "status_message_key",
    "status_media_kind",
    "legacy_confirmation_pending",
    "legacy_media_guard",
    "graphic_channel_opted_in",
    "status_keyboard",
    "rotate_legacy_photo_after_recovery",
    "upsert_text_status_message",
)


class TextStatusIdentityQaTests(unittest.TestCase):
    """Only disposable SQLite and fake Telegram methods are used."""

    def setUp(self) -> None:
        """Create a fake monitor and verified starting text identity."""
        temporary = tempfile.TemporaryDirectory(prefix="qa-text-status-id-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        flags = patch.dict(
            os.environ,
            {
                "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "0",
                "TELEGRAM_GRAPHIC_OWNER_APPROVED": "0",
            },
        )
        flags.start()
        self.addCleanup(flags.stop)
        self.storage = APP.Storage(self.root / "test.sqlite3", "Europe/Kyiv")
        self.storage.initialize()
        self.storage.set_meta(KEY, "101")
        self.storage.set_meta(MARKER, f"text:{CHAT}:101")
        self.monitor = self.make_monitor()

    def make_monitor(self) -> SimpleNamespace:
        """Bind only safe source methods to a synthetic monitor."""
        bot = Mock()
        bot.enabled = True
        bot.status_chat_id = CHAT
        bot.send.return_value = 102
        monitor = SimpleNamespace(
            storage=self.storage,
            telegram=bot,
            cfg=SimpleNamespace(telegram_status_chat_id=CHAT),
            last_recovery_duration=None,
            last_recovery_at=None,
            last_telegram=0.0,
            last_sent_connectivity=None,
        )
        for name in NAMES:
            method = MethodType(getattr(APP.Monitor, name), monitor)
            setattr(monitor, name, method)
        monitor.finish_voltage_status_update = Mock()
        monitor.ensure_status_pinned = Mock()
        monitor.delete_previous_voltage_status = Mock()
        monitor.voltage_status_edit_window_active = Mock(return_value=False)
        return monitor

    def assert_text_identity(self, message_id: int) -> None:
        """Confirm that the message ID and type belong to the same post."""
        self.assertEqual(self.storage.get_meta(KEY), str(message_id))
        marker = f"text:{CHAT}:{message_id}"
        self.assertEqual(self.storage.get_meta(MARKER), marker)
        self.assertEqual(self.monitor.status_media_kind(), "text")

    def test_new_status_text_is_bound_atomically(self) -> None:
        """The first known text send must record the exact media type."""
        self.storage.delete_meta(KEY)
        self.storage.delete_meta(MARKER)
        result = self.monitor.upsert_text_status_message("fixture", KEY, CHAT)
        self.assertTrue(result)
        self.assert_text_identity(102)

    def test_missing_text_message_recreation_rebinds(self) -> None:
        """A confirmed replacement cannot keep the stale prior ID."""
        self.monitor.telegram.edit.side_effect = RuntimeError("not found")
        with (
            patch.object(APP, "message_missing", return_value=True),
            patch.object(APP, "message_not_modified", return_value=False),
        ):
            result = self.monitor.upsert_text_status_message(
                "fixture", KEY, CHAT
            )
        self.assertTrue(result)
        self.assert_text_identity(102)

    def test_voltage_status_replacement_rebinds(self) -> None:
        """New voltage text must not strand the previous status marker."""
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            result = APP.Monitor.replace_voltage_status(
                self.monitor, False, 123.0
            )
        self.assertTrue(result)
        self.assert_text_identity(102)
        self.monitor.delete_previous_voltage_status.assert_called_once()

    def test_recovery_rotation_rebinds(self) -> None:
        """A recovered connection creates one verified new text status."""
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.rotate_status_after_recovery(self.monitor, 9200.0)
        self.assert_text_identity(102)
        self.assertIsNone(
            self.storage.get_meta(APP.RECOVERY_ROTATION_PENDING_KEY)
        )

    def test_stable_recovery_replacement_rebinds(self) -> None:
        """A silent stable recovery rotation stores the replacement ID."""

        self.monitor.stable_recovery_status_due = Mock(return_value=True)
        self.monitor.clear_stable_recovery_context = Mock()
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.maybe_rotate_stable_recovery_status(self.monitor)
        self.assert_text_identity(102)
        self.monitor.clear_stable_recovery_context.assert_called_once()
        self.monitor.delete_previous_voltage_status.assert_called_once()

    def test_stable_recovery_then_edit_reuses_one_status(self) -> None:
        """A replacement can be edited without sending another message."""

        self.monitor.stable_recovery_status_due = Mock(return_value=True)
        self.monitor.clear_stable_recovery_context = Mock()
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.maybe_rotate_stable_recovery_status(self.monitor)
        self.assert_text_identity(102)
        updated = APP.Monitor.upsert_status_message(
            self.monitor, "latest", KEY, CHAT
        )
        self.assertTrue(updated)
        self.assert_text_identity(102)
        self.monitor.telegram.send.assert_called_once()
        self.monitor.telegram.edit.assert_called_once()

    def test_all_text_status_send_paths_bind_confirmed_identity(self) -> None:
        """Static guard prevents regression in a newly added send path."""

        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "app.py").read_text())
        sites = {
            "upsert_text_status_message": 2,
            "rotate_status_after_recovery": 1,
            "maybe_rotate_stable_recovery_status": 1,
            "replace_voltage_status": 1,
        }
        functions = {
            node.name: node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name in sites
        }
        self.assertEqual(functions.keys(), sites.keys())
        for name, node in functions.items():
            calls = [
                item for item in ast.walk(node) if isinstance(item, ast.Call)
            ]
            atomic = [
                item
                for item in calls
                if isinstance(item.func, ast.Attribute)
                and item.func.attr == "bind_text_post"
            ]
            self.assertEqual(len(atomic), sites[name], name)
            direct = [
                item
                for item in calls
                if ast.unparse(item.func) == "self.storage.set_meta"
                and item.args
                and isinstance(item.args[0], ast.Name)
                and item.args[0].id == "key"
            ]
            self.assertFalse(direct, name)

    def test_stable_recovery_unknown_marker_never_sends(self) -> None:
        """A preexisting stale marker must remain fail-closed."""

        self.storage.set_meta(KEY, "102")
        self.monitor.stable_recovery_status_due = Mock(return_value=True)
        self.monitor.clear_stable_recovery_context = Mock()
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.maybe_rotate_stable_recovery_status(self.monitor)
        self.monitor.telegram.send.assert_not_called()
        self.monitor.clear_stable_recovery_context.assert_not_called()
        self.assertEqual(self.storage.get_meta(MARKER), f"text:{CHAT}:101")

    def test_stable_recovery_send_failure_preserves_old_binding(self) -> None:
        """An uncertain stable-recovery send cannot overwrite old identity."""

        self.monitor.stable_recovery_status_due = Mock(return_value=True)
        self.monitor.clear_stable_recovery_context = Mock()
        self.monitor.telegram.send.side_effect = RuntimeError("synthetic")
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.maybe_rotate_stable_recovery_status(self.monitor)
        self.assert_text_identity(101)
        self.monitor.clear_stable_recovery_context.assert_not_called()

    def test_stale_preexisting_marker_stays_blocked(self) -> None:
        """Never assume that an unknown current post is text or photo."""
        self.storage.set_meta(KEY, "102")
        current_marker = self.storage.get_meta(MARKER)
        result = APP.Monitor.upsert_status_message(
            self.monitor, "fixture", KEY, CHAT
        )
        self.assertFalse(result)
        self.assertEqual(self.storage.get_meta(MARKER), current_marker)
        self.monitor.telegram.send.assert_not_called()

    def test_atomic_write_failure_retains_old_status(self) -> None:
        """A failed marker write must roll back the ID update too."""
        original = self.storage.set_meta

        def broken(key: str, value: str, db=None) -> None:
            """Raise only on the synthetic media binding update."""
            if key == MARKER:
                raise sqlite3.OperationalError("synthetic")
            original(key, value, db)

        with (
            patch.object(self.storage, "set_meta", side_effect=broken),
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.storage.bind_text_post(KEY, 102, CHAT)
        self.assert_text_identity(101)

    def test_photo_status_does_not_send_new_text(self) -> None:
        """A real photo marker remains protected from text replacement."""
        self.storage.set_meta(MARKER, f"photo:{CHAT}:101")
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.rotate_status_after_recovery(self.monitor, 123.0)
        self.monitor.telegram.edit_photo_caption.assert_called_once()
        self.monitor.telegram.send.assert_not_called()
        self.assertEqual(self.storage.get_meta(MARKER), f"photo:{CHAT}:101")

    def test_failed_recovery_send_keeps_old_identity(self) -> None:
        """An uncertain send may never silently rebind a prior message."""
        self.monitor.telegram.send.side_effect = RuntimeError("synthetic")
        with patch.object(APP, "multiphase_status_text", return_value="QA"):
            APP.Monitor.rotate_status_after_recovery(self.monitor, 123.0)
        self.assert_text_identity(101)
        self.assertIsNotNone(
            self.storage.get_meta(APP.RECOVERY_ROTATION_PENDING_KEY)
        )


if __name__ == "__main__":
    unittest.main()
