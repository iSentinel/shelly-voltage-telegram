"""Check persistent Telegram message reuse independently of sound policy."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class VoltageEditWindowTests(unittest.TestCase):
    """Cover message lifetime, upgrades, settings and notification policy."""

    def monitor(self, **overrides):
        """Create an isolated database and replace all Telegram mutations."""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        values = {
            "database_path": Path(directory.name) / "state.sqlite3",
            "telegram_token": "token",
            "telegram_chat_id": "123",
            "telegram_status_chat_id": "-100999",
            "telegram_pin_status": False,
        }
        values.update(overrides)
        monitor = APP.Monitor(config(**values))
        monitor.storage.initialize()
        monitor.state.restore(900.0, 230.0, 50.0)
        monitor.storage.add(900.0, 230.0, 50.0, "L1")
        self.mock_telegram(monitor)
        return monitor

    def mock_telegram(self, monitor):
        """Record Telegram operations without contacting the public service."""

        for name in (
            "edit",
            "delete",
            "unpin",
            "pin",
            "set_commands",
            "clear_reply_markup",
        ):
            setattr(monitor.telegram, name, Mock())
        monitor.telegram.quiet_now = Mock(return_value=False)
        monitor.telegram.send = Mock(side_effect=range(101, 120))

    def event(self, monitor, kind, timestamp):
        """Update the observed voltage and publish one confirmed transition."""

        voltage = {
            "critical_low": 170.0,
            "critical_high": 260.0,
            "recovered": 230.0,
        }[kind]
        monitor.state.restore(timestamp, voltage, 50.0)
        with patch.object(APP.time, "time", return_value=timestamp):
            monitor.record_voltage_event(kind, voltage, 50.0)

    def test_low_normal_low_high_normal_reuses_original_post(self):
        """Recovery and opposite critical states keep the original message."""

        monitor = self.monitor()
        kinds = (
            "critical_low",
            "recovered",
            "critical_low",
            "critical_high",
            "recovered",
        )
        for offset, kind in enumerate(kinds):
            self.event(monitor, kind, 1000.0 + offset * 60)
        self.assertEqual(monitor.telegram.send.call_count, 1)
        self.assertEqual(monitor.telegram.edit.call_count, 4)
        self.assertEqual(
            {call.args[0] for call in monitor.telegram.edit.call_args_list},
            {101},
        )
        self.assertEqual(
            monitor.storage.get_meta("voltage_status_edit_window_started_at"),
            "1000.0",
        )
        monitor.telegram.unpin.assert_not_called()
        monitor.telegram.delete.assert_not_called()

    def test_all_window_lengths_include_exact_boundary(self):
        """Each supported duration keeps its post at the exact deadline."""

        for minutes in (3, 5, 10):
            with self.subTest(minutes=minutes):
                monitor = self.monitor(voltage_status_edit_window_min=minutes)
                self.event(monitor, "critical_low", 1000.0)
                self.event(monitor, "recovered", 1000.0 + minutes * 60)
                monitor.telegram.edit.assert_called_once()
                self.assertEqual(monitor.telegram.send.call_count, 1)

    def test_expiry_does_not_slide_after_edits(self):
        """A later transition creates a post after the original deadline."""

        monitor = self.monitor()
        operations = Mock()
        for name in ("send", "unpin", "delete", "edit"):
            operations.attach_mock(getattr(monitor.telegram, name), name)
        self.event(monitor, "critical_low", 1000.0)
        self.event(monitor, "recovered", 1599.0)
        operations.reset_mock()
        self.event(monitor, "critical_high", 1601.0)
        self.assertEqual(
            [call[0] for call in operations.mock_calls],
            ["send", "unpin", "delete"],
        )
        monitor.telegram.unpin.assert_called_once_with(101, "-100999")
        monitor.telegram.delete.assert_called_once_with(101, "-100999")
        self.assertEqual(
            monitor.storage.get_meta("voltage_status_edit_window_started_at"),
            "1601.0",
        )

    def test_restart_preserves_message_window_and_settings(self):
        """Restart restores the setting and the original message deadline."""

        monitor = self.monitor()
        monitor.update_setting("editwindow_5", "7")
        self.event(monitor, "critical_low", 1000.0)
        self.event(monitor, "recovered", 1060.0)
        restarted = APP.Monitor(monitor.cfg)
        self.mock_telegram(restarted)
        with patch.object(APP, "start_health_server"):
            restarted.initialize()
        self.event(restarted, "critical_high", 1120.0)
        self.assertEqual(restarted.status_edit_window_min, 5)
        restarted.telegram.send.assert_not_called()
        self.assertEqual(restarted.telegram.edit.call_args.args[0], 101)
        self.assertEqual(
            restarted.storage.get_meta(
                "voltage_status_edit_window_started_at"
            ),
            "1000.0",
        )
        self.assertEqual(
            restarted.storage.get_meta("last_voltage_notification_at"),
            "1000.0",
        )

    def test_upgrade_reuses_recent_legacy_event(self):
        """An existing metadata database needs no destructive schema change."""

        monitor = self.monitor()
        self.event(monitor, "critical_low", 1000.0)
        monitor.storage.delete_meta("voltage_status_edit_window_started_at")
        monitor.restore_voltage_event()
        with patch.object(APP.time, "time", return_value=1100.0):
            monitor.restore_voltage_status_window()
        self.event(monitor, "recovered", 1120.0)
        monitor.telegram.edit.assert_called_once()
        self.assertEqual(monitor.telegram.send.call_count, 1)

    def test_upgrade_does_not_reopen_expired_legacy_event(self):
        """An old event cannot grant a fresh window after an upgrade."""

        monitor = self.monitor()
        self.event(monitor, "critical_low", 1000.0)
        monitor.storage.delete_meta("voltage_status_edit_window_started_at")
        with patch.object(APP.time, "time", return_value=1700.0):
            monitor.restore_voltage_status_window()
        self.assertIsNone(
            monitor.storage.get_meta("voltage_status_edit_window_started_at")
        )

    def test_edit_failure_keeps_post_without_send_or_delete(self):
        """A temporary edit failure never falls back to a new message."""

        monitor = self.monitor()
        self.event(monitor, "critical_low", 1000.0)
        monitor.telegram.edit.side_effect = RuntimeError("temporary failure")
        self.event(monitor, "recovered", 1100.0)
        self.assertEqual(monitor.telegram.send.call_count, 1)
        monitor.telegram.delete.assert_not_called()
        monitor.telegram.unpin.assert_not_called()
        self.assertEqual(
            monitor.storage.get_meta("telegram_status_message_id"), "101"
        )

    def test_missing_message_can_be_recreated(self):
        """A confirmed missing Telegram message can be replaced."""

        monitor = self.monitor()
        self.event(monitor, "critical_low", 1000.0)
        monitor.telegram.edit.side_effect = RuntimeError(
            "Bad Request: message to edit not found"
        )
        self.event(monitor, "recovered", 1100.0)
        self.assertEqual(monitor.telegram.send.call_count, 2)
        self.assertFalse(monitor.telegram.send.call_args.kwargs["notify"])
        self.assertEqual(
            monitor.storage.get_meta("telegram_status_message_id"), "102"
        )

    def test_unchanged_message_counts_as_success(self):
        """Telegram's unchanged-message response keeps the same post."""

        monitor = self.monitor()
        self.event(monitor, "critical_low", 1000.0)
        monitor.telegram.edit.side_effect = RuntimeError(
            "Bad Request: message is not modified"
        )
        self.event(monitor, "recovered", 1100.0)
        self.assertEqual(monitor.telegram.send.call_count, 1)
        monitor.telegram.delete.assert_not_called()

    def test_edits_do_not_consume_or_extend_sound_cooldown(self):
        """Message edits leave the last audible send timestamp untouched."""

        monitor = self.monitor(
            voltage_status_edit_window_min=3,
            voltage_notification_cooldown_min=10,
        )
        self.event(monitor, "critical_low", 1000.0)
        self.event(monitor, "critical_high", 1100.0)
        self.event(monitor, "critical_low", 1181.0)
        self.assertFalse(monitor.telegram.send.call_args.kwargs["notify"])
        self.assertEqual(
            monitor.storage.get_meta("last_voltage_notification_at"), "1000.0"
        )
        self.event(monitor, "critical_high", 1601.0)
        self.assertTrue(monitor.telegram.send.call_args.kwargs["notify"])

    def test_quiet_hours_keep_edits_and_new_posts_silent(self):
        """Quiet hours suppress sounds without changing message reuse."""

        monitor = self.monitor(voltage_status_edit_window_min=3)
        monitor.telegram.quiet_now.return_value = True
        self.event(monitor, "critical_low", 1000.0)
        self.event(monitor, "recovered", 1100.0)
        self.event(monitor, "critical_high", 1181.0)
        self.assertEqual(monitor.telegram.edit.call_count, 1)
        for call in monitor.telegram.send.call_args_list:
            self.assertFalse(call.kwargs["notify"])
        self.assertIsNone(
            monitor.storage.get_meta("last_voltage_notification_at")
        )

    def test_settings_choices_are_persisted_separately(self):
        """Only supported edit durations are accepted in settings."""

        monitor = self.monitor()
        for minutes in (3, 5, 10):
            monitor.update_setting(f"editwindow_{minutes}", "7")
            self.assertEqual(monitor.status_edit_window_min, minutes)
            self.assertEqual(
                monitor.storage.get_meta(
                    "telegram_setting_status_edit_window"
                ),
                str(minutes),
            )
        self.assertEqual(monitor.notification_cooldown_min, 5)
        with self.assertRaises(ValueError):
            monitor.update_setting("editwindow_4", "7")
        self.assertEqual(monitor.status_edit_window_min, 10)
        keyboard = str(monitor.settings_keyboard("edit"))
        for minutes in (3, 5, 10):
            self.assertIn(f"editwindow_{minutes}", keyboard)

    def test_environment_default_and_validation(self):
        """The default is ten minutes; unsupported durations are rejected."""

        with patch.dict(os.environ, {}, clear=True):
            defaults = APP.telegram_policy_settings()
        self.assertEqual(defaults["voltage_status_edit_window_min"], 10)
        for minutes in (0, 4, 11):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                APP.validate_telegram_config(
                    config(voltage_status_edit_window_min=minutes)
                )

    def test_invalid_or_future_window_does_not_reuse_post(self):
        """Bad timestamps and backward clock jumps do not extend a window."""

        for raw in ("invalid", "nan", "inf", "2000.0"):
            with self.subTest(raw=raw):
                monitor = self.monitor()
                monitor.storage.set_meta("telegram_status_message_id", "50")
                monitor.storage.set_meta(
                    "voltage_status_edit_window_started_at", raw
                )
                self.event(monitor, "critical_low", 1000.0)
                monitor.telegram.edit.assert_not_called()
                monitor.telegram.send.assert_called_once()

    def test_periodic_recreation_clears_previous_window(self):
        """A recreated periodic status cannot inherit a deleted post's age."""

        monitor = self.monitor()
        self.event(monitor, "critical_low", 1000.0)
        monitor.telegram.edit.side_effect = RuntimeError(
            "message to edit not found"
        )
        self.assertTrue(
            monitor.upsert_status_message(
                "status", monitor.status_message_key(), "-100999"
            )
        )
        self.assertIsNone(
            monitor.storage.get_meta("voltage_status_edit_window_started_at")
        )
