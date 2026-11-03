"""Regression tests for the 0.5.8 Telegram usability features."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class LanguageSettingTests(unittest.TestCase):
    """Validate persisted RU/UA/EN Telegram UI selection."""

    def test_language_setting_persists_and_applies_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "language.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            monitor.telegram.set_commands = Mock()
            monitor.update_status = Mock()
            monitor.update_setting("lang_uk", "7")
            self.assertEqual(monitor.language, "uk")
            self.assertIn("🌐 Мова: UA", monitor.settings_text())
            monitor.telegram.set_commands.assert_called_once_with("uk")
            restarted = APP.Monitor(config(database_path=path))
            restarted.storage.initialize()
            restarted.load_runtime_settings()
            self.assertEqual(restarted.language, "uk")

    def test_english_help_and_status_are_localized(self):
        monitor = APP.Monitor(config(telegram_language="en"))
        monitor.state.restore(1000.0, 230.0, 50.0)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 230.0, "max": 230.0, "avg": 230.0}
        )
        self.assertTrue(monitor.command_text("/help").startswith("Commands:"))
        text = APP.multiphase_status_text(monitor)
        self.assertIn("Grid voltage", text)
        self.assertIn("L: 230.0 V", text)
        self.assertNotIn("L2", text)


class NotificationTestTests(unittest.TestCase):
    """Validate explicit delivery tests without changing alert cooldown."""

    def test_loud_silent_and_edit_tests_use_private_control_chat(self):
        monitor = APP.Monitor(
            config(telegram_token="token", telegram_chat_id="123")
        )
        monitor.storage.initialize()
        monitor.telegram.send_test = Mock(side_effect=[11, 12, 13])
        monitor.telegram.edit = Mock()
        before = monitor.storage.get_meta("last_voltage_notification_at")
        monitor.run_notification_test("test_loud")
        monitor.run_notification_test("test_silent")
        monitor.run_notification_test("test_edit")
        calls = monitor.telegram.send_test.call_args_list
        self.assertTrue(calls[0].args[1])
        self.assertFalse(calls[1].args[1])
        self.assertFalse(calls[2].args[1])
        monitor.telegram.edit.assert_called_once()
        after = monitor.storage.get_meta("last_voltage_notification_at")
        self.assertEqual(before, after)


class EpisodeSummaryTests(unittest.TestCase):
    """Validate final event summaries built from persisted samples."""

    def _episode_monitor(self, path: Path) -> APP.Monitor:
        monitor = APP.Monitor(
            config(database_path=path, voltage_status_edit_window_min=3)
        )
        monitor.storage.initialize()
        for ts, voltage in ((1000.0, 180.0), (1060.0, 230.0), (1100.0, 260.0)):
            monitor.storage.add(ts, voltage, 50.0, "L1")
        monitor.update_voltage_episode("critical_low", 180.0, 1000.0, "L1")
        monitor.update_voltage_episode("recovered", 230.0, 1060.0, "L1")
        monitor.update_voltage_episode("warning_high", 260.0, 1100.0, "L1")
        return monitor

    def test_episode_finalizes_with_real_sqlite_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = self._episode_monitor(
                Path(directory) / "episode.sqlite3"
            )
            with patch.object(APP.time, "time", return_value=1181.0):
                monitor.maybe_finalize_voltage_episode()
            summary = monitor.voltage_episode_summary()
            self.assertEqual(summary["minimum"], "180.0")
            self.assertEqual(summary["maximum"], "260.0")
            self.assertEqual(summary["average"], "223.3")
            self.assertEqual(summary["transitions"], 3)
            self.assertEqual(summary["abnormal"], "2м 20с")

    def test_finalization_edits_existing_status_without_new_message(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit-summary.sqlite3"
            monitor = self._episode_monitor(path)
            monitor.telegram.enabled = True
            monitor.telegram.status_chat_id = "-1009"
            monitor.telegram.edit = Mock()
            monitor.telegram.send = Mock()
            monitor.storage.set_meta(monitor.status_message_key(), "55")
            monitor.state.restore(1100.0, 260.0, 50.0)
            with patch.object(APP.time, "time", return_value=1181.0):
                monitor.maybe_finalize_voltage_episode()
            monitor.telegram.edit.assert_called_once()
            self.assertEqual(monitor.telegram.edit.call_args.args[0], 55)
            monitor.telegram.send.assert_not_called()

    def test_same_direction_severity_does_not_add_transition(self):
        episode = APP.new_episode("warning_low", 205.0, 1000.0, "L1")
        episode = APP.update_episode(
            episode, "critical_low", 185.0, 1010.0, "L1", 600.0
        )
        self.assertEqual(len(episode["transitions"]), 1)


class InstabilityTests(unittest.TestCase):
    """Validate persisted transition-based instability detection."""

    def test_four_transitions_in_fifteen_minutes_show_indicator(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unstable.sqlite3"
            monitor = APP.Monitor(
                config(database_path=path, telegram_language="en")
            )
            monitor.storage.initialize()
            events = [
                ("warning_low", 205.0, 1000.0),
                ("recovered", 230.0, 1100.0),
                ("warning_high", 254.0, 1200.0),
                ("recovered", 230.0, 1250.0),
            ]
            for kind, voltage, at in events:
                monitor.update_voltage_episode(kind, voltage, at, "L1")
            monitor.state.restore(1300.0, 230.0, 50.0)
            self.assertEqual(monitor.instability_transition_count(1300.0), 4)
            self.assertIn(
                "Unstable voltage", "\n".join(APP.instability_lines(monitor))
            )
            restarted = APP.Monitor(config(database_path=path))
            restarted.storage.initialize()
            self.assertEqual(restarted.instability_transition_count(1300.0), 4)

    def test_instability_indicator_can_be_disabled(self):
        monitor = APP.Monitor(config(voltage_instability_enabled=False))
        monitor.instability_transition_count = Mock(return_value=8)
        self.assertEqual(APP.instability_lines(monitor), [])


class TranslationHelperTests(unittest.TestCase):
    """Validate direct translation helpers for all supported languages."""

    def test_all_languages_have_command_descriptions(self):
        for language in APP.LANGUAGES:
            with self.subTest(language=language):
                descriptions = APP.command_descriptions(language)
                self.assertEqual(len(descriptions), 17)
                self.assertTrue(descriptions["settings"])

    def test_episode_metadata_is_json_serializable(self):
        episode = APP.new_episode("warning_high", 254.0, 1.0, "L1")
        self.assertIn("warning_high", json.dumps(episode))


if __name__ == "__main__":
    unittest.main()
