"""Synthetic checks for retrospective read-only natural outage recovery."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
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

    def test_open_incident_returns_pending_without_http(self):
        """Natural blackout is pending, never a false recovery or API error."""

        fixture_db = self.db
        with contextlib.closing(sqlite3.connect(fixture_db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.execute("DELETE FROM measurements")
            db.commit()
        output = io.StringIO()
        argv = ["audit", "--db", str(fixture_db), "--json"]
        with (
            patch.object(sys, "argv", argv),
            patch.object(AUDIT, "local_json") as network,
            contextlib.redirect_stdout(output),
        ):
            code = AUDIT.main()
        self.assertEqual(code, 2)
        answer = json.loads(output.getvalue())
        self.assertEqual(answer["status"], "PENDING")
        self.assertEqual(answer["outage_id"], 18)
        self.assertFalse(answer["mains_restoration_verified"])
        self.assertFalse(answer["telegram_delivery_verified"])
        self.assertFalse(answer["release_authorized"])
        network.assert_not_called()

    def test_stale_historical_voltage_stays_hidden_in_offline_cli(self):
        """The last good 216.5 V sample must never become a live reading."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.execute("DELETE FROM measurements")
            db.execute(
                "INSERT INTO measurements VALUES (?,?,?,?)",
                (self.now - 1000, 216.5, 50.0, "L1"),
            )
            db.commit()
        original = self.db.read_bytes()
        output = io.StringIO()
        with (
            patch.object(
                sys, "argv", ["audit", "--db", str(self.db), "--json"]
            ),
            patch.object(AUDIT, "local_json") as network,
            contextlib.redirect_stdout(output),
        ):
            code = AUDIT.main()
        self.assertEqual(code, 2)
        response = json.loads(output.getvalue())
        self.assertEqual(response["status"], "PENDING")
        self.assertEqual(response["reason"], "shelly_connectivity_loss_open")
        self.assertNotIn("216.5", output.getvalue())
        self.assertNotIn("current_voltage_v", response)
        self.assertFalse(response["mains_restoration_verified"])
        self.assertFalse(response["telegram_delivery_verified"])
        self.assertEqual(self.db.read_bytes(), original)
        network.assert_not_called()

    def test_hysteresis_does_not_use_http_503_to_claim_recovery(self):
        """A fresh sample without a closed outage remains unverified."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.commit()
        output = io.StringIO()
        with (
            patch.object(
                sys, "argv", ["audit", "--db", str(self.db), "--json"]
            ),
            patch.object(AUDIT, "local_json") as network,
            contextlib.redirect_stdout(output),
        ):
            code = AUDIT.main()
        response = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(response["status"], "PENDING")
        self.assertEqual(
            response["reason"], "open_incident_with_unconfirmed_samples"
        )
        self.assertFalse(response["mains_restoration_verified"])
        self.assertFalse(response["release_authorized"])
        network.assert_not_called()

    def test_first_samples_stay_pending_until_outage_closes(self):
        """Recovery hysteresis cannot declare physical restoration early."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.execute(
                "INSERT INTO measurements VALUES (?,?,?,?)",
                (self.now - 20, 223.0, 50.0, "L1"),
            )
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        result = AUDIT.pending_record(state, self.now)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(
            result["reason"], "open_incident_with_unconfirmed_samples"
        )
        self.assertFalse(result["mains_restoration_verified"])

    def test_pending_rejects_synthetic_zero_sample(self):
        """A forged zero-voltage reading never becomes recovery evidence."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.execute(
                "INSERT INTO measurements VALUES (?,?,?,?)",
                (self.now - 20, 0.0, 50.0, "L1"),
            )
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            AUDIT.pending_record(state, self.now)

    def test_pending_rejects_parallel_open_outages(self):
        """Never accept two overlapping physical connectivity incidents."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.execute("DELETE FROM measurements")
            db.execute(
                "INSERT INTO outages VALUES (19,?,?,?)",
                (self.now - 60, None, "L1"),
            )
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        self.assertEqual(state["active"], 2)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            AUDIT.pending_record(state, self.now)

    def test_pending_rejects_missing_stable_status(self):
        """An open outage cannot be accepted without saved status metadata."""

        with contextlib.closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE outages SET ended_at=NULL")
            db.execute("DELETE FROM measurements")
            db.execute("DELETE FROM meta")
            db.commit()
        state = AUDIT.read_state(self.db, 60)
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            AUDIT.pending_record(state, self.now)

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
