"""Exercise disposable online SQLite snapshots without production access."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_snapshot_rehearsal", ROOT / "scripts/em50-snapshot-rehearsal.py"
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def fixture(path: Path) -> None:
    """Create isolated test history, event and Telegram metadata."""

    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(
            "CREATE TABLE measurements ("
            "id INTEGER PRIMARY KEY,ts REAL,voltage REAL,"
            "frequency REAL,phase TEXT);"
            "CREATE TABLE outages ("
            "id INTEGER PRIMARY KEY,started_at REAL,"
            "ended_at REAL,phase TEXT);"
            "CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT);"
            "INSERT INTO measurements VALUES (1,100,220,50,'L1');"
            "INSERT INTO measurements VALUES (2,110,221,50,'L1');"
            "INSERT INTO outages VALUES (18,50,90,'L1');"
            "INSERT INTO meta VALUES "
            "('telegram_status_message_id','private-qa-test-value');"
        )


def mutate(path: Path, sql: str) -> None:
    """Apply a post-snapshot change in a disposable database only."""

    with closing(sqlite3.connect(path)) as db, db:
        db.execute(sql)


class SnapshotRehearsalTests(unittest.TestCase):
    """Separate offline source QA from approval of a real rollback."""

    def setUp(self) -> None:
        """Use a temporary SQLite database with no real user identifiers."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source.sqlite3"
        fixture(self.source)

    def test_initial_parity_passes_and_sandbox_is_removed(self):
        """An unchanged online snapshot must have complete row parity."""

        result = QA.rehearsal(self.source)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["record_gap"], 0)
        self.assertTrue(result["measurements_match"])
        self.assertTrue(result["outages_match"])
        self.assertTrue(result["status_metadata_matches"])
        self.assertTrue(result["temporary_copies_removed"])
        self.assertFalse(result["wrote_to_production"])
        self.assertFalse(result["release_authorized"])

    def test_new_measurement_after_snapshot_blocks(self):
        """One new reading invalidates even a previously perfect snapshot."""

        def change() -> None:
            """Insert one additional synthetic measurement."""

            mutate(
                self.source,
                "INSERT INTO measurements VALUES (3,120,222,50,'L1')",
            )

        result = QA.rehearsal(self.source, after_snapshot=change)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["record_gap"], 1)
        self.assertIn("measurements_not_equal", result["reason_codes"])
        self.assertTrue(result["temporary_copies_removed"])

    def test_modified_old_row_with_equal_count_blocks(self):
        """A changed historic measurement must not pass by row count alone."""

        result = QA.rehearsal(
            self.source,
            after_snapshot=lambda: mutate(
                self.source,
                "UPDATE measurements SET voltage=235 WHERE id=1",
            ),
        )
        self.assertEqual(result["record_gap"], 0)
        self.assertFalse(result["measurements_match"])
        self.assertIn("measurements_not_equal", result["reason_codes"])

    def test_outage_edit_after_snapshot_blocks(self):
        """No lost outage history can be approved as matching."""

        result = QA.rehearsal(
            self.source,
            after_snapshot=lambda: mutate(
                self.source, "UPDATE outages SET ended_at=95 WHERE id=18"
            ),
        )
        self.assertFalse(result["outages_match"])
        self.assertIn("outage_state_not_equal", result["reason_codes"])

    def test_telegram_metadata_changed_after_snapshot_blocks(self):
        """A changed status message reference must invalidate parity."""

        result = QA.rehearsal(
            self.source,
            after_snapshot=lambda: mutate(
                self.source,
                "UPDATE meta SET value='different-qa-value'",
            ),
        )
        self.assertFalse(result["status_metadata_matches"])
        self.assertIn(
            "status_message_metadata_not_equal", result["reason_codes"]
        )

    def test_source_symlink_is_rejected(self):
        """Do not follow user-controlled symlinks to the real database."""

        link = self.source.with_name("linked.sqlite3")
        link.symlink_to(self.source)
        with self.assertRaises(ValueError):
            QA.rehearsal(link)

    def test_snapshots_are_private_even_during_compression(self):
        """Both in-flight QA SQLite and compressed backups use 0600."""

        original = QA.compress_snapshot

        def inspect(source: Path, folder: Path) -> None:
            """Check temp permissions before file deletion."""

            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            original(source, folder)
            archive = folder / "voltage-qa-online.sqlite3.gz"
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)

        with patch.object(QA, "compress_snapshot", side_effect=inspect):
            self.assertEqual(QA.rehearsal(self.source)["status"], "PASS")

    def test_dry_run_reads_no_database_or_files(self):
        """Dry-run must never even instantiate an online backup."""

        with (
            patch.object(sys, "argv", ["tool", "--dry-run"]),
            patch.object(QA, "rehearsal") as action,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 0)
        action.assert_not_called()
        self.assertIn("no_files_created=true", output.getvalue())


if __name__ == "__main__":
    unittest.main()
