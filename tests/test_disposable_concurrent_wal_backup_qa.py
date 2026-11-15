"""Strict tests for concurrent WAL snapshots and local-only restoration."""

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
SCRIPT = ROOT / "scripts/disposable-concurrent-wal-backup-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_concurrent_wal", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class ConcurrentWalBackupTests(unittest.TestCase):
    """Use only invented SQLite databases, no production files or Docker."""

    def setUp(self) -> None:
        """Each case owns a private directory discarded after the test."""
        temp = tempfile.TemporaryDirectory(prefix="qa-concurrent-wal-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = QA.load_backup_qa()

    def cli(self, *args: str) -> int:
        """Default and rejected consent paths cannot inspect actual host."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_backup_qa", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """No arguments must not create a writer, database or backup."""
        self.assertEqual(self.cli(), 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_untrusted_confirmation_refused(self) -> None:
        """A generic yes never starts a networkless integration run."""
        self.assertEqual(self.cli("--run-disposable", "--confirm", "yes"), 2)

    def test_only_adjacent_backup_qa_loaded(self) -> None:
        """No mutable production or remote helper can be imported."""
        self.assertEqual(QA.BASE.parent, SCRIPT.parent)
        self.assertTrue(QA.BASE.is_file())

    def test_valid_snapshot_row_sequence(self) -> None:
        """Checks guarantee no torn or skipped integer sample IDs."""
        QA.validate_arithmetic(("ok", 422, 422 * 423 // 2, 422))

    def test_torn_sequence_rejected(self) -> None:
        """A snapshot with missing rows cannot pass merely on quick_check."""
        with self.assertRaisesRegex(RuntimeError, "TORN_ROW_SEQUENCE"):
            QA.validate_arithmetic(("ok", 422, 100, 422))

    def test_wrong_max_id_rejected(self) -> None:
        """Max ID inconsistent with count must fail strict state checks."""
        with self.assertRaisesRegex(RuntimeError, "TORN_ROW_SEQUENCE"):
            QA.validate_arithmetic(("ok", 422, 422 * 423 // 2, 425))

    def test_sqlite_quick_check_required(self) -> None:
        """A corrupt snapshot cannot be interpreted as valid data."""
        with self.assertRaisesRegex(RuntimeError, "NOT_CONSISTENT"):
            QA.validate_arithmetic(("broken", 422, 89300, 422))

    def test_empty_or_baseline_only_copy_rejected(self) -> None:
        """A missing concurrent writer must not count as full acceptance."""
        with self.assertRaisesRegex(RuntimeError, "NOT_CONSISTENT"):
            QA.validate_arithmetic(("ok", 420, 88410, 420))

    def test_real_two_concurrent_snapshots_integrity(self) -> None:
        """Actual different WAL snapshot boundaries each restore cleanly."""
        result = QA.backup_during_writes(self.root, self.base)
        self.assertEqual(
            result["result"], "PASS_CONCURRENT_WAL_TWO_LOCAL_RESTORES"
        )
        self.assertGreater(result["snapshot_A_rows"], 420)
        self.assertGreater(
            result["snapshot_B_rows"], result["snapshot_A_rows"]
        )
        self.assertTrue(result["both_independent_local_restores"])
        self.assertEqual(result["release_decision"], "NO_GO")

    def test_each_snapshot_has_different_valid_identity(self) -> None:
        """Two online backups can represent two different commit boundaries."""
        QA.backup_during_writes(self.root, self.base)
        first = self.root / "concurrent-A.sqlite3"
        second = self.root / "concurrent-B.sqlite3"
        a = QA.snapshot_state(first, self.base)
        b = QA.snapshot_state(second, self.base)
        QA.validate_arithmetic(a)
        QA.validate_arithmetic(b)
        self.assertLess(a[1], b[1])

    def test_tampered_snapshot_cannot_recover(self) -> None:
        """Each snapshot must carry its own SHA256 before copy."""
        QA.backup_during_writes(self.root, self.base)
        snapshot = self.root / "concurrent-A.sqlite3"
        expected = QA.snapshot_state(snapshot, self.base)
        manifest = {
            "source_sqlite_state": list(expected),
            "local_snapshot_sha256": {
                snapshot.name: self.base.sha256_file(snapshot)
            },
        }
        with snapshot.open("ab") as stream:
            stream.write(b"extra")
        with self.assertRaisesRegex(
            self.base.BackupRejected, "CHECKSUM_MISMATCH"
        ):
            self.base.verify_snapshot(
                snapshot, self.root / "tampered-restore.sqlite3", manifest
            )

    def test_stale_live_count_cannot_replace_per_copy_state(self) -> None:
        """A later live count cannot be used as an older copy identity."""
        QA.backup_during_writes(self.root, self.base)
        first = self.root / "concurrent-A.sqlite3"
        second = self.root / "concurrent-B.sqlite3"
        old_state = QA.snapshot_state(first, self.base)
        newer_state = QA.snapshot_state(second, self.base)
        self.assertNotEqual(old_state, newer_state)
        manifest = {
            "source_sqlite_state": list(newer_state),
            "local_snapshot_sha256": {
                first.name: self.base.sha256_file(first)
            },
        }
        with self.assertRaisesRegex(
            self.base.BackupRejected, "STATE_STALE_OR_INVALID"
        ):
            self.base.verify_snapshot(
                first, self.root / "wrong-boundary.sqlite3", manifest
            )

    def test_thread_stops_after_backup(self) -> None:
        """The writer must never leak a background thread after QA."""
        before = set(__import__("threading").enumerate())
        QA.backup_during_writes(self.root, self.base)
        after = set(__import__("threading").enumerate())
        self.assertEqual(before, after)

    def test_source_wal_is_active_during_writer(self) -> None:
        """Synthetic source keeps WAL and does not copy a raw live db file."""
        result = QA.backup_during_writes(self.root, self.base)
        self.assertTrue(result["both_integrity_ok"])
        self.assertTrue(result["no_torn_row_sequence"])
        self.assertTrue(result["single_nvme_failure_risk"])
        self.assertFalse(result["real_production_database_touched"])

    def test_main_runs_guard_before_integration(self) -> None:
        """Exact consent must pass the fixed guest guard before writing."""
        seen: list[str] = []
        fake = type(
            "QA",
            (),
            {
                "guest_guard": lambda self: seen.append("guard"),
            },
        )()
        with (
            patch.object(
                sys,
                "argv",
                ["qa", "--run-disposable", "--confirm", QA.CONSENT],
            ),
            patch.object(QA, "load_backup_qa", return_value=fake),
            patch.object(
                QA,
                "backup_during_writes",
                side_effect=lambda *_: seen.append("run") or {"ok": True},
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 0)
        self.assertEqual(seen, ["guard", "run"])

    def test_unexpected_sqlite_failure_fails_closed(self) -> None:
        """A backup error must not be converted to a release approval."""
        fake = type("QA", (), {"guest_guard": lambda self: None})()
        with (
            patch.object(
                sys,
                "argv",
                ["qa", "--run-disposable", "--confirm", QA.CONSENT],
            ),
            patch.object(QA, "load_backup_qa", return_value=fake),
            patch.object(
                QA,
                "backup_during_writes",
                side_effect=sqlite3.OperationalError("invented"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("QA_BLOCKED", out.getvalue())

    def test_negative_manifest_cannot_claim_signature(self) -> None:
        """No synthetic rollback test is a signed production restore."""
        result = QA.backup_during_writes(self.root, self.base)
        output = json.dumps(result)
        self.assertIn('"release_decision": "NO_GO"', output)
        self.assertIn('"single_nvme_failure_risk": true', output)


if __name__ == "__main__":
    unittest.main()
