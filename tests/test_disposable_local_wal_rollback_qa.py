"""Strict tests for disposable local-only WAL backup and twin restore."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/disposable-local-wal-rollback-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_local_wal_rollback", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class LocalWalRollbackTests(unittest.TestCase):
    """Use only temporary synthetic SQLite, never live production SQLite."""

    def setUp(self) -> None:
        """Create one private scratch directory per test."""
        temp = tempfile.TemporaryDirectory(prefix="local-wal-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def cli(self, *args: str) -> int:
        """Default and refusal paths must never consult actual host state."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "guest_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_dry_run_inert(self) -> None:
        """No flags cannot start a backup, write files or contact Shelly."""
        self.assertEqual(self.cli(), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_consent_fail_closed(self) -> None:
        """Only exact offline confirmation activates actual QA integration."""
        self.assertEqual(self.cli("--run-disposable", "--confirm", "yes"), 2)

    def test_guard_wrong_host_refused(self) -> None:
        """A production host must never run this synthetic setup."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(QA.socket, "gethostname", return_value="production"),
            self.assertRaisesRegex(QA.BackupRejected, "WRONG_GUEST"),
        ):
            QA.guest_guard()

    def test_guard_nonroot_refused(self) -> None:
        """Only controlled QA root may execute integration scenario."""
        with (
            patch.object(QA.os, "geteuid", return_value=65534),
            self.assertRaisesRegex(QA.BackupRejected, "ROOT_REQUIRED"),
        ):
            QA.guest_guard()

    def test_guard_external_network_refused(self) -> None:
        """Real network interfaces must prohibit disposable integration."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(QA.BackupRejected, "NETWORK_PRESENT"),
        ):
            QA.guest_guard()

    def test_invented_source_has_active_wal(self) -> None:
        """A created SQLite WAL must contain committed test transactions."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            wal = self.root / "running.sqlite3-wal"
            self.assertGreater(wal.stat().st_size, 0)
            self.assertEqual(
                QA.sqlite_state(db),
                (
                    "ok",
                    420,
                    88410,
                    420,
                ),
            )

    def test_online_copy_is_atomic_private(self) -> None:
        """Only a 0600 completed snapshot is left, not partial files."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            snapshot = self.root / "local-A.sqlite3"
            QA.create_online_backup(db, snapshot)
            self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
            self.assertFalse(snapshot.with_suffix(".partial").exists())
            with contextlib.closing(sqlite3.connect(snapshot)) as restored:
                self.assertEqual(
                    QA.sqlite_state(restored), QA.sqlite_state(db)
                )

    def test_overwrite_of_existing_snapshot_refused(self) -> None:
        """A repeated backup must never silently replace accepted data."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            path = self.root / "local-A.sqlite3"
            QA.create_online_backup(db, path)
            with self.assertRaisesRegex(
                QA.BackupRejected, "PATH_ALREADY_EXISTS"
            ):
                QA.create_online_backup(db, path)

    def test_manifest_contains_only_qa_identity(self) -> None:
        """Source digest and cached image ID are not signature verification."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            a = self.root / "local-A.sqlite3"
            b = self.root / "local-B.sqlite3"
            for file in (a, b):
                QA.create_online_backup(db, file)
            manifest = QA.fixture_manifest(self.root, db, (a, b))
        self.assertEqual(
            manifest["source_sqlite_state"],
            [
                "ok",
                420,
                88410,
                420,
            ],
        )
        self.assertFalse(manifest["signature_verified"])
        self.assertFalse(manifest["physical_independence"])
        self.assertTrue(manifest["image_id"].startswith("sha256:"))
        self.assertEqual(len(manifest["local_snapshot_sha256"]), 2)

    def test_manifest_is_private_and_atomic(self) -> None:
        """A completed manifest must be mode0600 and valid JSON."""
        path = self.root / "manifest.json"
        QA.write_manifest(path, {"kind": "synthetic_qa_only"})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            json.loads(path.read_text())["kind"], "synthetic_qa_only"
        )
        self.assertFalse(path.with_suffix(".partial").exists())

    def test_manifest_overwrite_refused(self) -> None:
        """No changing a verified backup manifest in place."""
        path = self.root / "manifest.json"
        QA.write_manifest(path, {"kind": "first"})
        with self.assertRaisesRegex(QA.BackupRejected, "ALREADY_EXISTS"):
            QA.write_manifest(path, {"kind": "second"})

    def test_two_independent_local_restore_passes(self) -> None:
        """Both local snapshots restore to distinct disposable paths."""
        result = QA.run_once(self.root)
        self.assertEqual(result["result"], "PASS_LOCAL_ONLY_WAL_TWO_RESTORES")
        self.assertEqual(result["two_separate_restores"], 2)
        self.assertEqual(result["state"], ["ok", 420, 88410, 420])
        self.assertTrue(result["both_equal"])
        self.assertEqual(result["release_decision"], "NO_GO")
        self.assertFalse(result["physically_independent"])

    def test_corrupted_backup_rejected_by_hash(self) -> None:
        """No checksum mismatch may count as a successful restoration."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            snapshot = self.root / "local-A.sqlite3"
            QA.create_online_backup(db, snapshot)
            manifest = QA.fixture_manifest(self.root, db, (snapshot, snapshot))
            QA.assert_corrupt_rejected(self.root, snapshot, manifest)
        self.assertFalse((self.root / "rejected-restore.sqlite3").exists())

    def test_stale_snapshot_rejected_by_row_identity(self) -> None:
        """A stale manifest row count is not the current accepted state."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            snapshot = self.root / "local-B.sqlite3"
            QA.create_online_backup(db, snapshot)
            manifest = QA.fixture_manifest(self.root, db, (snapshot, snapshot))
            QA.assert_stale_rejected(self.root, snapshot, manifest)

    def test_changed_snapshot_bytes_block_restore(self) -> None:
        """A tampered backing SQLite must not be copied for recovery."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            snapshot = self.root / "local-A.sqlite3"
            QA.create_online_backup(db, snapshot)
            manifest = QA.fixture_manifest(self.root, db, (snapshot, snapshot))
        with snapshot.open("ab") as out:
            out.write(b"tampered")
        with self.assertRaisesRegex(QA.BackupRejected, "CHECKSUM_MISMATCH"):
            QA.verify_snapshot(
                snapshot, self.root / "restored.sqlite3", manifest
            )
        self.assertFalse((self.root / "restored.sqlite3").exists())

    def test_restore_target_already_exists_rejected(self) -> None:
        """Never overwrite the destination of a previous restore."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            snapshot = self.root / "local-A.sqlite3"
            QA.create_online_backup(db, snapshot)
            manifest = QA.fixture_manifest(self.root, db, (snapshot, snapshot))
        path = self.root / "restored.sqlite3"
        path.touch()
        with self.assertRaisesRegex(QA.BackupRejected, "RESTORE_PATH_EXISTS"):
            QA.verify_snapshot(snapshot, path, manifest)

    def test_read_only_backup_does_not_change_primary(self) -> None:
        """Actual backup API does not overwrite or close live source."""
        with contextlib.closing(QA.make_source(self.root)) as db:
            before = QA.sqlite_state(db)
            QA.create_online_backup(db, self.root / "backup.sqlite3")
            after = QA.sqlite_state(db)
        self.assertEqual(before, after)

    def test_full_qa_harness_never_claims_signed_release(self) -> None:
        """Synthetic identity must not imply cosign/Owner Approval."""
        result = QA.run_once(self.root)
        self.assertFalse(result["qa_image_signature_verified"])
        self.assertFalse(result["production_sqlite_accessed"])
        self.assertEqual(result["release_decision"], "NO_GO")


if __name__ == "__main__":
    unittest.main()
