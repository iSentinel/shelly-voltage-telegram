"""Test non-mutating local Shelly backup verification."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/verify-local-release-backups.py"
SPEC = importlib.util.spec_from_file_location(
    "qa_immutable_backup_reader", SCRIPT
)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


def fixture(root: Path) -> dict:
    """Create two fixed offline standalone SQLite copies plus manifest."""
    first = root / "current-A.sqlite3"
    with contextlib.closing(sqlite3.connect(first)) as db:
        db.execute("CREATE TABLE measurements(ts REAL, voltage REAL)")
        db.executemany(
            "INSERT INTO measurements VALUES (?, ?)",
            [(1.0, 221.1), (2.0, 222.2), (3.0, 223.3)],
        )
        db.commit()
    second = root / "current-B.sqlite3"
    shutil.copyfile(first, second)
    for path in (first, second):
        os.chmod(path, 0o600)
    entries = [
        {
            "filename": path.name,
            "bytes": path.stat().st_size,
            "sha256": QA.hash_file(path),
            "state": QA.sqlite_state(path),
        }
        for path in (first, second)
    ]
    manifest = {
        "kind": QA.EXPECTED_KIND,
        "physical_failure_domains": 1,
        "last_cutover_snapshot": False,
        "release_approval": False,
        "runtime_signature_reverified_in_this_run": False,
        "snapshots": entries,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    os.chmod(path, 0o600)
    return manifest


class ImmutableBackupVerifyTests(unittest.TestCase):
    """Never connect to live SQLite or production Docker."""

    def setUp(self) -> None:
        """Use an owned 0700 disposable directory for each test."""
        tmp = tempfile.TemporaryDirectory(prefix="immutable-backup-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.manifest = fixture(self.root)

    def update_manifest(self, updated: dict) -> None:
        """Overwrite only the invented test manifest."""
        (self.root / "manifest.json").write_text(json.dumps(updated))

    def test_default_inert(self) -> None:
        """Default CLI must not access the verifier."""
        with (
            patch.object(sys, "argv", ["qa"]),
            patch.object(QA, "verify", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 0)

    def test_explicit_backup_dir_required(self) -> None:
        """--verify cannot guess a production directory or backup source."""
        with (
            patch.object(sys, "argv", ["qa", "--verify"]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 2)

    def test_verified_copies_remain_release_no_go(self) -> None:
        """Local intact snapshots do not authorize production release."""
        report = QA.verify(self.root)
        self.assertEqual(report["snapshot_A_rows"], 3)
        self.assertEqual(report["snapshot_B_rows"], 3)
        self.assertEqual(report["release_decision"], "NO_GO")
        self.assertFalse(report["cutover_snapshot"])

    def test_verification_creates_no_wal_or_shm(self) -> None:
        """Immutable SQLite reading must not create any sidecar files."""
        before = sorted(x.name for x in self.root.iterdir())
        self.assertEqual(len(before), 3)
        QA.verify(self.root)
        after = sorted(x.name for x in self.root.iterdir())
        self.assertEqual(before, after)

    def test_snapshot_tampering_refused(self) -> None:
        """An appended byte invalidates the original snapshot digest."""
        with (self.root / "current-A.sqlite3").open("ab") as stream:
            stream.write(b"untrusted")
        with self.assertRaisesRegex(QA.Rejected, "SIZE_MISMATCH"):
            QA.verify(self.root)

    def test_false_digest_refused(self) -> None:
        """A manifest containing invented SHA256 must fail before SQLite."""
        self.manifest["snapshots"][0]["sha256"] = "f" * 64
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "SHA256_MISMATCH"):
            QA.verify(self.root)

    def test_invalid_digest_shape_refused(self) -> None:
        """A shortened digest is never treated as the full hash."""
        self.manifest["snapshots"][0]["sha256"] = "abc"
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "INVALID_SHA256"):
            QA.verify(self.root)

    def test_late_expected_state_refused(self) -> None:
        """Row count from a newer snapshot cannot validate an old copy."""
        self.manifest["snapshots"][0]["state"]["rows"] = 5
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "STATE_MISMATCH"):
            QA.verify(self.root)

    def test_extra_wal_file_refused(self) -> None:
        """Standalone snapshot folder may contain no WAL/SHM sidecars."""
        (self.root / "current-A.sqlite3-wal").touch()
        with self.assertRaisesRegex(QA.Rejected, "EXTRA_OR_MISSING"):
            QA.verify(self.root)

    def test_extra_shm_file_refused(self) -> None:
        """SQLite shared-memory leftovers are unacceptable output."""
        (self.root / "verify-current-B.sqlite3-shm").touch()
        with self.assertRaisesRegex(QA.Rejected, "EXTRA_OR_MISSING"):
            QA.verify(self.root)

    def test_missing_second_copy_refused(self) -> None:
        """One snapshot cannot impersonate independent twin copies."""
        (self.root / "current-B.sqlite3").unlink()
        with self.assertRaisesRegex(QA.Rejected, "EXTRA_OR_MISSING"):
            QA.verify(self.root)

    def test_symlink_snapshot_refused(self) -> None:
        """Reject file identity switch even to the other valid snapshot."""
        second = self.root / "current-B.sqlite3"
        second.unlink()
        second.symlink_to(self.root / "current-A.sqlite3")
        with self.assertRaisesRegex(QA.Rejected, "FILE_TYPE"):
            QA.verify(self.root)

    def test_world_readable_copy_refused(self) -> None:
        """Local history may not be exposed to unprivileged accounts."""
        os.chmod(self.root / "current-A.sqlite3", 0o644)
        with self.assertRaisesRegex(QA.Rejected, "PERMISSIONS"):
            QA.verify(self.root)

    def test_manifest_requires_private_mode(self) -> None:
        """An unrestricted source manifest invalidates the approval."""
        os.chmod(self.root / "manifest.json", 0o644)
        with self.assertRaisesRegex(QA.Rejected, "PERMISSIONS"):
            QA.verify(self.root)

    def test_unknown_manifest_kind_refused(self) -> None:
        """Do not interpret arbitrary JSON as a backed-up release."""
        self.manifest["kind"] = "unknown_schema"
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "UNKNOWN_BACKUP_KIND"):
            QA.verify(self.root)

    def test_false_signature_claim_refused(self) -> None:
        """A manifest cannot pretend cosign was verified by this tool."""
        self.manifest["runtime_signature_reverified_in_this_run"] = True
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "SIGNATURE_CLAIM"):
            QA.verify(self.root)

    def test_approval_claim_refused(self) -> None:
        """A local checksum verification cannot count as Owner Approval."""
        self.manifest["release_approval"] = True
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "OWNER_APPROVAL_CLAIM"):
            QA.verify(self.root)

    def test_two_physical_domains_claim_refused(self) -> None:
        """Never claim a second failure domain on a single NVMe."""
        self.manifest["physical_failure_domains"] = 2
        self.update_manifest(self.manifest)
        with self.assertRaisesRegex(QA.Rejected, "DOMAIN_METADATA"):
            QA.verify(self.root)

    def test_clean_cli_explicit_verify(self) -> None:
        """The one explicit read-only flag returns evidence without GO."""
        with (
            patch.object(
                sys, "argv", ["qa", "--verify", "--backup-dir", str(self.root)]
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 0)
        self.assertIn('"release_decision": "NO_GO"', output.getvalue())

    def test_rejected_cli_returns_nonzero(self) -> None:
        """Bad backup state cannot be converted to CLI success."""
        os.chmod(self.root / "manifest.json", 0o644)
        with (
            patch.object(
                sys, "argv", ["qa", "--verify", "--backup-dir", str(self.root)]
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("FAIL_LOCAL_COPY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
