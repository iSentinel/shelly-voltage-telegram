"""Validate pending-update history guard using only temporary SQLite files."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/pending-recovery-history-guard.py"
)
SPEC = importlib.util.spec_from_file_location("pending_history_guard", SCRIPT)
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class PendingHistoryGuardTests(unittest.TestCase):
    """Enforce logical parity before any synthetic rollback decision."""

    def setUp(self) -> None:
        """Create a committed, owner-private synthetic SQLite snapshot."""
        temporary = tempfile.TemporaryDirectory(
            prefix="svt-pending-history-qa-", dir="/tmp"
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / "current.sqlite3"
        self.archive = self.root / "historical.sqlite3.gz"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA user_version=7")
            db.execute("CREATE TABLE samples(id INTEGER,voltage REAL)")
            db.execute("CREATE TABLE outages(id INTEGER,start REAL,end REAL)")
            db.execute("CREATE TABLE config(k TEXT,v TEXT)")
            db.executemany(
                "INSERT INTO samples VALUES(?,?)",
                [(i, 220.0 + i % 7) for i in range(120)],
            )
            db.execute("INSERT INTO outages VALUES(30,10,20)")
            db.execute("INSERT INTO config VALUES('qa','only')")
            db.commit()
        self.capture_archive()

    def capture_archive(self) -> None:
        """Store an isolated full committed SQLite backup in gzip."""
        backed_up = self.root / "captured.sqlite3"
        with (
            closing(sqlite3.connect(self.database)) as source,
            closing(sqlite3.connect(backed_up)) as target,
        ):
            source.backup(target)
        with (
            backed_up.open("rb") as source,
            gzip.open(self.archive, "wb") as destination,
        ):
            destination.write(source.read())
        backed_up.unlink()

    def mutate(self, query: str) -> None:
        """Change only the disposable active history."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(query)
            db.commit()

    def call_cli(self) -> subprocess.CompletedProcess:
        """Run the real helper process on scratch paths only."""
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(self.database),
                "--archive",
                str(self.archive),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )

    def test_equal_snapshot_passes_without_database_replacement(self) -> None:
        """The archive and current history must match exactly."""
        inode = self.database.stat().st_ino
        content = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.assertEqual(self.call_cli().returncode, 0)
        self.assertEqual(self.database.stat().st_ino, inode)
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).hexdigest(), content
        )

    def test_new_rows_fail(self) -> None:
        """An extra committed sample cannot be lost during image rewind."""
        self.mutate("INSERT INTO samples VALUES(121,224.2)")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_equal_count_changed_voltage_fails(self) -> None:
        """Row counts alone must never authorize a rollback."""
        self.mutate("UPDATE samples SET voltage=111 WHERE id=3")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_new_outage_fails(self) -> None:
        """Nonmeasurement rows are covered by the full logical hash."""
        self.mutate("INSERT INTO outages VALUES(31,30,NULL)")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_metadata_change_fails(self) -> None:
        """Configuration entries must be included in source comparison."""
        self.mutate("UPDATE config SET v='changed' WHERE k='qa'")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_schema_change_fails(self) -> None:
        """Different schemas cannot be restored without explicit review."""
        self.mutate("CREATE TABLE extra(value TEXT)")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_header_change_fails(self) -> None:
        """SQLite user_version is part of history parity."""
        self.mutate("PRAGMA user_version=8")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_committed_wal_change_fails(self) -> None:
        """Committed WAL-only changes are compared against the archive."""
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA wal_autocheckpoint=0")
            db.execute("INSERT INTO samples VALUES(121,221.8)")
            db.commit()
            self.assertTrue(Path(str(self.database) + "-wal").exists())
            self.assertEqual(self.call_cli().returncode, 3)

    def test_corrupted_gzip_fails(self) -> None:
        """Neither malformed input nor truncation can pass."""
        self.archive.write_bytes(b"not gzip")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_truncated_gzip_fails(self) -> None:
        """An incomplete stream can never be accepted."""
        content = self.archive.read_bytes()
        self.archive.write_bytes(content[:25])
        self.assertEqual(self.call_cli().returncode, 3)

    def test_missing_database_fails(self) -> None:
        """Established installations must not silently accept a missing DB."""
        self.database.unlink()
        self.assertEqual(self.call_cli().returncode, 3)

    def test_linked_database_fails(self) -> None:
        """Reject a linked current source to avoid untrusted redirection."""
        original = self.root / "original.sqlite3"
        self.database.rename(original)
        self.database.symlink_to(original)
        self.assertEqual(self.call_cli().returncode, 3)

    def test_hardlinked_database_fails(self) -> None:
        """The source must not have another hardlink."""
        os.link(self.database, self.root / "aliased.sqlite3")
        self.assertEqual(self.call_cli().returncode, 3)

    def test_linked_archive_fails(self) -> None:
        """A symlinked archive may not be used for recovery."""
        original = self.root / "archive-real.gz"
        self.archive.rename(original)
        self.archive.symlink_to(original)
        self.assertEqual(self.call_cli().returncode, 3)

    def test_limit_rejects_excess_decompression(self) -> None:
        """The expanded SQLite size is capped."""
        with (
            patch.object(GUARD, "MAX_EXPANDED_BYTES", 100),
            self.assertRaises(GUARD.GuardFailure),
        ):
            GUARD.verify_live_parity(self.database, self.archive)

    def test_temporary_restore_is_cleaned(self) -> None:
        """Nothing expanded into backup storage may survive validation."""
        GUARD.verify_live_parity(self.database, self.archive)
        self.assertEqual(list(self.root.glob(".pending-rollback-qa-*")), [])

    def test_failed_restore_is_cleaned(self) -> None:
        """Corrupt input may not leave partial restored SQLite files."""
        self.archive.write_bytes(b"invalid")
        with self.assertRaises(OSError):
            GUARD.verify_live_parity(self.database, self.archive)
        self.assertFalse(list(self.root.glob(".pending-rollback-qa-*")))

    def test_concurrent_change_during_check_fails(self) -> None:
        """Changing current history during the check invalidates parity."""
        baseline = GUARD.sqlite_fingerprint
        calls = 0

        def fingerprint(path: Path) -> str:
            nonlocal calls
            calls += 1
            if calls == 3:
                self.mutate("INSERT INTO samples VALUES(121,220.0)")
            return baseline(path)

        with (
            patch.object(GUARD, "sqlite_fingerprint", side_effect=fingerprint),
            self.assertRaises(GUARD.GuardFailure),
        ):
            GUARD.verify_live_parity(self.database, self.archive)

    def test_cli_help_is_available(self) -> None:
        """Expose an ordinary usable --help switch."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--database", proc.stdout)


if __name__ == "__main__":
    unittest.main()
