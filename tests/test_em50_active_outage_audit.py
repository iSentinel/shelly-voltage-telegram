"""Source-only regression coverage for the active outage audit."""

from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/em50-active-outage-audit.py"
)
SPEC = importlib.util.spec_from_file_location("em50_active_outage", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
NOW = 1800.0


class ActiveOutageAuditTests(unittest.TestCase):
    """Keep offline telemetry honest without any Telegram operations."""

    def setUp(self):
        """Create an isolated sample and one active outage incident."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "test.sqlite3"
        with sqlite3.connect(self.path) as db:
            db.executescript(
                "CREATE TABLE measurements ("
                "id INTEGER PRIMARY KEY,ts REAL,voltage REAL,"
                "frequency REAL,phase TEXT);"
                "CREATE TABLE outages ("
                "id INTEGER PRIMARY KEY,started_at REAL,"
                "ended_at REAL,phase TEXT);"
                "INSERT INTO measurements VALUES "
                "(1,1000,219.9,50,'L1');"
                "INSERT INTO outages VALUES (27,1042,NULL,'L1');"
            )
        self.health = {
            "connectivity": "offline",
            "outage_count": 27,
            "last_success": 1000.01,
            "voltage": 219.9,
        }
        self.ready = {"ready": False, "reason": "shelly offline"}

    def execute(self):
        """Feed two matching offline states around one SQLite read."""

        replies = [(200, self.health), (503, self.ready), (200, self.health)]
        with patch.object(AUDIT, "local_json", side_effect=replies):
            return AUDIT.audit(self.path, 27, NOW)

    def update(self, sql: str):
        """Edit only the temporary database used by the test."""

        with sqlite3.connect(self.path) as db:
            db.execute(sql)

    def rejects(self, reason: str):
        """Assert that the auditor fails closed on a specific condition."""

        with self.assertRaisesRegex(AUDIT.Blocked, reason):
            self.execute()

    def test_active_offline_incident_passes(self):
        """The stored 219.9 V reading is historical, not current."""

        result = self.execute()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["incident_id"], 27)
        self.assertFalse(result["current_voltage_known"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["production_modified"])

    def test_new_sample_during_outage_fails(self):
        """A contradictory new phase sample must invalidate the incident."""

        self.update("INSERT INTO measurements VALUES (2,1100,224,50,'L1')")
        self.rejects("INCIDENT_SEQUENCE")

    def test_synthetic_zero_fails(self):
        """Never interpret zero records as physical meter readings."""

        self.update("INSERT INTO measurements VALUES (0,900,0,50,'L1')")
        self.rejects("POST_OUTAGE_DATA_CONFLICT")

    def test_future_timestamp_fails(self):
        """A future sample is not acceptable even on another phase."""

        self.update("INSERT INTO measurements VALUES (0,1900,223,50,'L2')")
        self.rejects("POST_OUTAGE_DATA_CONFLICT")

    def test_closed_event_does_not_pass(self):
        """An already closed outage needs the recovery checker instead."""

        self.update("UPDATE outages SET ended_at=1200 WHERE id=27")
        self.rejects("INCIDENT_NOT_OPEN")

    def test_wrong_incident_id_fails(self):
        """Do not silently accept evidence from a different incident."""

        with self.assertRaisesRegex(AUDIT.Blocked, "INCIDENT_ID_MISMATCH"):
            AUDIT.inspect_db(self.path, 26, NOW)

    def test_online_state_is_not_an_active_outage(self):
        """A restored connection must not pass as an offline state."""

        self.health["connectivity"] = "online"
        self.rejects("METER_NOT_CONFIRMED_OFFLINE")

    def test_unrelated_503_is_not_evidence(self):
        """A failing service is not necessarily an offline Shelly."""

        self.ready["reason"] = "database unavailable"
        self.rejects("METER_NOT_CONFIRMED_OFFLINE")

    def test_health_timestamp_must_match(self):
        """Timestamp parity prevents stale API state from being trusted."""

        self.health["last_success"] = 900
        self.rejects("HEALTH_TIMESTAMP_INVALID")

    def test_incident_counter_must_match(self):
        """A different API incident cannot inherit this database state."""

        self.health["outage_count"] = 26
        self.rejects("INCIDENT_COUNTER_MISMATCH")

    def test_missing_database_is_rejected(self):
        """A nonexistent SQLite file must never become a PASS."""

        with self.assertRaisesRegex(AUDIT.Blocked, "DATABASE_UNAVAILABLE"):
            AUDIT.inspect_db(Path("/nonexistent/em50-test.sqlite3"), 27, NOW)

    def test_non_loopback_api_is_rejected(self):
        """The command cannot be used as a generic network fetcher."""

        with self.assertRaisesRegex(AUDIT.Blocked, "UNSAFE_ENDPOINT"):
            AUDIT.local_json("http://example.com/health", "/health")

    def test_outage_sequence_must_make_sense(self):
        """An outage cannot predate its latest real sample."""

        self.update("UPDATE outages SET started_at=990 WHERE id=27")
        self.rejects("INCIDENT_SEQUENCE")

    def test_invalid_voltage_is_rejected(self):
        """Physically implausible latest readings are not accepted."""

        self.update("UPDATE measurements SET voltage=900 WHERE id=1")
        self.rejects("INCIDENT_SEQUENCE")


if __name__ == "__main__":
    unittest.main()
