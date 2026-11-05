import argparse
import importlib.util
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import closing
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


def set_meta(path: Path, key: str, value: str | None) -> None:
    """Set or delete one metadata key in a test database."""

    with closing(sqlite3.connect(path)) as db:
        if value is None:
            db.execute("DELETE FROM meta WHERE key=?", (key,))
        else:
            db.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                (key, value),
            )
        db.commit()


def make_stable_recovery(path: Path, started_at: float = 1000.0) -> None:
    """Create metadata produced by one confirmed voltage recovery."""

    event = RECOVERY.json.dumps(
        {
            "kind": "recovered",
            "voltage": 230.0,
            "frequency": 50.0,
            "timestamp": started_at,
            "phase": "L1",
        }
    )
    set_meta(path, "voltage_recovery_stable_since", str(started_at))
    set_meta(path, "last_voltage_event", event)
    set_meta(path, "voltage_episode", RECOVERY.json.dumps({"start_at": 900.0}))
    set_meta(path, "last_voltage_notification_at", "777.0")


def complete_stable_rotation(path: Path) -> None:
    """Simulate successful silent replacement after stable recovery."""

    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "UPDATE meta SET value='900' "
            "WHERE key='telegram_status_message_id'"
        )
        db.execute(
            "DELETE FROM meta WHERE key='voltage_recovery_stable_since'"
        )
        db.execute("DELETE FROM meta WHERE key='last_voltage_event'")
        db.execute("DELETE FROM meta WHERE key='voltage_episode'")
        db.execute("INSERT INTO measurements VALUES(1901,'L1',230,50,1)")
        db.commit()


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

    def test_stable_baseline_redacts_message_id(self):
        """Capture active recovery state without serializing Telegram ID."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stable.sqlite3"
            create_database(path, open_outage=False)
            make_stable_recovery(path)
            responses = [
                (200, {"connectivity": "online"}),
                (200, {"ready": True}),
            ]
            with patch.object(RECOVERY, "fetch_json", side_effect=responses):
                payload = RECOVERY.stable_baseline_payload(args(path))
            self.assertEqual(payload["status"], "pass")
            self.assertNotIn("status_message_id", payload["state"])
            self.assertNotIn('"682"', RECOVERY.json.dumps(payload))
            self.assertEqual(
                payload["state"]["last_voltage_event_kind"], "recovered"
            )
            self.assertEqual(
                payload["state"]["recovery_stable_since"], "1000.0"
            )

    def test_stable_verify_passes_after_silent_rotation(self):
        """Accept a rotated plain status after the full stable period."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stable-pass.sqlite3"
            create_database(path, open_outage=False)
            make_stable_recovery(path)
            responses = [
                (200, {"connectivity": "online"}),
                (200, {"ready": True}),
            ]
            with patch.object(RECOVERY, "fetch_json", side_effect=responses):
                baseline = RECOVERY.stable_baseline_payload(args(path))
            baseline_path = Path(directory) / "stable-baseline.json"
            baseline_path.write_text(RECOVERY.json.dumps(baseline))
            complete_stable_rotation(path)
            verify_args = args(path)
            verify_args.baseline = baseline_path
            responses = [
                (200, {"connectivity": "online"}),
                (200, {"ready": True}),
            ]
            with (
                patch.object(RECOVERY, "fetch_json", side_effect=responses),
                patch.object(RECOVERY.time, "time", return_value=1901.0),
            ):
                payload = RECOVERY.stable_verify_payload(verify_args)
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["read_only"])
            self.assertTrue(payload["state"]["status_message_rotated"])
            self.assertIsNone(payload["state"]["recovery_stable_since"])
            self.assertIsNone(payload["state"]["last_voltage_event_kind"])
            self.assertFalse(payload["state"]["voltage_episode_present"])

    def test_stable_verify_rejects_later_outage(self):
        """Do not count a stable interval broken by another outage."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "interrupted.sqlite3"
            create_database(path, open_outage=False)
            make_stable_recovery(path)
            responses = [
                (200, {"connectivity": "online"}),
                (200, {"ready": True}),
            ]
            with patch.object(RECOVERY, "fetch_json", side_effect=responses):
                baseline = RECOVERY.stable_baseline_payload(args(path))
            self.assertEqual(baseline["status"], "pass")
            complete_stable_rotation(path)
            with closing(sqlite3.connect(path)) as db:
                db.execute("INSERT INTO outages VALUES(8,1500,1600,'L1')")
                db.commit()
            errors = RECOVERY.stable_verify_errors(
                baseline,
                RECOVERY.current_db_state(path),
                {"connectivity": "online"},
                {"ready": True},
                1901.0,
            )
            self.assertIn(
                "Shelly outage changed since stable baseline", errors
            )

    def test_stable_verify_fails_before_fifteen_minutes(self):
        """Reject verification before the fixed stability period ends."""

        baseline = {
            "state": {
                "recovery_stable_since": "1000.0",
                "status_message_fingerprint": (
                    RECOVERY.status_message_fingerprint("682")
                ),
                "measurement_count": 1,
                "last_notification_at": "777.0",
            }
        }
        state = {
            "quick_check": "ok",
            "zero_voltage_rows": 0,
            "measurement_count": 2,
            "status_message_id": "900",
            "recovery_stable_since": None,
            "last_notification_at": "777.0",
            "last_voltage_event_kind": None,
            "voltage_episode_present": False,
        }
        errors = RECOVERY.stable_verify_errors(
            baseline,
            state,
            {"connectivity": "online"},
            {"ready": True},
            1899.0,
        )
        self.assertIn(
            "15-minute stable recovery period has not elapsed", errors
        )

    def test_stable_verify_detects_changed_audible_cooldown(self):
        """Fail if silent rotation mutates the audible cooldown timestamp."""

        baseline = {
            "state": {
                "recovery_stable_since": "1000.0",
                "status_message_fingerprint": (
                    RECOVERY.status_message_fingerprint("682")
                ),
                "measurement_count": 1,
                "last_notification_at": "777.0",
            }
        }
        state = {
            "quick_check": "ok",
            "zero_voltage_rows": 0,
            "measurement_count": 2,
            "status_message_id": "900",
            "recovery_stable_since": None,
            "last_notification_at": "1800.0",
            "last_voltage_event_kind": None,
            "voltage_episode_present": False,
        }
        errors = RECOVERY.stable_verify_errors(
            baseline,
            state,
            {"connectivity": "online"},
            {"ready": True},
            1901.0,
        )
        self.assertIn(
            "audible notification cooldown timestamp changed", errors
        )

    def test_stable_verify_requires_recovery_context_cleanup(self):
        """Fail while the recovery timer or event context remains present."""

        baseline = {
            "state": {
                "recovery_stable_since": "1000.0",
                "status_message_fingerprint": (
                    RECOVERY.status_message_fingerprint("682")
                ),
                "measurement_count": 1,
                "last_notification_at": "777.0",
            }
        }
        state = {
            "quick_check": "ok",
            "zero_voltage_rows": 0,
            "measurement_count": 2,
            "status_message_id": "900",
            "recovery_stable_since": "1000.0",
            "last_notification_at": "777.0",
            "last_voltage_event_kind": "recovered",
            "voltage_episode_present": True,
        }
        errors = RECOVERY.stable_verify_errors(
            baseline,
            state,
            {"connectivity": "online"},
            {"ready": True},
            1901.0,
        )
        self.assertIn("stable recovery timer was not cleared", errors)
        self.assertIn("recovery event context was not cleared", errors)
        self.assertIn("voltage episode context was not cleared", errors)

    def test_capture_once_is_private_and_idempotent(self):
        """Keep the first outage baseline while measurements advance."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "active.sqlite3"
            create_database(path)
            request = args(path)
            request.output_dir = Path(directory) / "private"
            first = RECOVERY.capture_once_payload(request)
            self.assertEqual(first["status"], "pass")
            self.assertTrue(first["created"])
            target = Path(first["path"])
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(target.parent.stat().st_mode), 0o700)
            first_bytes = target.read_bytes()
            self.assertNotIn(b'"682"', first_bytes)
            with closing(sqlite3.connect(path)) as db:
                db.execute("INSERT INTO measurements VALUES(2,'L1',225,50,1)")
                db.commit()
            second = RECOVERY.capture_once_payload(request)
            self.assertFalse(second["created"])
            self.assertEqual(first_bytes, target.read_bytes())

    def test_capture_once_skips_when_idle(self):
        """Do not create or backfill files after an outage has ended."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "idle.sqlite3"
            create_database(path, open_outage=False)
            request = args(path)
            request.output_dir = Path(directory) / "private"
            result = RECOVERY.capture_once_payload(request)
            self.assertEqual(result["status"], "skip")
            self.assertFalse(request.output_dir.exists())

    def test_capture_once_saves_live_stable_timer(self):
        """Capture a real stable timer using read-only health endpoints."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stable.sqlite3"
            create_database(path, open_outage=False)
            make_stable_recovery(path)
            request = args(path)
            request.output_dir = Path(directory) / "private"
            responses = [
                (200, {"connectivity": "online"}),
                (200, {"ready": True}),
            ]
            with patch.object(RECOVERY, "fetch_json", side_effect=responses):
                result = RECOVERY.capture_once_payload(request)
            self.assertEqual(result["status"], "pass")
            self.assertTrue(result["created"])
            self.assertEqual(
                result["baseline_kind"], "stable_recovery_baseline"
            )

    def test_failed_recovery_baseline_cannot_be_reused(self):
        """A failed capture must never authorize recovery acceptance."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed.json"
            payload = {
                "kind": "recovery_baseline",
                "status": "fail",
                "state": {"outage": {"id": 7}},
            }
            path.write_text(RECOVERY.json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "not accepted"):
                RECOVERY.load_baseline(path)

    def test_failed_stable_baseline_cannot_be_reused(self):
        """A failed timer capture must never authorize stable acceptance."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed-stable.json"
            payload = {
                "kind": "stable_recovery_baseline",
                "status": "fail",
                "state": {"recovery_stable_since": "1000.0"},
            }
            path.write_text(RECOVERY.json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "not accepted"):
                RECOVERY.load_stable_baseline(path)

    def test_recovery_baseline_requires_json_object(self):
        """Reject JSON arrays and scalars instead of crashing."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text("[]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                RECOVERY.load_baseline(path)
            with self.assertRaisesRegex(ValueError, "JSON object"):
                RECOVERY.load_stable_baseline(path)

    def test_stable_timer_rejects_nonfinite_values(self):
        """Never accept NaN, infinity or a negative recovery timestamp."""

        for value in ("NaN", "Infinity", "-Infinity", "-1", "0"):
            with self.subTest(value=value):
                self.assertIsNone(
                    RECOVERY.stable_since_value(
                        {"recovery_stable_since": value}
                    )
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
