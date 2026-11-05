"""Per-administrator Telegram preview preferences and channel isolation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_app import APP, config


def make_monitor(path: Path, language: str = "uk") -> APP.Monitor:
    """Create an isolated monitor and initialized settings database."""

    monitor = APP.Monitor(
        config(
            database_path=path,
            telegram_language=language,
            telegram_chat_id="123",
            telegram_admin_user_ids=("7", "8"),
            telegram_allowed_user_ids=("7", "8"),
        )
    )
    monitor.storage.initialize()
    monitor.telegram.send = Mock()
    monitor.telegram.edit = Mock()
    monitor.telegram.pin = Mock()
    return monitor


def update(user: int, command: str, chat_type: str = "private") -> dict:
    """Build a Telegram command without connecting to Telegram."""

    chat_id = user if chat_type == "private" else 123
    return {
        "message": {
            "chat": {"type": chat_type, "id": chat_id},
            "from": {"id": user},
            "text": command,
        }
    }


class PrivatePreviewLayoutTests(unittest.TestCase):
    """Validate an opt-in private layout without touching public status."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "status.sqlite3"
        self.monitor = make_monitor(self.path)

    def test_default_layout_remains_compact(self):
        """A fresh administrator sees the safe compact preview."""

        self.monitor.handle_update(update(7, "/preview"))
        call = self.monitor.telegram.send.call_args
        self.assertIn("Короткий статус", call.args[0])
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertFalse(call.kwargs["notify"])
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()

    def test_admin_can_choose_detailed_private_layout(self):
        """The detailed private view includes the existing settings panel."""

        self.monitor.handle_update(update(7, "/view detailed"))
        call = self.monitor.telegram.send.call_args
        self.assertIn("докладний", call.args[0])
        self.assertIn("Налаштування", call.args[0])
        self.assertFalse(call.kwargs["notify"])
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertEqual(self.monitor.private_preview_layout("7"), "detailed")
        self.monitor.telegram.edit.assert_not_called()

    def test_preference_survives_process_restart(self):
        """Store the choice in SQLite rather than only in memory."""

        self.monitor.handle_update(update(7, "/view detailed"))
        restarted = make_monitor(self.path)
        self.assertEqual(restarted.private_preview_layout("7"), "detailed")
        restarted.handle_update(update(7, "/preview"))
        self.assertIn(
            "Налаштування", restarted.telegram.send.call_args.args[0]
        )

    def test_preferences_are_per_administrator(self):
        """One administrator cannot silently change another person's view."""

        self.monitor.handle_update(update(7, "/view detailed"))
        self.assertEqual(self.monitor.private_preview_layout("8"), "compact")
        self.monitor.handle_update(update(8, "/preview"))
        self.assertIn(
            "Короткий статус", self.monitor.telegram.send.call_args.args[0]
        )

    def test_switch_back_to_compact(self):
        """Switching back must preserve notification and pin policies."""

        self.monitor.handle_update(update(7, "/view detailed"))
        self.monitor.handle_update(update(7, "/view compact"))
        self.assertEqual(self.monitor.private_preview_layout("7"), "compact")
        self.assertIn(
            "Короткий статус", self.monitor.telegram.send.call_args.args[0]
        )
        self.monitor.telegram.pin.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()

    def test_invalid_selection_is_read_only(self):
        """Wrong input sends usage without storing a preference."""

        self.monitor.handle_update(update(7, "/view invalid"))
        call = self.monitor.telegram.send.call_args
        self.assertIn("/view compact", call.args[0])
        self.assertEqual(self.monitor.private_preview_layout("7"), "compact")
        self.assertFalse(call.kwargs["notify"])

    def test_channel_cannot_modify_private_preference(self):
        """Private view commands may not be run from the status channel."""

        self.monitor.handle_update(update(7, "/view detailed", "channel"))
        self.assertEqual(self.monitor.private_preview_layout("7"), "compact")
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()

    def test_unauthorized_sender_cannot_change_preference(self):
        """Non-administrator commands are ignored without any response."""

        self.monitor.handle_update(update(9, "/view detailed"))
        self.assertEqual(self.monitor.private_preview_layout("9"), "compact")
        self.monitor.telegram.send.assert_not_called()

    def test_localized_selection_confirmation(self):
        """Mode names must use the selected language."""

        for language, selected in (
            ("ru", "подробный"),
            ("uk", "докладний"),
            ("en", "detailed"),
        ):
            with self.subTest(language=language):
                monitor = make_monitor(self.path, language)
                monitor.handle_update(update(7, "/view detailed"))
                self.assertIn(
                    selected, monitor.telegram.send.call_args.args[0]
                )

    def test_offline_detailed_view_hides_cached_voltage(self):
        """Detailed mode still must not publish stale voltage as live."""

        self.monitor.state.restore(1000.0, 225.9, 50.0)
        self.monitor.state.error("unreachable", 1)
        self.monitor.handle_update(update(7, "/view detailed"))
        message = self.monitor.telegram.send.call_args.args[0]
        self.assertNotIn("L: 225.9 В", message)
        self.assertIn("недоступна", message)


if __name__ == "__main__":
    unittest.main()
