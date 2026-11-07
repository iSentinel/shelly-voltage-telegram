"""Check localized Telegram report readability without sending messages."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class TelegramReportLayoutTests(unittest.TestCase):
    """Test bounded real measurements and localized calendar boundaries."""

    def setUp(self):
        """Create a private SQLite test database, never production data."""

        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.now = datetime.fromisoformat(
            "2026-11-07T00:10:00+02:00"
        ).timestamp()
        self.previous = datetime.fromisoformat(
            "2026-11-06T23:59:50+02:00"
        ).timestamp()
        self.today = datetime.fromisoformat(
            "2026-11-07T00:02:00+02:00"
        ).timestamp()
        self.future = datetime.fromisoformat(
            "2026-11-07T18:30:00+02:00"
        ).timestamp()
        self.db = Path(work.name) / "report.sqlite3"
        self.monitor = APP.Monitor(config(database_path=self.db))
        self.monitor.storage.initialize()
        self.monitor.telegram.send = Mock()

    def render(self, language: str, kind: str = "daily") -> str:
        """Render once with a frozen clock for a selected language."""

        self.monitor.language = language
        with patch.object(APP.time, "time", return_value=self.now):
            if kind == "weekly":
                return self.monitor.weekly_summary_text()
            return self.monitor.daily_summary_text()

    def test_three_locales_show_daily_interval_and_sample_count(self):
        """Daily report must show date, timezone, and real sample count."""

        self.monitor.storage.add(self.previous, 250.0, 50.0)
        self.monitor.storage.add(self.today, 225.0, 50.0)
        self.monitor.storage.add(self.future, 260.0, 50.0)
        for language, count_label in (
            ("ru", "Измерений: 1"),
            ("uk", "Вимірювань: 1"),
            ("en", "Samples: 1"),
        ):
            with self.subTest(language=language):
                rendered = self.render(language)
                self.assertIn("07.11.2026 00:00", rendered)
                self.assertIn("07.11.2026 00:10", rendered)
                self.assertIn("Europe/Kyiv", rendered)
                self.assertIn(count_label, rendered)
                self.assertIn("225.0", rendered)
                self.assertNotIn("250.0", rendered)
                self.assertNotIn("260.0", rendered)
        self.monitor.telegram.send.assert_not_called()

    def test_no_connectivity_loss_does_not_claim_one_occurred(self):
        """Generic guidance must never fabricate a past Shelly outage."""

        for language, forbidden in (
            ("ru", "Зафиксирована недоступность"),
            ("uk", "Зафіксовано недоступність"),
            ("en", "A connection loss was detected"),
        ):
            with self.subTest(language=language):
                rendered = self.render(language)
                self.assertNotIn(forbidden, rendered)
                self.assertIn("ℹ️", rendered)
        self.monitor.telegram.send.assert_not_called()

    def test_empty_day_shows_unknown_instead_of_fake_zero(self):
        """Missing measurements must be visible as missing, not 0 volts."""

        rendered = self.render("ru")
        self.assertIn("Измерений: 0", rendered)
        self.assertIn("неизвестно", rendered)
        self.assertNotIn("0.0–0.0", rendered)

    def test_weekly_report_has_exact_local_range(self):
        """Seven-day summary clearly says what dates were observed."""

        rendered = self.render("en", "weekly")
        self.assertIn("Europe/Kyiv", rendered)
        self.assertIn("07.11.2026 00:10", rendered)
        self.assertIn("31.10.2026 00:10", rendered)
        self.assertIn("unknown", rendered.lower())
        self.monitor.telegram.send.assert_not_called()

    def test_weekly_voltage_units_follow_ru_uk_en_locale(self):
        """Weekly voltage labels must use Cyrillic В for RU/UK only."""

        self.monitor.storage.add(self.today, 225.5, 50.0)
        for language in ("ru", "uk", "en"):
            with self.subTest(language=language):
                rendered = self.render(language, "weekly")
                measured = next(
                    row for row in rendered.splitlines() if "225.5" in row
                )
                unit = " V" if language == "en" else " В"
                self.assertIn(f"225.5{unit}", measured)
                if language != "en":
                    self.assertNotIn(" V", measured)
        self.monitor.telegram.send.assert_not_called()

    def test_report_uses_configured_timezone_not_hardcoded_kyiv(self):
        """A non-Kyiv setup must not describe its interval as Kyiv."""

        self.monitor.cfg = APP.replace(self.monitor.cfg, timezone="UTC")
        rendered = self.render("en")
        self.assertIn("UTC", rendered)
        self.assertIn("06.11.2026 22:10", rendered)
        self.assertNotIn("Europe/Kyiv", rendered)

    def test_public_status_and_private_controls_are_not_modified(self):
        """New report rendering may never alter the channel or its pins."""

        self.monitor.state.success(220.0, 50.0, 1.0, 1)
        self.monitor.language = "uk"
        old = APP.multiphase_status_text(self.monitor)
        self.monitor.telegram.edit = Mock()
        self.monitor.telegram.pin = Mock()
        _ = self.render("uk")
        current = APP.multiphase_status_text(self.monitor)
        self.assertEqual(old, current)
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
