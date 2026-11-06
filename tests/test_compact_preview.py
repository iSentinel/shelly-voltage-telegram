"""Private compact status preview must not alter public notifications."""

from __future__ import annotations

import time
import unittest
from unittest.mock import Mock

from test_app import APP, config


def make_monitor(language: str = "uk") -> APP.Monitor:
    """Create a disconnected test monitor with an authorized administrator."""

    monitor = APP.Monitor(
        config(
            telegram_language=language,
            telegram_chat_id="123",
            telegram_admin_user_ids=("7",),
            telegram_allowed_user_ids=("7",),
        )
    )
    monitor.telegram.send = Mock()
    monitor.telegram.edit = Mock()
    monitor.telegram.pin = Mock()
    return monitor


def private_update(user: int, chat_type: str = "private") -> dict:
    """Build a command update without any Telegram network activity."""

    chat_id = user if chat_type == "private" else 123
    return {
        "message": {
            "chat": {"type": chat_type, "id": chat_id},
            "from": {"id": user},
            "text": "/preview",
        }
    }


class CompactPreviewTests(unittest.TestCase):
    """Cover preview rendering, language, and channel isolation."""

    def test_ukrainian_offline_preview_has_no_current_voltage(self):
        """A cached voltage must not be presented as a fresh reading."""

        monitor = make_monitor()
        monitor.state.restore(time.time() - 700, 225.9, 50.0)
        monitor.state.error("unreachable", 1)
        message = APP.compact_preview_text(monitor)
        self.assertIn("Немає зв'язку", message)
        self.assertIn("поточна напруга невідома", message)
        self.assertIn("Останні дані", message)
        self.assertNotIn("L: 225.9 В", message)
        self.assertNotIn("0.0 В", message)
        self.assertIn("не змінено", message)

    def test_ukrainian_online_preview_shows_measured_values(self):
        """Display current values only when the meter is connected."""

        monitor = make_monitor()
        monitor.state.success(225.9, 50.0, 1.0, 1)
        message = APP.compact_preview_text(monitor)
        self.assertIn("Короткий статус", message)
        self.assertIn("L: 225.9 В", message)
        self.assertIn("Частота: 50.00 Гц", message)
        self.assertNotIn("avg ", message)
        self.assertNotIn("отключений", message)

    def test_missing_measurement_is_explicit(self):
        """Do not create a voltage or a time when no sample exists."""

        monitor = make_monitor()
        message = APP.compact_preview_text(monitor)
        self.assertIn("немає коректних вимірювань", message)
        self.assertNotIn("L: 0.0 В", message)

    def test_admin_can_preview_silently_in_private_chat(self):
        """A preview must go to the administrator, not the status channel."""

        monitor = make_monitor()
        monitor.handle_update(private_update(7))
        monitor.telegram.send.assert_called_once()
        call = monitor.telegram.send.call_args
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertIs(call.kwargs["notify"], False)
        self.assertIn("Короткий статус", call.args[0])
        monitor.telegram.edit.assert_not_called()
        monitor.telegram.pin.assert_not_called()

    def test_dtek_schedule_is_private_admin_only(self):
        """Only an approved administrator can request the DTEK forecast."""

        monitor = make_monitor()
        monitor.schedule_text = Mock(return_value="DTEK fixture")
        private = private_update(7)
        private["message"]["text"] = "/schedule"
        monitor.handle_update(private)
        monitor.telegram.send.assert_called_once_with(
            "DTEK fixture", notify=False, chat_id="7"
        )

        monitor.telegram.send.reset_mock()
        other = private_update(8)
        other["message"]["text"] = "/schedule"
        monitor.handle_update(other)
        monitor.telegram.send.assert_not_called()

        channel = private_update(7, "channel")
        channel["message"]["text"] = "/schedule"
        monitor.handle_update(channel)
        monitor.telegram.send.assert_not_called()

    def test_other_user_cannot_request_preview(self):
        """Ignore attempts from a Telegram user not on the admin list."""

        monitor = make_monitor()
        monitor.handle_update(private_update(8))
        monitor.telegram.send.assert_not_called()

    def test_channel_command_cannot_publish_preview(self):
        """Even an admin must use the private chat for this command."""

        monitor = make_monitor()
        monitor.handle_update(private_update(7, "channel"))
        monitor.telegram.send.assert_not_called()
        monitor.telegram.edit.assert_not_called()

    def test_unknown_channel_command_does_not_publish_offline_warning(self):
        """Invalid commands must remain silent when Shelly is offline."""

        monitor = make_monitor()
        monitor.handle_update(
            {
                "message": {
                    "chat": {"type": "channel", "id": 123},
                    "from": {"id": 7},
                    "text": "/unsupported",
                }
            }
        )
        monitor.telegram.send.assert_not_called()

    def test_preview_uses_all_selected_languages(self):
        """No translation should silently fall back to Russian."""

        for language, title in (
            ("ru", "Краткий статус"),
            ("uk", "Короткий статус"),
            ("en", "Compact status"),
        ):
            with self.subTest(language=language):
                message = APP.compact_preview_text(make_monitor(language))
                self.assertIn(title, message)


if __name__ == "__main__":
    unittest.main()
