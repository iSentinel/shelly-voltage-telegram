"""Synthetic checks for retrospective read-only natural outage recovery."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sqlite3
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_natural_audit", ROOT / "scripts/em50-natural-recovery-audit.py"
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def fixture(path: Path, now: float, *, open_outage: bool = False) -> None:
    """Create one synthetic incident with its real recovery sample."""

    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute(
            "CREATE TABLE outages "
            "(id INTEGER PRIMARY KEY,started_at REAL,ended_at REAL,phase TEXT)"
        )
        db.execute(
            "CREATE TABLE measurements "
            "(ts REAL,voltage REAL,frequency REAL,phase TEXT)"
        )
        db.execute("CREATE TABLE meta (key TEXT,value TEXT)")
        db.execute(
            "INSERT INTO outages VALUES (18,?,?,?)",
            (now - 900, None if open_outage else now - 45, "L1"),
        )
        db.execute(
            "INSERT INTO measurements VALUES (?,?,?,?)",
            (now - 30, 216.3, 50.0, "L1"),
        )
        db.execute(
            "INSERT INTO meta VALUES ('telegram_status_message_id',?)",
            ("synthetic-private-id",),
        )
        db.commit()


def health(now: float) -> dict:
    """Provide a fresh Shelly observation without network calls."""

    return {
        "last_success": now - 5,
        "voltage": 215.2,
        "connectivity": "online",
    }


class NaturalRecoveryAuditTests(unittest.TestCase):
    """Require physical recovery; do not claim Telegram client delivery."""

    def setUp(self):
        """Create synthetic SQLite in a disposable private directory."""

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "incident.sqlite3"
        self.now = time.time()
        fixture(self.db, self.now)
        self.state = AUDIT.read_state(self.db, 60)

    def test_real_recovery_only_reports_physical_evidence(self):
        """Never infer a Telegram edit from a saved message ID."""

        AUDIT.check_recovery(
            self.state, health(self.now), {"ready": True}, self.now, 60
        )
        record = AUDIT.report(self.state, health(self.now), self.now, 60)
        self.assertEqual(record["status"], "PASS")
        self.assertEqual(record["measurements_during_offline"], 0)
        self.assertEqual(record["outage_id"], 18)
        self.assertEqual(
            record["telegram_channel_edit_visible"], "NOT_VERIFIED"
        )
        self.assertFalse(record["release_authorized"])
        self.assertNotIn("synthetic-private-id", json.dumps(record))

    def test_read_only_does_not_modify_source_database(self):
        """Even metadata must stay byte-for-byte unchanged."""

        before = self.db.read_bytes()
        AUDIT.read_state(self.db, 60)
        self.assertEqual(self.db.read_bytes(), before)

    def test_open_incident_is_not_a_recovery(self):
        """Active outage cannot pass despite an optimistic /health."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        with self.assertRaisesRegex(ValueError, "incident history"):
            AUDIT.check_recovery(
                state, health(self.now), {"ready": True}, self.now, 60
            )

    def test_early_measurement_during_offline_fails(self):
        """Reject fabricated or leaking data during an extended blackout."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "INSERT INTO measurements VALUES (?,?,?,?)",
                (self.now - 850, 0.1, 50.0, "L1"),
            )
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        self.assertEqual(state["early_count"], 1)
        with self.assertRaisesRegex(ValueError, "incident history"):
            AUDIT.check_recovery(
                state, health(self.now), {"ready": True}, self.now, 60
            )

    def test_zero_voltage_synthesized_after_recovery_fails(self):
        """Do not misrepresent zero as a real meter reading."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "INSERT INTO measurements VALUES (?,?,?,?)",
                (self.now - 20, 0, 50.0, "L1"),
            )
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        self.assertEqual(state["zero_count"], 1)
        with self.assertRaisesRegex(ValueError, "incident history"):
            AUDIT.check_recovery(
                state, health(self.now), {"ready": True}, self.now, 60
            )

    def test_missing_post_recovery_sample_fails(self):
        """Only samples newer than a closed incident count as recovered."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("DELETE FROM measurements")
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        with self.assertRaisesRegex(ValueError, "incident history"):
            AUDIT.check_recovery(
                state, health(self.now), {"ready": True}, self.now, 60
            )

    def test_last_success_must_be_newer_than_recovery(self):
        """A stale historical sample may not satisfy online readiness."""

        old = health(self.now)
        old["last_success"] = self.now - 120
        with self.assertRaisesRegex(ValueError, "current Shelly"):
            AUDIT.check_recovery(
                self.state, old, {"ready": True}, self.now, 60
            )

    def test_offline_and_not_ready_are_rejected(self):
        """An HTTP response must explicitly say ready true and online."""

        for status, ready in (
            ("offline", {"ready": True}),
            ("online", {"ready": False}),
            ("online", {}),
        ):
            with (
                self.subTest(status=status, ready=ready),
                self.assertRaisesRegex(ValueError, "current Shelly"),
            ):
                AUDIT.check_recovery(
                    self.state,
                    {**health(self.now), "connectivity": status},
                    ready,
                    self.now,
                    60,
                )

    def test_bad_or_zero_voltage_is_not_an_online_measurement(self):
        """Reject sentinel zero, boolean, NaN and impossible readings."""

        for voltage in (0, True, float("nan"), 501):
            with (
                self.subTest(voltage=str(voltage)),
                self.assertRaisesRegex(ValueError, "physically valid"),
            ):
                AUDIT.check_recovery(
                    self.state,
                    {**health(self.now), "voltage": voltage},
                    {"ready": True},
                    self.now,
                    60,
                )

    def test_latest_same_phase_voltage_and_frequency_are_real(self):
        """A good health response cannot hide a corrupt L1 SQLite row."""

        for voltage, frequency in ((900, 50), (216, 5), (-5, 50)):
            with contextlib.closing(sqlite3.connect(self.db)) as db:
                db.execute(
                    "UPDATE measurements SET voltage=?,frequency=? "
                    "WHERE phase='L1'",
                    (voltage, frequency),
                )
                db.commit()
            current = AUDIT.read_state(self.db, 60)
            with (
                self.subTest(voltage=voltage, frequency=frequency),
                self.assertRaisesRegex(ValueError, "same-phase"),
            ):
                AUDIT.check_recovery(
                    current,
                    health(self.now),
                    {"ready": True},
                    self.now,
                    60,
                )

    def test_stale_same_phase_sample_is_not_fresh_health(self):
        """A new HTTP health timestamp cannot refresh an old L1 row."""

        later = self.now + 150
        with self.assertRaisesRegex(ValueError, "same-phase"):
            AUDIT.check_recovery(
                self.state, health(later), {"ready": True}, later, 60
            )

    def test_future_same_phase_sample_is_not_recovery(self):
        """Reject a future-dated SQLite row as physical recovery evidence."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute(
                "UPDATE measurements SET ts=? WHERE phase='L1'",
                (self.now + 100,),
            )
            db.commit()
        current = AUDIT.read_state(self.db, 60)
        with self.assertRaisesRegex(ValueError, "same-phase"):
            AUDIT.check_recovery(
                current, health(self.now), {"ready": True}, self.now, 60
            )

    def test_latest_same_phase_field_never_reports_private_id(self):
        """Return physical tuple without leaking saved Telegram metadata."""

        self.assertEqual(len(self.state["latest_phase_sample"]), 3)
        self.assertEqual(self.state["latest_phase_sample"][1], 216.3)
        self.assertNotIn(
            "latest_phase_sample",
            AUDIT.report(self.state, health(self.now), self.now, 60),
        )

    def test_missing_status_id_fails(self):
        """A missing status message cannot be treated as delivered."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("DELETE FROM meta")
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        with self.assertRaisesRegex(ValueError, "current Shelly"):
            AUDIT.check_recovery(
                state, health(self.now), {"ready": True}, self.now, 60
            )

    def test_local_only_urls_reject_offhost_access(self):
        """No monitoring request may leak data to a non-local endpoint."""

        with patch.object(AUDIT.urllib.request, "build_opener") as client:
            for address in (
                "https://remote.example/health",
                "http://127.0.0.1.evil.test/health",
                "http://admin@127.0.0.1/health",
                "http://127.0.0.1/private",
            ):
                with (
                    self.subTest(address=address),
                    self.assertRaisesRegex(ValueError, "local health"),
                ):
                    AUDIT.local_json(address, "/health")
            client.assert_not_called()

    def test_redirects_cannot_change_destination(self):
        """Reject a redirect before any request to Telegram or remote host."""

        req = urllib.request.Request("http://127.0.0.1:8080/health")
        with self.assertRaisesRegex(ValueError, "redirect"):
            AUDIT.NoRedirect().redirect_request(
                req, None, 302, "Found", {}, "https://remote.example/health"
            )

    def test_empty_sqlite_and_unexpected_schema_fail(self):
        """Missing incident tables cannot create a fake PASS."""

        with tempfile.TemporaryDirectory() as folder:
            empty = Path(folder) / "empty.sqlite3"
            with contextlib.closing(sqlite3.connect(empty)):
                pass
            with self.assertRaises(sqlite3.DatabaseError):
                AUDIT.read_state(empty, 60)

    def test_missing_database_fails_closed(self):
        """Never create a missing production database."""

        missing = Path(self.temporary.name) / "missing.sqlite3"
        with self.assertRaisesRegex(ValueError, "missing"):
            AUDIT.read_state(missing, 60)
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
