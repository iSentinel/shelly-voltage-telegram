import argparse
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "recovery-acceptance.py"
SPEC = importlib.util.spec_from_file_location("recovery_acceptance", SCRIPT)
RECOVERY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RECOVERY
assert SPEC.loader is not None
SPEC.loader.exec_module(RECOVERY)


def create_database(path: Path, *, open_outage: bool = True) -> None:
    """Create the minimum schema needed by the recovery checker."""

    db = sqlite3.connect(path)
    try:
        db.executescript(
            "CREATE TABLE measurements(ts REAL,phase TEXT,voltage REAL,"
            "frequency REAL,rpc_ms REAL);"
            "CREATE TABLE outages(id INTEGER PRIMARY KEY,started_at REAL,"
            "ended_at REAL,phase TEXT);"
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
        )
        db.execute("INSERT INTO measurements VALUES(1,'L1',220,50,1)")
        ended_at = None if open_outage else 2.0
        db.execute("INSERT INTO outages VALUES(7,1,?,'L1')", (ended_at,))
        db.execute(
            "INSERT INTO meta VALUES('telegram_status_message_id','682')"
        )
        db.commit()
    finally:
        db.close()


def args(path: Path) -> argparse.Namespace:
    """Return CLI-like arguments for direct function tests."""

    return argparse.Namespace(
        db=path,
        health_url="http://health",
        ready_url="http://ready",
        timeout=1.0,
        baseline=None,
        output=None,
    )


class RecoveryAcceptanceTests(unittest.TestCase):
    def test_baseline_captures_open_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            create_database(path)
            payload = RECOVERY.baseline_payload(args(path))
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(payload["state"]["outage"]["id"], 7)
            self.assertNotIn("status_message_id", payload["state"])
            self.assertEqual(
                payload["state"]["status_message_fingerprint"],
                RECOVERY.status_message_fingerprint("682"),
            )

    def test_legacy_baseline_message_id_remains_supported(self):
        baseline = {
            "state": {
                "status_message_id": "682",
                "outage": {
                    "id": 7,
                    "started_at": 1.0,
                    "ended_at": None,
                    "phase": "L1",
                },
                "measurement_count": 1,
            }
        }
        self.assertFalse(RECOVERY.status_message_rotated(baseline, "682"))
        self.assertFalse(RECOVERY.status_message_rotated(baseline, None))
        self.assertFalse(RECOVERY.status_message_rotated(baseline, ""))
        self.assertTrue(RECOVERY.status_message_rotated(baseline, "700"))

    def test_new_baseline_never_serializes_raw_message_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            create_database(path)
            payload = RECOVERY.baseline_payload(args(path))
            text = RECOVERY.json.dumps(payload)
            self.assertNotIn("status_message_id", text)
            self.assertNotIn('"682"', text)
            self.assertIn("status_message_fingerprint", text)

    def test_verify_passes_after_recovery_and_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            create_database(path)
            baseline = RECOVERY.baseline_payload(args(path))
            self._close_outage_and_rotate(path)
            verify_args = args(path)
            baseline_path = Path(directory) / "baseline.json"
            baseline_path.write_text(RECOVERY.json.dumps(baseline))
            verify_args.baseline = baseline_path
            responses = [
                (200, {"connectivity": "online"}),
                (200, {"ready": True}),
            ]
            with patch.object(RECOVERY, "fetch_json", side_effect=responses):
                payload = RECOVERY.verify_payload(verify_args)
            self.assertEqual(payload["status"], "pass")

    def test_verify_redacts_deployment_specific_health_details(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            create_database(path)
            baseline = RECOVERY.baseline_payload(args(path))
            verify_args = args(path)
            baseline_path = Path(directory) / "baseline.json"
            baseline_path.write_text(RECOVERY.json.dumps(baseline))
            verify_args.baseline = baseline_path
            marker = "private-host-marker"
            responses = [
                (
                    200,
                    {
                        "version": "0.5.4-hw",
                        "connectivity": "offline",
                        "last_error": f"cannot connect to {marker}",
                        "phases": {
                            "L1": {
                                "connectivity": "offline",
                                "last_error": marker,
                            }
                        },
                    },
                ),
                (503, {"ready": False, "reason": "shelly offline"}),
            ]
            with patch.object(RECOVERY, "fetch_json", side_effect=responses):
                payload = RECOVERY.verify_payload(verify_args)
            text = RECOVERY.json.dumps(payload)
            self.assertNotIn(marker, text)
            self.assertNotIn("last_error", text)
            self.assertNotIn("status_message_id", text)
            self.assertTrue(payload["state"]["status_message_present"])
            self.assertFalse(payload["state"]["status_message_rotated"])

    def test_outage_identity_includes_start_and_phase(self):
        baseline = {
            "state": {
                "outage": {
                    "id": 7,
                    "started_at": 1.0,
                    "ended_at": None,
                    "phase": "L1",
                },
                "measurement_count": 1,
                "status_message_fingerprint": (
                    RECOVERY.status_message_fingerprint("682")
                ),
            }
        }
        state = {
            "quick_check": "ok",
            "zero_voltage_rows": 0,
            "measurement_count": 2,
            "status_message_id": "700",
            "outage": {
                "id": 7,
                "started_at": 2.0,
                "ended_at": 3.0,
                "phase": "L2",
            },
        }
        errors = RECOVERY.recovery_errors(
            baseline, state, {"connectivity": "online"}
        )
        self.assertIn(
            "latest outage does not match the captured outage", errors
        )

    def test_verify_fails_while_outage_is_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "db.sqlite3"
            create_database(path)
            baseline = RECOVERY.baseline_payload(args(path))
            errors = RECOVERY.recovery_errors(
                baseline,
                RECOVERY.current_db_state(path),
                {"connectivity": "offline"},
            )
            self.assertIn("captured outage is still open", errors)
            self.assertIn(
                "stable Telegram status message was not rotated", errors
            )

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"
            with self.assertRaises(ValueError):
                RECOVERY.current_db_state(path)
            self.assertFalse(path.exists())

    def _close_outage_and_rotate(self, path: Path) -> None:
        """Simulate the database state after a valid recovery."""

        db = sqlite3.connect(path)
        try:
            db.execute("UPDATE outages SET ended_at=20 WHERE id=7")
            db.execute("INSERT INTO measurements VALUES(21,'L1',225,50,1)")
            db.execute(
                "UPDATE meta SET value='700' "
                "WHERE key='telegram_status_message_id'"
            )
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
