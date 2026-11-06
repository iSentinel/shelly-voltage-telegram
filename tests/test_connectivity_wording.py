"""Ensure device connectivity gaps are not described as power cuts."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_app import APP, config

from telegram_i18n import tr


def monitor_for(language: str, path: Path) -> APP.Monitor:
    """Create an offline-only test monitor without Telegram network IO."""

    return APP.Monitor(
        config(
            telegram_language=language,
            database_path=path,
        )
    )


class ConnectivityWordingTests(unittest.TestCase):
    """Protect against false power-outage claims after router faults."""

    def test_outage_navigation_uses_connection_language(self):
        """Commands and buttons must not claim measured mains failures."""

        expected = {
            "ru": "Потери связи",
            "uk": "Втрати зв’язку",
            "en": "Connection losses",
        }
        for lang, phrase in expected.items():
            with self.subTest(lang=lang):
                self.assertIn(phrase.lower(), tr(lang, "menu_outages").lower())
                self.assertIn(phrase.lower(), tr(lang, "cmd_outages").lower())

    def test_history_labels_are_not_power_cut_assertions(self):
        """Show the stored outage journal as availability history."""

        expected = {
            "ru": "потери связи",
            "uk": "втрати зв'язку",
            "en": "connection losses",
        }
        empty = {
            "ru": "потерь связи нет",
            "uk": "втрат зв'язку немає",
            "en": "no connection losses",
        }
        for lang, phrase in expected.items():
            with self.subTest(lang=lang):
                text = tr(lang, "outages_title", phase="L1")
                self.assertIn(phrase, text.lower())
                self.assertIn(
                    empty[lang],
                    tr(lang, "no_outages", phase="L1").lower(),
                )

    def test_daily_counts_are_connectivity_counts(self):
        """The journal does not prove a utility distribution outage."""

        expected = {
            "ru": "потерь связи",
            "uk": "втрат зв'язку",
            "en": "connection losses",
        }
        for lang, phrase in expected.items():
            with self.subTest(lang=lang):
                rendered = tr(lang, "outage_count_today", count=14)
                self.assertIn(phrase, rendered.lower())
                self.assertIn("14", rendered)

    def test_recovered_sensor_is_not_mains_recovery(self):
        """A successful reconnection means only connectivity returned."""

        bad = {
            "ru": "питание/датчик",
            "uk": "живлення відновлено",
            "en": "power restored",
        }
        for lang, fragment in bad.items():
            with self.subTest(lang=lang):
                text = tr(
                    lang,
                    "phase_sensor_recovered",
                    phase="L1",
                    duration="45с",
                )
                self.assertNotIn(fragment.lower(), text.lower())

    def test_offline_availability_never_invents_voltage(self):
        """A network failure can leave the live voltage unknown."""

        expected = {
            "ru": "недоступен",
            "uk": "зв'язку",
            "en": "unreachable",
        }
        for lang, word in expected.items():
            with self.subTest(lang=lang):
                text = tr(lang, "availability_offline")
                self.assertIn(word.lower(), text.lower())
                self.assertNotIn("0.0", text)

    def test_status_with_cached_voltage_does_not_claim_live_power(self):
        """Offline rendering must retain the unknown-current-voltage label."""

        expected = {
            "ru": "неизвестно",
            "uk": "невідома",
            "en": "unknown",
        }
        with tempfile.TemporaryDirectory() as folder:
            for lang, phrase in expected.items():
                with self.subTest(lang=lang):
                    monitor = monitor_for(
                        lang, Path(folder) / (lang + ".sqlite3")
                    )
                    monitor.state.restore(time.time() - 60, 220.0, 50.0)
                    monitor.state.error("router unreachable", 1)
                    text = APP.compact_preview_text(monitor)
                    self.assertIn(phrase, text.lower())
                    self.assertNotIn("0.0 В", text)

    def test_rendered_history_includes_connectivity_disclaimer(self):
        """The user-facing history includes source uncertainty."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = monitor_for("ru", Path(folder) / "history.sqlite3")
            with patch.object(
                APP,
                "outage_history",
                return_value=[(time.time() - 45, time.time())],
            ):
                text = monitor.outage_history_text("L1")
            self.assertIn("потери связи", text.lower())
            self.assertIn("не всегда означает", text.lower())

    def test_no_history_uses_connection_loss_wording(self):
        """Zero events means zero connectivity gaps, not powered uptime."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = monitor_for("uk", Path(folder) / "empty.sqlite3")
            with patch.object(APP, "outage_history", return_value=[]):
                text = monitor.outage_history_text("L1")
            self.assertIn("втрат зв'язку немає", text.lower())


if __name__ == "__main__":
    unittest.main()
