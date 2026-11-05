"""Cached readings must never be described as new voltage measurements."""

from __future__ import annotations

import time
import unittest

from test_app import APP, config


class CachedVoltageTests(unittest.TestCase):
    """Ensure restart and recovery states have truthful labels."""

    def test_cached_ukrainian_voltage_is_marked_stale(self):
        """A restored measurement is saved data, not current voltage."""

        monitor = APP.Monitor(config(telegram_language="uk"))
        monitor.state.restore(time.time() - 600, 225.9, 50.0)
        line = APP.phase_line(monitor, "L1")
        self.assertIn("L: 225.9 В", line)
        self.assertIn("збережено", line)
        self.assertIn("очікуємо вимір", line)

    def test_cached_labels_are_localized(self):
        """Never mix a Russian or Ukrainian cached label into English."""

        cases = (
            ("ru", "сохранено"),
            ("uk", "збережено"),
            ("en", "cached"),
        )
        for language, word in cases:
            with self.subTest(language=language):
                monitor = APP.Monitor(config(telegram_language=language))
                monitor.state.restore(1000, 225.9, 50.0)
                self.assertIn(word, APP.phase_line(monitor, "L1"))

    def test_successful_poll_replaces_cached_label(self):
        """Only a fresh successful RPC may remove the cached marker."""

        monitor = APP.Monitor(config(telegram_language="uk"))
        monitor.state.restore(time.time() - 1000, 225.9, 50.0)
        self.assertIn("збережено", APP.phase_line(monitor, "L1"))
        monitor.state.success(227.2, 50.0, 1.0, 1)
        line = APP.phase_line(monitor, "L1")
        self.assertIn("L: 227.2 В", line)
        self.assertNotIn("збережено", line)

    def test_offline_hides_cached_voltage_as_live(self):
        """A lost Shelly connection never reports a current cached value."""

        monitor = APP.Monitor(config(telegram_language="uk"))
        monitor.state.restore(time.time() - 1000, 225.9, 50.0)
        monitor.state.error("unreachable", offline_after=1)
        line = APP.phase_line(monitor, "L1")
        self.assertIn("недоступна", line)
        self.assertNotIn("225.9 В", line)

    def test_private_preview_labels_restored_voltage(self):
        """The admin preview uses the same cached-data truthfulness."""

        monitor = APP.Monitor(config(telegram_language="uk"))
        monitor.state.restore(time.time() - 1000, 225.9, 50.0)
        message = APP.compact_preview_text(monitor)
        self.assertIn("збережено", message)
        self.assertIn("Останні дані", message)
        self.assertIn("Очікуємо вимірювань", message)


if __name__ == "__main__":
    unittest.main()
