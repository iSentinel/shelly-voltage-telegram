"""Fail closed for stale, altered or corrupt disposable SQLite rollbacks."""

from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-rollback-history-parity-qa.py"
)
SPEC = importlib.util.spec_from_file_location("rollback_parity_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class RollbackParityTests(unittest.TestCase):
    """Compare only artificial private SQLite databases and gzip backups."""

    def setUp(self) -> None:
        """Create a 0700 fixture with a 120-row private current and backup."""
        temporary = tempfile.TemporaryDirectory(
            dir="/tmp", prefix="svt-rollback-history-qa-"
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / QA.MARKER).write_text(QA.MARKER_VALUE)
        current = self.root / QA.DATABASE
        with closing(sqlite3.connect(current)) as db:
            db.execute("PRAGMA user_version=7")
            db.execute(
                "CREATE TABLE measurements("
                "id INTEGER PRIMARY KEY,ts REAL,voltage REAL,phase TEXT)"
            )
            db.execute(
                "CREATE TABLE outages(id INTEGER, start REAL, end REAL)"
            )
            db.execute("CREATE TABLE meta(k TEXT, v TEXT)")
            db.executemany(
                "INSERT INTO measurements(ts,voltage,phase) VALUES(?,?,?)",
                [(i, 225.0 + i % 8, "L1") for i in range(120)],
            )
            db.execute("INSERT INTO outages VALUES(30,10,20)")
            db.execute("INSERT INTO meta VALUES('qa','scratch')")
            db.commit()
        current.chmod(0o600)
        self.create_archive()

    def create_archive(self) -> None:
        """Capture a real SQLite backup API image into a private gzip."""
        current = self.root / QA.DATABASE
        backed_up = self.root / "source-of-archive.sqlite3"
        with (
            closing(sqlite3.connect(current)) as source,
            closing(sqlite3.connect(backed_up)) as target,
        ):
            source.backup(target)
        with (
            backed_up.open("rb") as source,
            gzip.open(self.root / QA.ARCHIVE, "wb") as target,
        ):
            target.write(source.read())
        backed_up.unlink()
        (self.root / QA.ARCHIVE).chmod(0o600)

    def run_cli(self, *options: str) -> tuple[int, str]:
        """Capture the exit code and structured command output."""
        with (
            patch.object(
                sys, "argv", [str(SCRIPT), "--root", str(self.root), *options]
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            result = QA.main()
        return result, output.getvalue()

    def update_current(self, statement: str) -> None:
        """Mutate only the disposable live-history fixture."""
        with closing(sqlite3.connect(self.root / QA.DATABASE)) as db:
            db.execute(statement)
            db.commit()

    def test_exact_history_parity_passes_without_authorization(self) -> None:
        """Identical source and archive are only a QA equality pass."""
        status = QA.audit(self.root)
        self.assertEqual(status["status"], "PARITY_PASS")
        self.assertEqual(status["live_rows"], 120)
        self.assertEqual(status["archive_rows"], 120)
        self.assertFalse(status["rollback_authorized"])
        self.assertFalse(status["production_modified"])

    def test_newer_measurement_fails_closed(self) -> None:
        """Never accept a backup older than the current SQLite history."""
        self.update_current(
            "INSERT INTO measurements(ts,voltage,phase) VALUES(121,224.5,'L1')"
        )
        status = QA.audit(self.root)
        self.assertEqual(status["status"], "BLOCKED")
        self.assertEqual(status["archive_rows"], 120)
        self.assertEqual(status["live_rows"], 121)
        self.assertFalse(status["rollback_authorized"])

    def test_changed_voltage_with_equal_counts_blocks(self) -> None:
        """Matching row counts cannot hide changed measurement values."""
        self.update_current("UPDATE measurements SET voltage=111.1 WHERE id=7")
        status = QA.audit(self.root)
        self.assertEqual(status["live_rows"], status["archive_rows"])
        self.assertEqual(status["status"], "BLOCKED")

    def test_new_committed_wal_row_blocks(self) -> None:
        """A fresh row in SQLite WAL is part of current logical history."""
        with closing(sqlite3.connect(self.root / QA.DATABASE)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "INSERT INTO measurements(ts,voltage,phase)"
                " VALUES(121,222.2,'L1')"
            )
            db.commit()
            result = QA.audit(self.root)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["live_rows"], 121)
        self.assertEqual(result["archive_rows"], 120)

    def test_deleted_row_blocks(self) -> None:
        """A row deleted after snapshot also invalidates parity."""
        self.update_current("DELETE FROM measurements WHERE id=1")
        self.assertEqual(QA.audit(self.root)["status"], "BLOCKED")

    def test_new_outage_blocks(self) -> None:
        """All tables are considered, not just the measurement count."""
        self.update_current("INSERT INTO outages VALUES(31,30,NULL)")
        self.assertEqual(QA.audit(self.root)["status"], "BLOCKED")

    def test_meta_change_blocks(self) -> None:
        """Updated nonmeasurement metadata must invalidate rollback."""
        self.update_current("UPDATE meta SET v='changed' WHERE k='qa'")
        self.assertEqual(QA.audit(self.root)["status"], "BLOCKED")

    def test_changed_schema_blocks(self) -> None:
        """Added tables or indexes cannot be silently reverted."""
        self.update_current("CREATE TABLE new_data(x INTEGER)")
        self.assertEqual(QA.audit(self.root)["status"], "BLOCKED")

    def test_changed_sqlite_header_blocks(self) -> None:
        """User-version changes matter even with identical row content."""
        self.update_current("PRAGMA user_version=8")
        self.assertEqual(QA.audit(self.root)["status"], "BLOCKED")

    def test_dry_run_does_not_open_database(self) -> None:
        """Default audit mode must remain non-mutating."""
        with patch.object(QA, "audit", side_effect=AssertionError):
            self.assertEqual(self.run_cli()[0], 0)
            self.assertEqual(self.run_cli("--dry-run")[0], 0)

    def test_cli_different_history_returns_three(self) -> None:
        """A mismatched snapshot receives stable BLOCKED exit code."""
        self.update_current("INSERT INTO outages VALUES(31,30,NULL)")
        exit_code, content = self.run_cli("--audit-disposable")
        self.assertEqual(exit_code, 3)
        self.assertIn('"rollback_authorized": false', content)

    def test_cli_equal_history_returns_zero(self) -> None:
        """Equality is reported without granting production authority."""
        exit_code, content = self.run_cli("--audit-disposable")
        self.assertEqual(exit_code, 0)
        self.assertIn('"production_modified": false', content)

    def test_production_path_forbidden(self) -> None:
        """No production source or database path can be selected."""
        with self.assertRaises(QA.Blocked):
            QA.require_root(Path("/var/lib/shelly-voltage"))

    def test_root_symlink_is_refused(self) -> None:
        """A linked scratch root cannot bypass the /tmp guard."""
        link = self.root.with_name(self.root.name + "-link")
        link.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(link.unlink)
        with self.assertRaises(QA.Blocked):
            QA.require_root(link)

    def test_public_root_permissions_block(self) -> None:
        """Only a private 0700 scratch root may hold the fixture."""
        self.root.chmod(0o755)
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_missing_marker_blocks(self) -> None:
        """A private directory alone is not an authorized QA fixture."""
        (self.root / QA.MARKER).unlink()
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_invalid_marker_blocks(self) -> None:
        """Reject a changed QA-only consent marker."""
        (self.root / QA.MARKER).write_text("wrong")
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_linked_database_blocks(self) -> None:
        """Never follow a symlink to read a potentially live database."""
        path = self.root / QA.DATABASE
        original = self.root / "saved-current.db"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_linked_archive_blocks(self) -> None:
        """Refuse symlinked backup files despite a valid scratch root."""
        path = self.root / QA.ARCHIVE
        original = self.root / "saved-archive.gz"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_hardlinked_sqlite_blocks(self) -> None:
        """A second hardlink to the current DB must fail closed."""
        os.link(self.root / QA.DATABASE, self.root / "extra-hardlink.db")
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_public_archive_permissions_block(self) -> None:
        """Archives must remain private 0600 files."""
        (self.root / QA.ARCHIVE).chmod(0o644)
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_bad_gzip_blocks(self) -> None:
        """Malformed gzip cannot be interpreted as a healthy rollback."""
        (self.root / QA.ARCHIVE).write_bytes(b"invalid archive")
        exit_code, content = self.run_cli("--audit-disposable")
        self.assertEqual(exit_code, 3)
        self.assertIn('"status": "BLOCKED"', content)

    def test_oversized_archive_blocks(self) -> None:
        """The explicit compressed-size limit cannot be skipped."""
        with (
            patch.object(QA, "MAX_ARCHIVE_BYTES", 1),
            self.assertRaises(QA.Blocked),
        ):
            QA.audit(self.root)

    def test_expansion_limit_blocks(self) -> None:
        """Oversized decompressed SQLite data must be rejected."""
        with (
            patch.object(QA, "MAX_RESTORED_BYTES", 100),
            self.assertRaises(QA.Blocked),
        ):
            QA.audit(self.root)

    def test_missing_measurements_table_blocks(self) -> None:
        """A database without measurement schema cannot be accepted."""
        self.update_current("DROP TABLE measurements")
        with self.assertRaises(QA.Blocked):
            QA.audit(self.root)

    def test_temp_restores_are_removed(self) -> None:
        """Never leave extracted backup files in a private fixture."""
        QA.audit(self.root)
        self.assertFalse(list(self.root.glob(".rollback-expanded-*")))

    def test_failed_expansion_cleans_temp_file(self) -> None:
        """A bounded expansion error still removes temporary SQLite."""
        with (
            patch.object(QA, "MAX_RESTORED_BYTES", 100),
            self.assertRaises(QA.Blocked),
        ):
            QA.audit(self.root)
        self.assertFalse(list(self.root.glob(".rollback-expanded-*")))


if __name__ == "__main__":
    unittest.main()
