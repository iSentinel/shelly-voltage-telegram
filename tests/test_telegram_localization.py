"""Regression tests for every Telegram language and the compact status."""

from __future__ import annotations

import json
import string
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config

import telegram_i18n as I18N


def template_fields(template: str) -> set[str]:
    """Collect field names without including format specifiers."""

    return {
        name
        for _, name, _, _ in string.Formatter().parse(template)
        if name is not None
    }


def ukrainian_monitor(path: Path | None = None) -> APP.Monitor:
    """Create a real monitor without connecting to physical hardware."""

    options = {"telegram_language": "uk"}
    if path is not None:
        options["database_path"] = path
    return APP.Monitor(config(**options))


class TelegramLocaleTests(unittest.TestCase):
    """Ensure a selected language never silently falls back to Russian."""

    def test_all_languages_have_identical_keys(self):
        """Every translated message must exist in all three languages."""

        expected = set(I18N._TEXT["ru"])
        self.assertGreater(len(expected), 175)
        for lang in I18N.LANGUAGES:
            with self.subTest(lang=lang):
                self.assertEqual(set(I18N._TEXT[lang]), expected)

    def test_translation_fields_match_across_languages(self):
        """Format placeholders must be available in all translations."""

        for key, template in I18N._TEXT["ru"].items():
            expected = template_fields(template)
            for lang in ("uk", "en"):
                with self.subTest(language=lang, key=key):
                    actual = template_fields(I18N._TEXT[lang][key])
                    self.assertEqual(actual, expected)

    def test_missing_translation_fails_instead_of_using_russian(self):
        """A forgotten translation should be caught during tests."""

        with self.assertRaises(KeyError):
            I18N.tr("uk", "nonexistent_translation_key")

    def test_ukrainian_has_no_known_foreign_ui_fragments(self):
        """Reject regression to the mixed-language user screenshot."""

        bad = (
            "отключений",
            "outage ",
            "avg ",
            "Cooldown",
            "Outage alerts",
            "Pin status",
            "Test loud",
            "Test silent",
            "Maintenance:",
            "Dwell:",
        )
        for key, value in I18N._TEXT["uk"].items():
            for word in bad:
                with self.subTest(key=key, fragment=word):
                    self.assertNotIn(word, value)

    def test_ukrainian_summary_uses_ukrainian_labels(self):
        """The daily summary must not inherit Russian outage wording."""

        monitor = ukrainian_monitor()
        monitor.storage.outage_stats = Mock(
            return_value={"count": 4, "duration": 48300.0}
        )
        stats = {"min": 117.5, "max": 251.5, "avg": 219.1}
        with patch.object(APP, "phase_stats", return_value=stats):
            message = monitor.daily_summary_text()
        self.assertIn("Добове зведення", message)
        self.assertIn("перерв зв'язку: 4", message)
        self.assertIn("13г 25хв", message)
        self.assertNotIn("отключений", message)
        self.assertNotIn("avg ", message)

    def test_ukrainian_status_shows_warning_and_frequency(self):
        """A warning voltage must not be presented as healthy green."""

        monitor = ukrainian_monitor()
        monitor.state.success(199.0, 50.0, 1.0, 1)
        monitor.storage.day_stats = Mock(
            return_value={
                "count": 12,
                "min": 117.5,
                "max": 251.5,
                "avg": 219.1,
            }
        )
        message = APP.multiphase_status_text(monitor)
        self.assertIn("🟡 L: 199.0 В", message)
        self.assertIn("Частота: 50.00 Гц", message)
        self.assertIn("сер. 219.1 В", message)
        self.assertNotIn("avg ", message)

    def test_offline_status_never_invents_zero_voltage(self):
        """Unreachable Shelly must display unknown current voltage."""

        monitor = ukrainian_monitor()
        monitor.state.success(225.9, 50.0, 1.0, 1)
        monitor.state.error("unreachable", 1)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 225.9, "max": 225.9, "avg": 225.9}
        )
        message = APP.multiphase_status_text(monitor)
        self.assertIn("поточна напруга невідома", message)
        self.assertNotIn("🟢 L:", message)
        self.assertNotIn("0.0 В", message)

    def test_event_timeline_reads_true_events(self):
        """Expose saved readings and connectivity events, not guesses."""

        with tempfile.TemporaryDirectory() as directory:
            monitor = ukrainian_monitor(Path(directory) / "event.sqlite3")
            monitor.storage.initialize()
            now = 1790000000.0
            changes = [
                {"state": "low", "phase": "L1", "at": now - 120},
                {"state": "normal", "phase": "L1", "at": now - 60},
            ]
            monitor.storage.set_meta(
                "voltage_transition_history", json.dumps(changes)
            )
            monitor.storage.begin_outage(now - 800)
            monitor.storage.end_outage(now - 400)
            with patch.object(APP.time, "time", return_value=now):
                message = monitor.event_timeline_text()
            self.assertIn("знижена напруга", message)
            self.assertIn("втрата зв'язку", message)
            self.assertIn("зв'язок відновлено", message)
            self.assertIn("зазвичай 1 год", message)

    def test_event_timeline_does_not_invent_missing_data(self):
        """Unrecorded voltage events cannot appear in the daily timeline."""

        with tempfile.TemporaryDirectory() as directory:
            monitor = ukrainian_monitor(Path(directory) / "empty.sqlite3")
            monitor.storage.initialize()
            monitor.storage.set_meta("voltage_transition_history", "broken")
            message = monitor.event_timeline_text()
            self.assertIn("немає", message)
            self.assertNotIn("знижена напруга", message)

    def test_notification_buttons_are_localized(self):
        """Never offer an English cooldown label in the Ukrainian menu."""

        monitor = ukrainian_monitor()
        labels = [
            button["text"]
            for row in monitor.notification_policy_rows()
            for button in row
        ]
        self.assertTrue(any("хв" in label for label in labels))
        self.assertFalse(any("Cooldown" in label for label in labels))

    def test_menu_includes_events_and_summary(self):
        """The private control menu must contain both new read-only views."""

        monitor = ukrainian_monitor()
        buttons = [
            button["text"]
            for row in monitor.main_menu_keyboard()["inline_keyboard"]
            for button in row
        ]
        self.assertTrue(any("Події" in value for value in buttons))
        self.assertTrue(any("Зведення" in value for value in buttons))

    def test_russian_summary_does_not_contain_english_labels(self):
        """The Russian summary must not use avg or mixed outage terms."""

        monitor = APP.Monitor(config(telegram_language="ru"))
        monitor.storage.outage_stats = Mock(
            return_value={"count": 4, "duration": 48300.0}
        )
        stats = {"min": 117.5, "max": 251.5, "avg": 219.1}
        with patch.object(APP, "phase_stats", return_value=stats):
            message = monitor.daily_summary_text()
        self.assertIn("ср. 219.1 V", message)
        self.assertIn("потерь связи: 4", message)
        self.assertNotIn("avg ", message)
        self.assertNotIn("отключений 4", message)

    def test_maintenance_is_localized_in_russian(self):
        """Technical maintenance notifications should not mix languages."""

        warning = I18N.tr("ru", "maintenance_admin_only")
        status = I18N.tr("ru", "maintenance_status", value="30 мин")
        self.assertIn("режим обслуживания", warning)
        self.assertIn("Обслуживание", status)
        self.assertNotIn("maintenance mode", warning)
        self.assertNotIn("Maintenance:", status)

    def test_help_lists_new_events_command_in_every_language(self):
        """Expose the same read-only timeline feature in each language."""

        for language in I18N.LANGUAGES:
            with self.subTest(language=language):
                self.assertIn("/events", I18N.tr(language, "help"))

    def test_ukrainian_device_diagnostics_and_voltage_units(self):
        """Display local voltage and clock units in Ukrainian settings."""

        monitor = ukrainian_monitor()
        self.assertTrue(monitor.voltage_range_text().endswith(" В"))
        self.assertEqual(
            APP.localized_clock_skew("uk", None), "немає даних"
        )
        self.assertEqual(APP.localized_clock_skew("uk", 1.25), "+1.2с")
        self.assertIn("увімкн.", I18N.tr(
            "uk", "settings_phases", l2="вимкн.", l3="вимкн."
        ))



if __name__ == "__main__":
    unittest.main()
