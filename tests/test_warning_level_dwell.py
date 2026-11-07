"""Regression tests for independent warning vs critical confirmation times."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


def live_style_monitor(**overrides):
    """Prepare a disconnected monitor with Telegram event recording mocked."""

    settings = {
        "telegram_token": "test-token",
        "telegram_chat_id": "123",
        "alerts_enabled": True,
        "alert_dwell_sec": 15.0,
        "alert_recovery_dwell_sec": 30.0,
    }
    settings.update(overrides)
    monitor = APP.Monitor(config(**settings))
    monitor.record_voltage_event = Mock()
    return monitor


class WarningDwellTests(unittest.TestCase):
    """Protect the existing critical speed and independent phase handling."""

    def test_default_warning_dwell_equals_legacy_alert_dwell(self):
        """An upgrade without a saved override keeps the old timing."""

        monitor = live_style_monitor(alert_dwell_sec=42.0)
        self.assertEqual(monitor.warning_dwell, 42.0)
        self.assertEqual(monitor.level_dwell("warning"), 42.0)
        self.assertEqual(monitor.level_dwell("critical"), 42.0)
        self.assertEqual(monitor.level_dwell("normal"), 30.0)

    def test_warning_180_does_not_delay_critical_15(self):
        """Set a slow warning while preserving a fast critical escalation."""

        monitor = live_style_monitor()
        monitor.warning_dwell = 180.0
        with patch.object(
            APP.time,
            "monotonic",
            side_effect=[10.0, 25.0, 50.0, 65.0],
        ):
            monitor.maybe_alert(206.0, 50.0)
            monitor.maybe_alert(205.0, 50.0)
            monitor.maybe_alert(189.0, 50.0)
            monitor.maybe_alert(189.0, 50.0)
        monitor.record_voltage_event.assert_called_once_with(
            "critical_low", 189.0, 50.0
        )

    def test_sustained_warning_respects_180_seconds(self):
        """Do not notify for a short dip below the 207 V warning level."""

        monitor = live_style_monitor()
        monitor.warning_dwell = 180.0
        with patch.object(
            APP.time,
            "monotonic",
            side_effect=[100.0, 279.9, 280.0],
        ):
            monitor.maybe_alert(205.0, 50.0)
            monitor.maybe_alert(205.0, 50.0)
            monitor.record_voltage_event.assert_not_called()
            monitor.maybe_alert(205.0, 50.0)
        monitor.record_voltage_event.assert_called_once_with(
            "warning_low", 205.0, 50.0
        )

    def test_normal_recovery_still_uses_existing_dwell(self):
        """An active warning clears with the original recovery dwell."""

        monitor = live_style_monitor()
        monitor.warning_dwell = 180.0
        monitor.alert_level = "warning"
        with patch.object(
            APP.time, "monotonic", side_effect=[100.0, 129.0, 130.0]
        ):
            monitor.maybe_alert(231.0, 50.0)
            monitor.maybe_alert(231.0, 50.0)
            monitor.record_voltage_event.assert_not_called()
            monitor.maybe_alert(231.0, 50.0)
        monitor.record_voltage_event.assert_called_once_with(
            "recovered", 231.0, 50.0
        )

    def test_secondary_phase_uses_warning_only_dwell(self):
        """L2/L3 warning confirmation follows the same separate policy."""

        monitor = live_style_monitor(shelly_meter_type="pro3em")
        monitor.warning_dwell = 180.0
        monitor.maybe_secondary_level("L2", 205.0, 50.0, 100.0)
        monitor.maybe_secondary_level("L2", 205.0, 50.0, 279.9)
        monitor.record_voltage_event.assert_not_called()
        monitor.maybe_secondary_level("L2", 205.0, 50.0, 280.0)
        monitor.record_voltage_event.assert_called_once_with(
            "warning_low", 205.0, 50.0, "L2"
        )

    def test_secondary_critical_is_not_delayed(self):
        """Critical event on L3 still follows the original 15 seconds."""

        monitor = live_style_monitor(shelly_meter_type="pro3em")
        monitor.warning_dwell = 300.0
        monitor.maybe_secondary_level("L3", 189.0, 50.0, 100.0)
        monitor.maybe_secondary_level("L3", 189.0, 50.0, 115.0)
        monitor.record_voltage_event.assert_called_once_with(
            "critical_low", 189.0, 50.0, "L3"
        )

    def test_admin_selection_persists_without_touching_critical(self):
        """Warning-only choice is audited via existing settings workflow."""

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "warning-dwell.sqlite3"
            monitor = live_style_monitor(database_path=path)
            monitor.storage.initialize()
            monitor.update_setting("warnwait_180", "7")
            self.assertEqual(monitor.warning_dwell, 180.0)
            self.assertEqual(monitor.alert_dwell, 15.0)
            self.assertEqual(
                monitor.storage.get_meta("telegram_setting_warning_dwell"),
                "180",
            )

    def test_legacy_setting_dwell_is_used_when_override_missing(self):
        """Prior manually configured dwell remains effective on upgrade."""

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.sqlite3"
            monitor = live_style_monitor(database_path=path)
            monitor.storage.initialize()
            monitor.storage.set_meta("telegram_setting_alert_dwell", "45")
            monitor.load_runtime_alert_settings()
            self.assertEqual(monitor.alert_dwell, 45.0)
            self.assertEqual(monitor.warning_dwell, 45.0)

    def test_dwell_inc_changes_both_until_warn_is_customized(self):
        """Preserve the previous global dwell action for legacy settings."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = live_style_monitor(
                database_path=Path(folder) / "settings.sqlite3"
            )
            monitor.storage.initialize()
            monitor.update_setting("dwell_inc", "7")
            self.assertEqual(monitor.alert_dwell, 20.0)
            self.assertEqual(monitor.warning_dwell, 20.0)
            monitor.update_setting("warnwait_180", "7")
            monitor.update_setting("dwell_inc", "7")
            self.assertEqual(monitor.alert_dwell, 25.0)
            self.assertEqual(monitor.warning_dwell, 180.0)

    def test_corrupted_saved_warning_uses_existing_dwell(self):
        """Bad DB value must not prevent monitoring on restart."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = live_style_monitor(
                database_path=Path(folder) / "corrupt.sqlite3"
            )
            monitor.storage.initialize()
            monitor.storage.set_meta("telegram_setting_warning_dwell", "NaN")
            monitor.load_runtime_alert_settings()
            self.assertEqual(monitor.warning_dwell, 15.0)

    def test_invalid_admin_choice_is_rejected_without_writes(self):
        """Only intended choices may change warning-only timing."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = live_style_monitor(
                database_path=Path(folder) / "actions.sqlite3"
            )
            monitor.storage.initialize()
            with self.assertRaisesRegex(ValueError, "choice"):
                monitor.update_setting("warnwait_9999", "7")
            self.assertIsNone(
                monitor.storage.get_meta("telegram_setting_warning_dwell")
            )

    def test_all_locales_include_new_controls(self):
        """Warning confirmation is visible in each supported language."""

        for lang in ("ru", "uk", "en"):
            with self.subTest(lang=lang):
                monitor = live_style_monitor(telegram_language=lang)
                self.assertIn("15", monitor.settings_voltage_text())
                buttons = monitor.voltage_setting_rows()
                commands = [
                    item["callback_data"] for row in buttons for item in row
                ]
                self.assertTrue(
                    any(value.endswith("warnwait_180") for value in commands)
                )


if __name__ == "__main__":
    unittest.main()
