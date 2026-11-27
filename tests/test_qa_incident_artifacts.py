"""Recompute synthetic evidence fingerprints and database contents."""

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "incident_artifacts",
    Path(__file__).resolve().parents[1] / "scripts/qa-incident-artifacts.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def write(root, name, value):
    """Persist exact private synthetic JSON and return its hash."""
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path = root / name
    path.write_bytes(raw)
    path.chmod(0o600)
    return QA.CHAIN.digest(raw)


def create_db(root):
    """Create independent standalone copies containing a pending marker."""
    live, rescue = root / "live.sqlite3", root / "rescue.sqlite3"
    with closing(sqlite3.connect(live)) as db, db:
        db.execute("CREATE TABLE measurements(id INTEGER PRIMARY KEY, v REAL)")
        db.executemany(
            "INSERT INTO measurements VALUES (?,?)", [(1, 220), (2, 221)]
        )
        db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
        db.execute(
            "INSERT INTO meta VALUES ('telegram_report_pending:x','pending')"
        )
    with (
        closing(sqlite3.connect(live)) as db,
        closing(sqlite3.connect(rescue)) as backup,
    ):
        db.backup(backup)
    live.chmod(0o600)
    rescue.chmod(0o600)
    return QA.GEN.snapshot(live), [
        QA.CHAIN.digest(p.read_bytes()) for p in (live, rescue)
    ]


def identity_fields(root, history):
    """Construct synthetic writer identity with actual fixture DB inode."""
    identity = {
        "container_id": "a" * 64,
        "image_id": "sha256:" + "b" * 64,
        "started_at": 100,
        "database_device": history["device"],
        "database_inode": history["inode"],
        "writer_lock_device": 1,
        "writer_lock_inode": 2,
    }
    identity["receipt_sha256"] = write(
        root,
        "receipt.json",
        {
            "schema": 1,
            "synthetic": True,
            "image_id": identity["image_id"],
            "signature_verified": False,
        },
    )
    identity["journal_sha256"] = write(
        root,
        "journal.json",
        {
            "schema": 1,
            "synthetic": True,
            "phase": "verified",
            "receipt_sha256": identity["receipt_sha256"],
        },
    )
    return identity


def declarations(root, history, hashes, identity):
    """Write and fingerprint the two supporting synthetic declarations."""
    observation = write(
        root,
        "observation.json",
        {
            "schema": 1,
            "synthetic": True,
            "identity": identity,
            "observed_at": 102,
            "fault_class": "general-runtime",
            "readiness_http": 503,
        },
    )
    capture = write(
        root,
        "wal-capture.json",
        {
            "schema": 1,
            "synthetic": True,
            "kind": "standalone-no-sidecars",
            "live_sha256": hashes[0],
            "rescue_sha256": hashes[1],
            "history_sha256": history["logical_sha256"],
            "wal_capture_verified": False,
        },
    )
    return observation, capture


def stage_fields(root, history, hashes, identity):
    """Create actual fault and standalone capture declarations."""
    observation, capture = declarations(root, history, hashes, identity)
    return {
        "baseline": {"writer_lease_held": True, "readiness_http": 200},
        "fault": {
            "fault_class": "general-runtime",
            "observation_sha256": observation,
        },
        "stopped": {
            "writer_quiescent": True,
            "history_sha256": history["logical_sha256"],
            "rescue_sha256": hashes[1],
            "pending_intents": history["pending"],
            "wal_capture_sha256": capture,
        },
        "hold": {
            "writer_quiescent": True,
            "history_sha256": history["logical_sha256"],
        },
    }


def hold_receipt(root, previous, identity, history):
    """Bind the synthetic hold declaration to stopped history."""
    return write(
        root,
        "hold-receipt.json",
        {
            "schema": 1,
            "synthetic": True,
            "stopped_sha256": previous,
            "identity": identity,
            "history_sha256": history["logical_sha256"],
            "release_authorized": False,
        },
    )


def build_fixture(root):
    """Create a complete synthetic chain and all referenced artifacts."""
    history, hashes = create_db(root)
    identity = identity_fields(root, history)
    fields = stage_fields(root, history, hashes, identity)
    previous, stages = None, {}
    for i, stage in enumerate(QA.CHAIN.STAGES):
        extra = fields[stage]
        if stage == "hold":
            extra["hold_receipt_sha256"] = hold_receipt(
                root, previous, identity, history
            )
        previous = stages[stage] = write(
            root,
            stage + ".json",
            {
                "schema": 1,
                "synthetic": True,
                "stage": stage,
                "observed_at": 101 + i,
                "identity": identity,
                "previous_sha256": previous,
                **extra,
            },
        )
    return write(
        root,
        "manifest.json",
        {
            "schema": 1,
            "synthetic": True,
            "stages": stages,
        },
    )


class ArtifactTests(unittest.TestCase):
    """Use real synthetic database and JSON bytes without root dependency."""

    def setUp(self):
        """Model root permission gates only."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.anchor = build_fixture(self.root)
        self.addCleanup(patch.stopall)
        patch.object(
            QA.CHAIN, "private_file", side_effect=Path.read_bytes
        ).start()
        patch.object(QA, "private_database").start()

    def check(self):
        """Verify the actual fixture contents."""
        raw = (self.root / "manifest.json").read_bytes()
        stages = {
            s: (self.root / (s + ".json")).read_bytes()
            for s in QA.CHAIN.STAGES
        }
        return QA.review(self.root, raw, stages, self.anchor)

    def test_complete_contents(self):
        """Actual synthetic files pass without claiming incident authority."""
        result = self.check()
        self.assertTrue(result["artifact_contents_verified"])
        self.assertTrue(result["synthetic_artifacts_only"])
        self.assertEqual((result["rows"], result["pending_intents"]), (2, 1))
        for key in (
            "wal_capture_verified",
            "incident_hold_verified",
            "fault_attribution_verified",
            "startup_authorized",
        ):
            self.assertIs(result[key], False)

    def test_modified_artifacts(self):
        """Every referenced JSON is independently checked."""
        for name in (
            "receipt",
            "journal",
            "observation",
            "wal-capture",
            "hold-receipt",
        ):
            path = self.root / (name + ".json")
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "fingerprint"),
            ):
                self.check()
            path.write_bytes(raw)

    def test_missing_artifact(self):
        """A bare fingerprint is not sufficient evidence."""
        (self.root / "observation.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.check()

    def test_stale_rescue(self):
        """A changed rescue cannot be accepted on the same row count."""
        with closing(sqlite3.connect(self.root / "rescue.sqlite3")) as db, db:
            db.execute("UPDATE measurements SET v=230 WHERE id=1")
        with self.assertRaisesRegex(ValueError, "database evidence"):
            self.check()

    def test_live_pending_drift(self):
        """Changed delivery metadata invalidates the history claim."""
        with closing(sqlite3.connect(self.root / "live.sqlite3")) as db, db:
            db.execute("DELETE FROM meta")
        with self.assertRaisesRegex(ValueError, "database evidence"):
            self.check()

    def test_sidecar_refused(self):
        """Do not treat a WAL file as an independent standalone snapshot."""
        (self.root / "live.sqlite3-wal").write_bytes(b"synthetic")
        with self.assertRaisesRegex(ValueError, "standalone"):
            self.check()

    def test_nested_claim_types(self):
        """Rehashed booleans cannot masquerade as numeric identity fields."""
        with self.assertRaisesRegex(ValueError, "claim mismatch"):
            QA.match(
                {"identity": {"device": True}}, {"identity": {"device": 1}}
            )

    def test_capture_claim_refused(self):
        """Do not promote a standalone fixture to actual WAL verification."""
        record = {"wal_capture_verified": True}
        with self.assertRaisesRegex(ValueError, "claim mismatch"):
            QA.match(record, {"wal_capture_verified": False})
