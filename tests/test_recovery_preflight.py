"""Conservative, offline-only checks for natural Shelly restoration."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/recovery-preflight.py"
SPEC = importlib.util.spec_from_file_location("recovery_preflight", SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)

LOSS = "2026-11-06T20:31:07+02:00"
RESTORE = "2026-11-06T21:02:00+02:00"


def when(value: str) -> float:
    """Convert an explicit Kyiv fixture timestamp to epoch seconds."""

    return datetime.fromisoformat(value).timestamp()


class RecoveryPreflightTests(unittest.TestCase):
    """Reject stale, fabricated or unrelated recovery claims."""

    def setUp(self):
        """Build isolated event history without any real network access."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.baseline = root / "baseline.json"
        self.database = root / "voltage.sqlite3"
        self.fixture = {
            "kind": "recovery_baseline",
            "status": "pass",
            "errors": [],
            "state": {
                "outage": {
                    "id": 15,
                    "started_at": when(LOSS),
                    "ended_at": None,
                    "phase": "L1",
                },
                "measurement_count": 1,
                "quick_check": "ok",
                "zero_voltage_rows": 0,
                "status_message_fingerprint": "dummy-sha",
            },
        }
        self.save_baseline()
        self.create_database()

    def save_baseline(self):
        """Write synthetic baseline data only into temporary test storage."""

        self.baseline.write_text(json.dumps(self.fixture))

    def create_database(self):
        """Create a tiny test database with an open real-like event."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.executescript(
                "create table outages (id integer primary key, "
                "started_at real, ended_at real, phase text);"
                "create table measurements (ts real, phase text, "
                "voltage real, frequency real);"
            )
            db.execute(
                "insert into outages values (?,?,?,?)",
                (15, when(LOSS), None, "L1"),
            )
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when("2026-11-06T20:30:25+02:00"), "L1", 234.8, 50),
            )

    def add_valid_recovery(self):
        """Close connectivity and add a real-looking voltage measurement."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "update outages set ended_at=? where id=15",
                (when(RESTORE),),
            )
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when("2026-11-06T21:02:03+02:00"), "L1", 230.1, 50),
            )

    def inspect(self, now: str):
        """Run the same baseline, DB and policy functions used by CLI."""

        baseline = PREFLIGHT.load_baseline(self.baseline)
        state = PREFLIGHT.read_live(self.database, "L1")
        return PREFLIGHT.classify(baseline, state, when(now), 45)

    def test_open_gap_returns_pending_not_failure(self):
        """Scheduled outage and unreachable Shelly is an expected state."""

        state = self.inspect("2026-11-06T21:05:00+02:00")
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["reason"], "shelly_connectivity_loss_open")
        self.assertIsNone(state["current_voltage_v"])
        self.assertFalse(state["mains_restoration_verified"])

    def test_real_incident_id_preserved(self):
        """Preflight must evaluate the captured event specifically."""

        state = self.inspect("2026-11-06T21:06:00+02:00")
        self.assertEqual(state["incident_id"], 15)

    def test_natural_valid_recovery_is_only_candidate(self):
        """Fresh voltage permits full verification, not instant success."""

        self.add_valid_recovery()
        state = self.inspect("2026-11-06T21:02:10+02:00")
        self.assertEqual(state["status"], "candidate")
        self.assertEqual(state["current_voltage_v"], 230.1)
        self.assertFalse(state["mains_restoration_verified"])
        self.assertFalse(state["telegram_delivery_verified"])

    def test_closed_gap_without_new_measurement_is_pending(self):
        """A connectivity flag alone is insufficient to prove new readings."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "update outages set ended_at=? where id=15",
                (when(RESTORE),),
            )
        state = self.inspect("2026-11-06T21:02:10+02:00")
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["reason"], "no_new_samples_after_baseline")

    def test_stale_recovery_reading_stays_pending(self):
        """A valid sample from ten minutes ago is not current voltage."""

        self.add_valid_recovery()
        state = self.inspect("2026-11-06T21:12:10+02:00")
        self.assertEqual(state["status"], "pending")
        self.assertIsNone(state["current_voltage_v"])

    def test_newest_invalid_frequency_does_not_use_older_good_sample(self):
        """A later 5 Hz reading must revoke a recovery candidacy."""

        self.add_valid_recovery()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when("2026-11-06T21:02:07+02:00"), "L1", 230.0, 5),
            )
        state = self.inspect("2026-11-06T21:02:10+02:00")
        self.assertEqual(state["status"], "pending")
        self.assertEqual(state["reason"], "latest_phase_sample_invalid")
        self.assertIsNone(state["current_voltage_v"])

    def test_newest_impossible_voltage_remains_pending(self):
        """An impossible 900 V report is not evidence of power recovery."""

        self.add_valid_recovery()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when("2026-11-06T21:02:07+02:00"), "L1", 900, 50),
            )
        self.assertEqual(
            self.inspect("2026-11-06T21:02:10+02:00")["status"],
            "pending",
        )

    def test_latest_null_and_infinite_voltage_remain_pending(self):
        """Reject invalid numeric fields without raising exceptions."""

        for value in (None, float("inf")):
            with self.subTest(voltage=value):
                self.add_valid_recovery()
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute(
                        "insert into measurements values (?,?,?,?)",
                        (when("2026-11-06T21:02:07+02:00"), "L1", value, 50),
                    )
                state = self.inspect("2026-11-06T21:02:10+02:00")
                self.assertEqual(state["status"], "pending")
                self.assertIsNone(state["current_voltage_v"])
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute(
                        "delete from measurements where ts=?",
                        (when("2026-11-06T21:02:07+02:00"),),
                    )

    def test_unrelated_l2_bad_measurement_does_not_hide_good_l1(self):
        """The captured phase drives preflight, not a different meter."""

        self.add_valid_recovery()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when("2026-11-06T21:02:07+02:00"), "L2", 900, 5),
            )
        self.assertEqual(
            self.inspect("2026-11-06T21:02:10+02:00")["status"],
            "candidate",
        )

    def test_bad_but_plausible_voltage_is_connectivity_candidate(self):
        """Preflight verifies a working meter, not normal grid quality."""

        self.add_valid_recovery()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "update measurements set voltage=185 "
                "where ts=? and phase='L1'",
                (when("2026-11-06T21:02:03+02:00"),),
            )
        state = self.inspect("2026-11-06T21:02:10+02:00")
        self.assertEqual(state["status"], "candidate")
        self.assertEqual(state["current_voltage_v"], 185.0)
        self.assertFalse(state["mains_restoration_verified"])

    def test_newer_unrelated_event_is_blocked(self):
        """An extra incident must not be misidentified as the captured one."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into outages values (?,?,?,?)",
                (16, when(RESTORE), None, "L1"),
            )
        baseline = PREFLIGHT.load_baseline(self.baseline)
        live = PREFLIGHT.read_live(self.database, "L1")
        with self.assertRaisesRegex(ValueError, "match"):
            PREFLIGHT.classify(
                baseline, live, when("2026-11-06T21:03:00+02:00"), 45
            )

    def test_synthetic_zero_voltage_is_blocked(self):
        """No fake 0 V rows may enter production without QA rejection."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when(RESTORE), "L1", 0, 50),
            )
        baseline = PREFLIGHT.load_baseline(self.baseline)
        state = PREFLIGHT.read_live(self.database, "L1")
        with self.assertRaisesRegex(ValueError, "zero"):
            PREFLIGHT.classify(
                baseline, state, when("2026-11-06T21:03:00+02:00"), 45
            )

    def test_old_valid_measurement_does_not_count_as_recovery(self):
        """A new row from another phase must not verify L1 recovery."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "update outages set ended_at=? where id=15", (when(RESTORE),)
            )
            db.execute(
                "insert into measurements values (?,?,?,?)",
                (when(RESTORE), "L2", 230.1, 50),
            )
        state = self.inspect("2026-11-06T21:02:15+02:00")
        self.assertEqual(state["status"], "pending")
        self.assertIsNone(state["current_voltage_v"])

    def test_recovery_timestamp_in_future_is_blocked(self):
        """The requested observation must not precede a recorded recovery."""

        self.add_valid_recovery()
        baseline = PREFLIGHT.load_baseline(self.baseline)
        state = PREFLIGHT.read_live(self.database, "L1")
        with self.assertRaisesRegex(ValueError, "timestamp"):
            PREFLIGHT.classify(
                baseline, state, when("2026-11-06T21:01:00+02:00"), 45
            )

    def test_invalid_baseline_status_blocked(self):
        """A failed baseline is never silently treated as accepted."""

        self.fixture["status"] = "fail"
        self.save_baseline()
        with self.assertRaisesRegex(ValueError, "PASS"):
            PREFLIGHT.load_baseline(self.baseline)

    def test_no_baseline_status_fingerprint_blocked(self):
        """Require a saved status fingerprint for later Telegram QA."""

        self.fixture["state"]["status_message_fingerprint"] = ""
        self.save_baseline()
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            PREFLIGHT.load_baseline(self.baseline)

    def test_symlink_baseline_rejected(self):
        """Do not follow an unexpected baseline symlink."""

        target = self.baseline.with_name("real.json")
        self.baseline.rename(target)
        self.baseline.symlink_to(target)
        with self.assertRaises(ValueError):
            PREFLIGHT.load_baseline(self.baseline)

    def test_missing_database_does_not_get_created(self):
        """Do not use normal SQLite connect and create a file by accident."""

        self.database.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            PREFLIGHT.read_live(self.database, "L1")
        self.assertFalse(self.database.exists())

    def test_naive_observation_rejected(self):
        """Do not silently adopt host timezone for event acceptance."""

        with self.assertRaisesRegex(ValueError, "timezone"):
            PREFLIGHT.observation_clock("2026-11-06T21:00:00")

    def test_cli_json_does_not_modify_source_database(self):
        """Run the CLI against original fixture bytes without writes."""

        before = self.database.read_bytes()
        args = [
            "recovery-preflight",
            "--baseline",
            str(self.baseline),
            "--db",
            str(self.database),
            "--at",
            "2026-11-06T21:06:00+02:00",
            "--json",
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(output):
            status = PREFLIGHT.main()
        report = json.loads(output.getvalue())
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "pending")
        self.assertEqual(before, self.database.read_bytes())

    def test_help_is_available(self):
        """Every administrative script supports -h and --help."""

        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", ["recovery-preflight", "--help"]),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as outcome,
        ):
            PREFLIGHT.parser().parse_args()
        self.assertEqual(outcome.exception.code, 0)
        self.assertIn("--baseline", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
