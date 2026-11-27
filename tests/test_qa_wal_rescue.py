"""Qualify existing SQLite rescue against disposable WAL transactions."""

import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_signed_forward_cutover import CUTOVER, PHOTO


class WalRescueTests(unittest.TestCase):
    """Exercise actual backup API without a deployed database or service."""

    def setUp(self):
        """Keep a synthetic WAL connection open to avoid final checkpoint."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "source.sqlite3"
        self.work = self.root / "rescue"
        self.work.mkdir()
        self.destination = self.work / "database-before.sqlite3"
        self.db = sqlite3.connect(self.source)
        self.addCleanup(self.db.close)
        self.db.executescript(
            "PRAGMA journal_mode=WAL;"
            "PRAGMA wal_autocheckpoint=0;"
            "CREATE TABLE measurements(id INTEGER PRIMARY KEY, v INTEGER);"
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);"
            "INSERT INTO measurements VALUES(1,220);"
        )
        self.db.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [
                ("telegram_status_message_id", PHOTO),
                ("telegram_status_media_identity", f"photo:-123:{PHOTO}"),
            ],
        )
        self.db.commit()
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        self.db.execute("INSERT INTO measurements VALUES(2,221)")
        self.db.execute("PRAGMA user_version=42")
        self.db.commit()
        self.paths = SimpleNamespace(database=self.source)

    def raw_hashes(self):
        """Fingerprint source main and WAL bytes without touching them."""
        return [
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (self.source, self.source.with_suffix(".sqlite3-wal"))
        ]

    def backup(self):
        """Use exactly the existing cutover backup routine."""
        CUTOVER.backup_database(self.paths, self.work, PHOTO)

    def rows(self, path):
        """Read a standalone rescue without assuming any WAL content."""
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
        ) as db:
            return db.execute(
                "SELECT * FROM measurements ORDER BY id"
            ).fetchall()

    def test_committed_wal(self):
        """A main-only read misses a row retained by the real backup."""
        self.assertEqual(self.rows(self.source), [(1, 220)])
        before = self.raw_hashes()
        self.backup()
        self.assertEqual(self.rows(self.destination), [(1, 220), (2, 221)])
        self.assertEqual(self.raw_hashes(), before)
        self.assertFalse(self.destination.with_suffix(".sqlite3-wal").exists())
        with closing(sqlite3.connect(self.destination)) as db:
            self.assertEqual(
                db.execute("PRAGMA user_version").fetchone(), (42,)
            )

    def test_uncommitted_excluded(self):
        """A separate uncommitted writer transaction is never rescued."""
        self.db.execute("INSERT INTO measurements VALUES(3,222)")
        before = self.raw_hashes()
        self.backup()
        self.assertEqual(self.rows(self.destination), [(1, 220), (2, 221)])
        self.assertEqual(self.raw_hashes(), before)
        self.assertTrue(self.db.in_transaction)

    def test_pending_intent_blocks_cutover(self):
        """A committed WAL intent is retained but prevents cutover."""
        key = "telegram_report_pending:daily_summary_day"
        self.db.execute("INSERT INTO meta VALUES (?,?)", (key, "synthetic"))
        self.db.commit()
        before = self.raw_hashes()
        with self.assertRaisesRegex(ValueError, "report delivery"):
            self.backup()
        with closing(
            sqlite3.connect(
                self.destination.as_uri() + "?mode=ro&immutable=1", uri=True
            )
        ) as db:
            self.assertEqual(
                db.execute(
                    "SELECT value FROM meta WHERE key=?", (key,)
                ).fetchone(),
                ("synthetic",),
            )
        self.assertEqual(self.raw_hashes(), before)
        self.assertFalse(
            self.destination.with_suffix(".sqlite3.sha256").exists()
        )

    def test_existing_rescue_refused(self):
        """A second capture cannot overwrite an earlier rescue."""
        self.backup()
        before = self.destination.read_bytes()
        with self.assertRaises(FileExistsError):
            self.backup()
        self.assertEqual(self.destination.read_bytes(), before)

    def test_seal_standalone(self):
        """Only a standalone, checked rescue receives a checksum."""
        self.backup()
        with patch.object(CUTOVER, "safe_path", side_effect=Path.lstat):
            CUTOVER.seal_rollback_rescue(self.work, PHOTO)
        expected = hashlib.sha256(self.destination.read_bytes()).hexdigest()
        self.assertEqual(
            self.destination.with_suffix(".sqlite3.sha256").read_text(),
            f"{expected}  {self.destination.name}\n",
        )

    def test_seal_sidecars_refused(self):
        """Immutable reads must not ignore a present WAL or journal."""
        self.backup()
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.destination) + suffix)
            sidecar.write_bytes(b"synthetic")
            with (
                self.subTest(suffix=suffix),
                patch.object(CUTOVER, "safe_path", side_effect=Path.lstat),
                self.assertRaisesRegex(ValueError, "SQLite sidecars"),
            ):
                CUTOVER.seal_rollback_rescue(self.work, PHOTO)
            self.assertFalse(
                self.destination.with_suffix(".sqlite3.sha256").exists()
            )
            sidecar.unlink()

    def test_seal_changed_identity_refused(self):
        """A changed rescue between inspection and hashing is rejected."""
        self.backup()
        with (
            patch.object(
                CUTOVER,
                "standalone_rescue_identity",
                side_effect=[(1, 2, 3, 4), (1, 2, 3, 5)],
            ),
            self.assertRaisesRegex(ValueError, "changed during review"),
        ):
            CUTOVER.seal_rollback_rescue(self.work, PHOTO)
        self.assertFalse(
            self.destination.with_suffix(".sqlite3.sha256").exists()
        )
