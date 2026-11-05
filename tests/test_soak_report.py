"""Tests for the schema-aware soak report utility."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "soak-report.py"
SPEC = importlib.util.spec_from_file_location("svt_soak_report", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load soak-report.py")
SOAK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SOAK
SPEC.loader.exec_module(SOAK)


def args(path: Path, hours: float = 1.0) -> SimpleNamespace:
    """Build report arguments for a temporary database."""

    return SimpleNamespace(
        db=str(path),
        hours=hours,
        expected_interval=10.0,
        timezone="Europe/Kyiv",
        json=True,
    )


class SoakReportTests(unittest.TestCase):
    """Validate legacy and outage-aware report behavior."""

    def test_schema_v1_remains_compatible(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE measurements(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL, voltage REAL NOT NULL,
                        frequency REAL NOT NULL
                    );
                    CREATE TABLE meta(
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    INSERT INTO meta(key,value) VALUES('schema_version','1');
                    INSERT INTO measurements(ts,voltage,frequency)
                    VALUES(1900,220.0,50.0),(1910,221.0,50.0);
                    """
                )
            report = SOAK.build_report(args(path), now=2000.0)
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["samples"], 2)
            self.assertEqual(report["sample_span_hours"], round(10 / 3600, 3))
            self.assertEqual(report["last_sample_age_seconds"], 90.0)
            self.assertEqual(report["confirmed_outages"], 0)
            self.assertEqual(report["confirmed_outage_seconds"], 0.0)
            self.assertEqual(
                report["voltage_transition_history_status"], "absent"
            )

    def test_schema_v2_separates_gaps_from_confirmed_outages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE measurements(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL, voltage REAL NOT NULL,
                        frequency REAL NOT NULL
                    );
                    CREATE TABLE meta(
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    CREATE TABLE outages(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at REAL NOT NULL, ended_at REAL
                    );
                    INSERT INTO meta(key,value) VALUES('schema_version','2');
                    INSERT INTO measurements(ts,voltage,frequency)
                    VALUES(16500,220.0,50.0),(16510,221.0,50.0),
                          (17000,222.0,50.0);
                    INSERT INTO outages(started_at,ended_at)
                    VALUES(16000,17000),(19000,NULL);
                    """
                )
            report = SOAK.build_report(args(path), now=20000.0)
            self.assertEqual(report["confirmed_outages"], 2)
            self.assertEqual(report["confirmed_open_outages"], 1)
            self.assertEqual(report["confirmed_outage_seconds"], 1600.0)
            self.assertEqual(report["sample_gaps_over_threshold"], 1)
            self.assertIn(
                "diagnostic only", report["sample_gap_interpretation"]
            )
            self.assertIn("hysteresis", report["confirmed_outage_semantics"])
            self.assertIn("not exact", report["confirmed_outage_semantics"])

    def test_voltage_history_is_windowed_and_sanitized(self):
        """Include only safe, recognized voltage-state transitions."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE meta(key TEXT,value TEXT)")
                items = [
                    {"at": 1600, "phase": "L1", "state": "normal"},
                    {"at": 1800, "phase": "L1", "state": "low",
                     "device_secret": "should-not-leak"},
                    {"at": 1850, "phase": "L1", "state": "normal"},
                    {"at": 1880, "phase": "private", "state": "low"},
                    {"at": "NaN", "phase": "L1", "state": "high"},
                ]
                db.execute(
                    "INSERT INTO meta VALUES(?,?)",
                    ("voltage_transition_history", json.dumps(items)),
                )
            report = SOAK.voltage_transitions(
                path, 1700, 2000, ZoneInfo("Europe/Kyiv")
            )
            self.assertEqual(
                report["voltage_transition_history_status"], "ok"
            )
            self.assertEqual(report["voltage_transition_count"], 2)
            self.assertEqual(
                [
                    entry["state"]
                    for entry in report["voltage_transition_events"]
                ],
                ["low", "normal"],
            )
            self.assertNotIn("should-not-leak", json.dumps(report))
            self.assertIn(
                "not a complete", report["voltage_transition_interpretation"]
            )

    def test_invalid_voltage_history_is_reported(self):
        """Invalid saved history is distinct from no saved history."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE meta(key TEXT,value TEXT)")
                db.execute(
                    "INSERT INTO meta VALUES(?,?)",
                    ("voltage_transition_history", "{not valid JSON"),
                )
            report = SOAK.voltage_transitions(
                path, 1700, 2000, ZoneInfo("Europe/Kyiv")
            )
            self.assertEqual(
                report["voltage_transition_history_status"], "invalid"
            )
            self.assertEqual(report["voltage_transition_count"], 0)

    def test_missing_database_is_not_created(self):
        """A typo in --db must fail closed without creating an empty DB."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"
            with self.assertRaises(sqlite3.OperationalError):
                SOAK.build_report(args(path), now=2000.0)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
