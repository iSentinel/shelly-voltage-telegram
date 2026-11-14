"""Test failure-recovery decisions without touching live sources or DB."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "scripts/source-reconcile-recovery-preflight.py"
SPEC = importlib.util.spec_from_file_location("source_recovery", FILE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SourceRecoveryPreflightTests(unittest.TestCase):
    """Run all cases on synthetic records or disposable SQLite fixtures."""

    def setUp(self) -> None:
        """Create healthy synthetic read-only state."""
        self.db = {"integrity": "ok", "samples": 100, "open_outages": 0}
        self.live = {"connectivity": "online", "stale": False, "ready": True}

    def result(self) -> dict:
        """Evaluate without contacting a meter, bot or Docker."""
        return MODULE.decision("0.5.21-hw", "0.5.15-hw", self.db, self.live)

    def test_forward_reapply_is_advisory_only(self) -> None:
        """A healthy source mismatch never authorizes installation."""
        result = self.result()
        self.assertEqual(result["status"], "PREFLIGHT_PASS")
        self.assertEqual(result["candidate_version"], "0.5.21-hw")
        self.assertEqual(result["installed_version"], "0.5.15-hw")
        self.assertFalse(result["source_install_authorized"])
        self.assertFalse(result["docker_build_allowed"])
        self.assertTrue(result["writer_fenced_snapshot_required"])

    def test_physical_outage_blocks_recovery(self) -> None:
        """An offline Shelly must not produce a successful preflight."""
        self.live.update(connectivity="offline", stale=True, ready=False)
        self.db["open_outages"] = 1
        result = self.result()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("OPEN_OUTAGE", result["reasons"])
        self.assertIn("SHELLY_OFFLINE_OR_STALE", result["reasons"])
        self.assertIn("SHELLY_NOT_READY", result["reasons"])

    def test_stale_but_http_ready_fails(self) -> None:
        """Do not treat an HTTP response as fresh physical data."""
        self.live["stale"] = True
        self.assertIn("SHELLY_OFFLINE_OR_STALE", self.result()["reasons"])

    def test_not_ready_during_recovery_fails(self) -> None:
        """Ready=503 blocks any source-reconciliation recommendation."""
        self.live["ready"] = False
        self.assertEqual(self.result()["reasons"], ["SHELLY_NOT_READY"])

    def test_open_incident_fails_when_shelly_online(self) -> None:
        """Do not allow stale outage records to pass silently."""
        self.db["open_outages"] = 1
        self.assertIn("OPEN_OUTAGE", self.result()["reasons"])

    def test_corrupt_db_fails(self) -> None:
        """Integrity failure cannot be confused with source mismatch."""
        self.db["integrity"] = "corrupt"
        self.assertIn("SQLITE_INTEGRITY_OR_HISTORY", self.result()["reasons"])

    def test_no_history_fails(self) -> None:
        """A completely empty SQLite history is never accepted."""
        self.db["samples"] = 0
        self.assertEqual(self.result()["status"], "BLOCKED")

    def test_database_query_is_read_only(self) -> None:
        """Probe a disposable SQLite DB and preserve its exact content."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.sqlite3"
            with sqlite3.connect(path) as db:
                db.executescript(
                    "CREATE TABLE measurements(voltage REAL);"
                    "INSERT INTO measurements VALUES(222.5);"
                    "CREATE TABLE outages(ended_at REAL);"
                )
            before = path.read_bytes()
            state = MODULE.database_state(path)
            self.assertEqual(
                state, {"integrity": "ok", "samples": 1, "open_outages": 0}
            )
            self.assertEqual(path.read_bytes(), before)

    def test_linked_database_is_rejected(self) -> None:
        """Never follow a symlink to source SQLite."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "db.sqlite3"
            path.write_bytes(b"not sqlite")
            alias = Path(temp) / "linked.sqlite3"
            alias.symlink_to(path)
            with self.assertRaises(MODULE.Blocked) as error:
                MODULE.database_state(alias)
            self.assertEqual(str(error.exception), "SQLITE_PATH_UNSAFE")

    def test_producer_failures_cannot_authorize_install(self) -> None:
        """An unavailable signed identity fails before any DB writes."""
        with patch.object(
            MODULE,
            "signed_identity",
            side_effect=MODULE.Blocked("SIGNED_IDENTITY_NOT_VERIFIED"),
        ):
            result = MODULE.preflight(
                Path("/unused"),
                Path("/unused"),
                Path("/unused"),
                Path("/unused"),
            )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["source_install_authorized"])
        self.assertFalse(result["production_modified"])


if __name__ == "__main__":
    unittest.main()
