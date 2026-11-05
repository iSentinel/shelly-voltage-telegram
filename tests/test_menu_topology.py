"""Verify private navigation and model-specific independent voltage phases."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_app import APP, config


class MenuTopologyTests(unittest.TestCase):
    """Exercise user-visible navigation and durable topology changes."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "monitor.sqlite3"
        self.monitor = APP.Monitor(
            config(
                database_path=self.path,
                telegram_chat_id="123",
                telegram_allowed_user_ids=("7", "8"),
                telegram_admin_user_ids=("7",),
            )
        )
        self.monitor.storage.initialize()
        self.monitor.telegram.send = Mock()
        self.monitor.telegram.edit = Mock()
        self.monitor.telegram.answer_callback = Mock()

    def callback(self, action="status", user=7, chat=123):
        """Build a callback from the private navigation message."""

        return {
            "callback_query": {
                "id": "callback",
                "from": {"id": user},
                "data": "menu:" + action,
                "message": {"chat": {"id": chat}, "message_id": 42},
            }
        }

    def test_start_opens_ten_action_menu(self):
        self.monitor.send_command_response("/start")
        call = self.monitor.telegram.send.call_args
        rows = call.kwargs["reply_markup"]["inline_keyboard"]
        actions = [button["callback_data"] for row in rows for button in row]
        self.assertEqual(
            actions,
            [
                "menu:" + action
                for action in (
                    "status",
                    "events",
                    "today",
                    "summary",
                    "chart",
                    "settings",
                    "outages",
                    "help",
                    "apartments",
                    "updates",
                )
            ],
        )
        self.assertNotIn("/status", call.args[0])

    def test_status_contains_live_settings_even_without_measurements(self):
        self.monitor.update_setting("editwindow_3", "7")
        self.monitor.handle_callback(self.callback())
        call = self.monitor.telegram.edit.call_args
        self.assertEqual(call.args[0], 42)
        self.assertIn(self.monitor.settings_text(), call.args[1])
        self.assertIn("3", call.args[1])
        self.assertIn("Pro EM-50", call.args[1])
        self.assertNotIn("L2", call.args[1])

    def test_status_reflects_new_measurements_and_keeps_public_status_plain(
        self,
    ):
        self.monitor.storage.add(APP.time.time(), 228.5, 50, "L1")
        self.monitor.state.restore(APP.time.time(), 228.5, 50)
        text = self.monitor.command_text("/status")
        self.assertIn("L: 228.5 V", text)
        self.assertIn(self.monitor.settings_text(), text)
        public = APP.multiphase_status_text(self.monitor)
        self.assertNotIn(self.monitor.settings_text(), public)

    def test_other_chat_and_disallowed_sender_cannot_use_menu(self):
        for query in (self.callback(chat=999), self.callback(user=9)):
            self.monitor.handle_callback(query)
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.answer_callback.assert_not_called()

    def test_update_menu_callback_is_admin_only(self):
        """The update menu never publishes release details in the group."""

        with unittest.mock.patch.object(
            APP, "release_notice", return_value="Latest stable: 0.5.13-hw"
        ) as lookup:
            self.monitor.handle_callback(self.callback("updates", user=8))
            lookup.assert_not_called()
            self.monitor.telegram.send.assert_not_called()
            self.monitor.handle_callback(self.callback("updates", user=7))
            lookup.assert_called_once()
        self.monitor.telegram.send.assert_called_once()
        self.assertEqual(
            self.monitor.telegram.send.call_args.kwargs["chat_id"], "7"
        )
        self.monitor.telegram.edit.assert_not_called()

    def test_apartment_menu_is_admin_only_and_uses_private_chat(self):
        """Apartment assignments must never appear in the shared menu."""

        self.monitor.handle_callback(self.callback("apartments", user=8))
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.handle_callback(self.callback("apartments", user=7))
        self.monitor.telegram.send.assert_called_once()
        self.assertEqual(
            self.monitor.telegram.send.call_args.kwargs["chat_id"], "7"
        )
        self.assertIn(
            "No assignments", self.monitor.telegram.send.call_args.args[0]
        )
        self.monitor.telegram.edit.assert_not_called()

    def test_apartment_phase_selector_stays_private(self):
        """Only the administrator can switch apartment list phases."""

        self.monitor.handle_apartments_command(
            "7", ["/apartments", "add", "A", "4", "L1"]
        )
        query = {
            "id": "phase-choice",
            "from": {"id": 7},
            "data": "apt:list:L1",
            "message": {"chat": {"id": 7}, "message_id": 55},
        }
        self.monitor.handle_callback({"callback_query": query})
        self.assertIn("A #4: L1", self.monitor.telegram.edit.call_args.args[1])
        self.assertEqual(
            self.monitor.telegram.edit.call_args.kwargs["chat_id"], "7"
        )
        self.monitor.telegram.edit.reset_mock()
        query["from"]["id"] = 8
        self.monitor.handle_callback({"callback_query": query})
        self.monitor.telegram.edit.assert_not_called()
        query["from"]["id"] = 7
        query["message"]["chat"]["id"] = 123
        self.monitor.handle_callback({"callback_query": query})
        self.monitor.telegram.edit.assert_not_called()

    def test_updates_command_requires_admin_and_private_reply(self):
        """Never send release details to a nonadministrator or group."""

        with unittest.mock.patch.object(
            APP, "release_notice", return_value="Latest stable: 0.5.13-hw"
        ) as lookup:
            self.monitor.handle_updates_command("8")
            lookup.assert_not_called()
            self.monitor.telegram.send.assert_not_called()
            self.monitor.handle_updates_command("7")
            lookup.assert_called_once()
        self.monitor.telegram.send.assert_called_once()
        call = self.monitor.telegram.send.call_args
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertFalse(call.kwargs["notify"])

    def test_updates_error_is_private_and_contains_no_network_details(self):
        """Keep GitHub request errors out of public Telegram messages."""

        with unittest.mock.patch.object(
            APP, "release_notice", side_effect=OSError("private-url-error")
        ) as lookup:
            self.monitor.handle_updates_command("7")
            lookup.assert_called_once()
        self.monitor.telegram.send.assert_called_once()
        call = self.monitor.telegram.send.call_args
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertNotIn("private-url-error", call.args[0])
        self.assertIn("GitHub", call.args[0])

    def test_admin_can_check_updates_from_direct_message(self):
        """An administrator can request release info without using a group."""

        msg = {
            "chat": {"id": 7, "type": "private"},
            "from": {"id": 7},
            "text": "/updates",
        }
        with unittest.mock.patch.object(
            APP, "release_notice", return_value="Stable: 0.5.12-hw"
        ) as lookup:
            self.monitor.handle_update({"message": msg})
            lookup.assert_called_once()
        self.monitor.telegram.send.assert_called_once()
        self.assertEqual(
            self.monitor.telegram.send.call_args.kwargs["chat_id"], "7"
        )

    def test_admin_can_change_apartments_only_in_own_direct_chat(self):
        """Reject forged IDs and nonadmin direct messages."""

        msg = {
            "chat": {"id": 7, "type": "private"},
            "from": {"id": 7},
            "text": "/apartments add A 12 L1",
        }
        self.monitor.handle_update({"message": msg})
        self.assertIn("Saved", self.monitor.telegram.send.call_args.args[0])
        self.monitor.telegram.send.reset_mock()
        for chat_id, sender_id in ((8, 7), (8, 8), (123, 8)):
            with self.subTest(chat=chat_id, user=sender_id):
                msg["chat"]["id"] = chat_id
                msg["from"]["id"] = sender_id
                msg["text"] = "/apartments remove A 12"
                self.monitor.handle_update({"message": msg})
        self.monitor.telegram.send.assert_not_called()
        msg["chat"]["id"] = 7
        msg["from"]["id"] = 7
        msg["text"] = "/apartments list"
        self.monitor.handle_update({"message": msg})
        self.assertIn(
            "A #12: L1", self.monitor.telegram.send.call_args.args[0]
        )

    def test_unrelated_private_commands_are_not_enabled(self):
        """Private access must not expose other group-only commands."""

        for command in ("/settings", "/maintenance", "/status", "/start"):
            with self.subTest(command=command):
                self.monitor.handle_update(
                    {
                        "message": {
                            "chat": {"id": 7, "type": "private"},
                            "from": {"id": 7},
                            "text": command,
                        }
                    }
                )
        self.monitor.telegram.send.assert_not_called()

    def test_non_admin_can_read_settings_without_write_buttons(self):
        self.monitor.handle_callback(self.callback("settings", user=8))
        self.assertIsNone(
            self.monitor.telegram.send.call_args.kwargs["reply_markup"]
        )

    def test_every_model_uses_independent_voltage_phase_count(self):
        for model in APP.METER_MODELS:
            with self.subTest(model=model):
                self.monitor.reconfigure_meter_type(model)
                expected = ("L1", "L2", "L3") if model == "pro3em" else ("L1",)
                self.assertEqual(tuple(self.monitor.phase_states), expected)
                self.assertEqual(tuple(self.monitor.phase_clients), expected)
                self.assertEqual(self.monitor.cfg.shelly_meter_type, model)
                labels = "L1, L2, L3" if model == "pro3em" else "L"
                self.assertEqual(self.monitor.phase_labels(), labels)
                self.assertIn(labels, self.monitor.settings_device_text())

    def test_switch_and_restart_preserve_all_historical_phases(self):
        for index, phase in enumerate(("L1", "L2", "L3")):
            self.monitor.storage.add(APP.time.time(), 220 + index, 50, phase)
        self.monitor.update_setting("meter_pro3em", "7")
        self.monitor.update_setting("meter_em50", "7")
        self.assertEqual(self.monitor.phase_labels(), "L")
        self.assertEqual(self.monitor.storage.latest("L2")[1], 221)
        restarted = APP.Monitor(config(database_path=self.path))
        restarted.storage.initialize()
        restarted.load_runtime_settings()
        self.assertEqual(restarted.phase_labels(), "L")
        restarted.update_setting("meter_pro3em", "7")
        self.assertEqual(
            restarted.phase_states["L2"].snapshot()["voltage"], 221
        )
        self.assertEqual(
            restarted.phase_states["L3"].snapshot()["voltage"], 222
        )

    def test_single_phase_poll_ignores_old_secondary_host_configuration(self):
        monitor = APP.Monitor(
            config(
                database_path=self.path,
                phase_l2_host="192.0.2.2",
                phase_l3_host="192.0.2.3",
            )
        )
        monitor.shelly.read = Mock(return_value=(230, 50, 1))
        monitor.poll_optional_phases = Mock()
        monitor.next_prune = APP.time.monotonic() + 100
        self.assertTrue(monitor.poll_cycle())
        monitor.shelly.read.assert_called_once()
        monitor.poll_optional_phases.assert_not_called()
        self.assertEqual(
            monitor.command_phase_hours(["/chart", "L"]), ("L1", 24)
        )
        with self.assertRaises(ValueError):
            monitor.command_phase_hours(["/chart", "L2"])

    def test_unknown_menu_action_has_no_effect(self):
        self.monitor.handle_callback(self.callback("unknown"))
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.send.assert_not_called()

    def test_inactive_phase_event_is_not_shown_after_model_change(self):
        self.monitor.last_voltage_event = ("low", 180, 50, 100, "L2")
        text = APP.multiphase_status_text(self.monitor)
        self.assertNotIn("L2", text)
        self.assertEqual(self.monitor.last_voltage_event[4], "L2")

    def test_menu_and_phase_labels_are_localized(self):
        for language in ("ru", "uk", "en"):
            self.monitor.language = language
            self.monitor.reconfigure_meter_type("pro3em")
            text = self.monitor.command_text("/status")
            for phase in ("L1", "L2", "L3"):
                self.assertIn(phase + ":", text)
            keyboard = self.monitor.main_menu_keyboard()["inline_keyboard"]
            for row in keyboard:
                for button in row:
                    self.assertFalse(button["text"].startswith("menu_"))

    def test_repeated_status_click_ignores_identical_telegram_edit(self):
        self.monitor.telegram.edit.side_effect = RuntimeError(
            "Bad Request: message is not modified"
        )
        self.monitor.handle_callback(self.callback())
        self.monitor.telegram.answer_callback.assert_called_once()
        self.monitor.telegram.send.assert_not_called()

    def test_menu_delivery_failure_is_not_silently_hidden(self):
        self.monitor.telegram.edit.side_effect = RuntimeError("unavailable")
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            self.monitor.handle_callback(self.callback())
