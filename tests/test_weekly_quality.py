"""Read-only weekly voltage quality and connection reporting tests."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from test_app import APP, config

import advanced_features as QUALITY


def make_database(path: Path) -> None:
    """Create only the tables needed by the read-only aggregate queries."""

    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            "CREATE TABLE measurements("
            "ts REAL, voltage REAL, frequency REAL, phase TEXT);"
            "CREATE TABLE outages("
            "started_at REAL, ended_at REAL, phase TEXT);"
        )


class WeeklyQualityTests(unittest.TestCase):
    """Check that weekly estimates never fabricate missing measurements."""

    def test_counts_only_short_observed_intervals(self):
        """An eighty-second gap is unknown, not a valid voltage state."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quality.sqlite3"
            make_database(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.executemany(
                    "INSERT INTO measurements VALUES(?,?,50,'L1')",
                    [(100, 200), (110, 230), (140, 260), (220, 225)],
                )
            result = QUALITY.weekly_quality_stats(path, "L1", 0, 300, 207, 253)
            self.assertEqual(result["count"], 4)
            self.assertEqual(result["min"], 200)
            self.assertEqual(result["max"], 260)
            self.assertEqual(result["observed"], 40)
            self.assertEqual(result["below"], 10)
            self.assertEqual(result["above"], 0)
            self.assertEqual(result["unknown"], 260)

    def test_missing_measurements_are_not_zero_volts(self):
        """Empty datasets show unknown time and no voltage aggregates."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.sqlite3"
            make_database(path)
            result = QUALITY.weekly_quality_stats(path, "L1", 0, 300, 207, 253)
            self.assertEqual(result["count"], 0)
            self.assertIsNone(result["min"])
            self.assertIsNone(result["avg"])
            self.assertEqual(result["unknown"], 300)
            self.assertEqual(result["below"], 0)

    def test_separates_phases(self):
        """One phase cannot contribute to another phase's report."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phases.sqlite3"
            make_database(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO measurements VALUES(100,240,50,'L2')")
            result = QUALITY.weekly_quality_stats(path, "L1", 0, 300, 207, 253)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["unknown"], 300)

    def test_connection_outages_are_clipped_to_week_window(self):
        """Count only the overlapping portions of connection outages."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outages.sqlite3"
            make_database(path)
            with closing(sqlite3.connect(path)) as db, db:
                db.executemany(
                    "INSERT INTO outages VALUES(?,?,'L1')",
                    [(-20, 20), (100, None), (400, 450)],
                )
            result = QUALITY.weekly_connection_stats(path, "L1", 0, 300)
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["duration"], 220)
            other = QUALITY.weekly_connection_stats(path, "L2", 0, 300)
            self.assertEqual(other["count"], 0)

    def test_ukrainian_week_command_shows_unknown_time(self):
        """The weekly report uses the selected language and real records."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.sqlite3"
            monitor = APP.Monitor(
                config(database_path=path, telegram_language="uk")
            )
            monitor.storage.initialize()
            with closing(sqlite3.connect(path)) as db, db:
                db.executemany(
                    "INSERT INTO measurements(ts,voltage,frequency,phase) "
                    "VALUES(?,?,50,'L1')",
                    [(100, 220), (110, 230)],
                )
            with patch.object(APP.time, "time", return_value=300.0):
                report = monitor.command_text("/week")
            self.assertIn("Якість електромережі", report)
            self.assertIn("без даних", report)
            self.assertIn("Втрат зв'язку", report)
            self.assertNotIn("avg ", report)
            self.assertNotIn("відключень", report)

    def test_week_command_descriptions_exist_for_all_languages(self):
        """One new command has corresponding text in RU, UK and EN."""

        for lang in APP.LANGUAGES:
            with self.subTest(lang=lang):
                descriptions = APP.command_descriptions(lang)
                self.assertIn("week", descriptions)
                self.assertTrue(descriptions["week"])


if __name__ == "__main__":
    unittest.main()
