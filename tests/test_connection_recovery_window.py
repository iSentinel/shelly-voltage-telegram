"""Natural Shelly network recovery requires 15 real sampled minutes."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/connection-recovery-window.py"
)
SPEC = importlib.util.spec_from_file_location("connection_window", SCRIPT)
WINDOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WINDOW)

HEALTH = (200, {"connectivity": "online"})
READY = (200, {"ready": True})
START = 800.0
END = 1000.0
NOW = 1901.0


class ConnectionRecoveryWindowTests(unittest.TestCase):
    """Exercise original baseline and read-only recovery evidence."""

    def setUp(self):
        """Create a disposable incident 15 with regular phase L1 samples."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.db = folder / "voltage.sqlite3"
        self.baseline = folder / "baseline.json"
        self.accepted = folder / "accepted.json"
        self.event = {
            "id": 15,
            "started_at": START,
            "ended_at": END,
            "phase": "L1",
        }
        self.make_database()
        self.make_evidence()

    def make_database(self):
        """Insert one genuine-looking event and periodic 10-second readings."""

        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript(
                "create table outages (id integer primary key,"
                "started_at real,ended_at real,phase text);"
                "create table measurements (ts real,phase text,"
                "voltage real,frequency real);"
            )
            conn.execute(
                "insert into outages values(?,?,?,?)",
                (15, START, END, "L1"),
            )
            conn.executemany(
                "insert into measurements values (?, 'L1', 230, 50)",
                [(stamp,) for stamp in range(1000, 1910, 10)],
            )
            conn.execute(
                "insert into measurements values(1901, 'L1', 230, 50)"
            )

    def make_evidence(self):
        """Use fake accepted metadata, never actual Telegram details."""

        original = {
            "kind": "recovery_baseline",
            "status": "pass",
            "errors": [],
            "state": {
                "outage": {
                    "id": 15,
                    "started_at": START,
                    "ended_at": None,
                    "phase": "L1",
                }
            },
        }
        verified = {
            "kind": "recovery_verification",
            "status": "pass",
            "errors": [],
            "state": {
                "outage": self.event,
                "status_message_rotated": True,
            },
            "health": {"connectivity": "online"},
            "ready": {"ready": True},
        }
        self.baseline.write_text(json.dumps(original))
        self.accepted.write_text(json.dumps(verified))

    def execute(self, sql: str):
        """Mutate only this disposable fixture to emulate a broken state."""

        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(sql)

    def verify(self, now: float = NOW) -> dict:
        """Evaluate the same SQLite path as the real operator CLI."""

        incident = WINDOW.accepted_incident(
            WINDOW.read_evidence(self.baseline),
            WINDOW.read_evidence(self.accepted),
        )
        state = WINDOW.read_state(self.db, incident, now)
        return WINDOW.evaluate(incident, state, HEALTH, READY, now)

    def test_regular_same_phase_measurements_pass_after_15_minutes(self):
        """A complete sampled interval permits connection stability PASS."""

        report = self.verify()
        self.assertEqual(report["status"], "pass")
        self.assertLessEqual(report["max_sample_gap_seconds"], 90)
        self.assertFalse(report["subscriber_visible_telegram_delivery"])

    def test_early_window_is_pending(self):
        """Nine sampled minutes are not yet fifteen minutes."""

        self.execute("delete from measurements where ts>1540")
        report = self.verify(1540.0)
        self.assertEqual(report["status"], "pending")

    def test_missing_measurement_gap_is_blocked(self):
        """A four-minute gap is not stable observation evidence."""

        self.execute("delete from measurements where ts>=1200 and ts<=1440")
        self.assertEqual(self.verify()["status"], "blocked")

    def test_wrong_phase_does_not_replace_l1(self):
        """L2 has no power to fill missing samples of captured L1."""

        self.execute("update measurements set phase='L2' where ts>1100")
        self.assertEqual(self.verify()["status"], "blocked")

    def test_stale_current_voltage_is_blocked(self):
        """Old voltage is not current when meter stops reporting."""

        self.execute("delete from measurements where ts>1800")
        self.assertEqual(self.verify()["status"], "blocked")

    def test_new_outage_blocks_same_recovery_evidence(self):
        """Incident 16 after restoration invalidates continuous uptime."""

        self.execute("insert into outages values(16,1800,null,'L1')")
        self.assertEqual(self.verify()["status"], "blocked")

    def test_synthetic_zero_voltage_blocks_acceptance(self):
        """A fabricated voltage row cannot appear in a PASS data set."""

        self.execute("insert into measurements values(1790,'L1',0,50)")
        self.assertEqual(self.verify()["status"], "blocked")

    def test_future_dated_measurement_blocks_acceptance(self):
        """A future sample indicates clock corruption, not stable supply."""

        self.execute("insert into measurements values(2100,'L1',230,50)")
        self.assertEqual(self.verify()["status"], "blocked")

    def test_bad_baseline_status_does_not_authorize_acceptance(self):
        """Only accepted original event baselines may trigger verification."""

        data = json.loads(self.baseline.read_text())
        data["status"] = "fail"
        self.baseline.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.verify()

    def test_recovery_identity_mismatch_is_rejected(self):
        """Unrelated closed gap 14 cannot authorize incident 15."""

        data = json.loads(self.accepted.read_text())
        data["state"]["outage"]["id"] = 14
        self.accepted.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.verify()

    def test_cli_reports_pass_with_no_file_writes(self):
        """CLI uses temporary DB, mocked endpoints, and a real clock mock."""

        before = self.db.read_bytes()
        flags = [
            "window",
            "--db",
            str(self.db),
            "--baseline",
            str(self.baseline),
            "--verification",
            str(self.accepted),
            "--json",
        ]
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", flags),
            patch.object(WINDOW.time, "time", return_value=NOW),
            patch.object(
                WINDOW, "fetch_endpoint", side_effect=[HEALTH, READY]
            ),
            redirect_stdout(stdout),
        ):
            exit_code = WINDOW.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "pass")
        self.assertEqual(before, self.db.read_bytes())

    def test_help_option_is_available(self):
        """Administrative command always supports -h and --help."""

        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                with self.assertRaises(SystemExit) as stopped:
                    WINDOW.parser().parse_args([flag])
                self.assertEqual(stopped.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
