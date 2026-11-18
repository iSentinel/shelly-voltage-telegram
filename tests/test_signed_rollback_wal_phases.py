"""Offline three-phase SQLite WAL backup and rollback parity acceptance."""

from __future__ import annotations

import importlib.util
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/signed-rollback-disposable-rehearsal.py"
SPEC = importlib.util.spec_from_file_location("signed_wal_rehearsal", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class SignedRollbackWalPhaseTests(unittest.TestCase):
    """Prove a WAL-consistent rescue preserves fresh synthetic L1/L2/L3."""

    def setUp(self) -> None:
        """Make a disposable Pro3EM fixture; never use production paths."""

        temp = tempfile.TemporaryDirectory(prefix="svt-pro3em-wal-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.active = self.folder / "active.sqlite3"
        self.rescue = self.folder / "rescue.sqlite3"
        self.bare = self.folder / "bare-main.sqlite3"
        QA.create_fixture(self.active, 10)
        with closing(sqlite3.connect(self.active)) as db, db:
            db.executescript(
                "ALTER TABLE measurements ADD COLUMN phase TEXT "
                "NOT NULL DEFAULT 'L1';"
                "ALTER TABLE measurements ADD COLUMN frequency REAL "
                "NOT NULL DEFAULT 50.0;"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "INSERT INTO meta VALUES ('telegram_setting_meter_type',"
                " 'pro3em');"
            )

    def open_wal(self) -> sqlite3.Connection:
        """Hold one committed phase triplet in uncheckpointed WAL."""

        db = sqlite3.connect(self.active, timeout=5)
        self.addCleanup(db.close)
        self.assertEqual(
            db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal"
        )
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.executemany(
            "INSERT INTO measurements (id,voltage,phase,frequency) "
            "VALUES (?,?,?,?)",
            [
                (11, 211.0, "L1", 50.0),
                (12, 208.0, "L2", 50.0),
                (13, 196.0, "L3", 50.0),
            ],
        )
        db.commit()
        self.assertTrue(Path(str(self.active) + "-wal").is_file())
        return db

    def assert_rescue_metadata(self) -> None:
        """Check recorded phase labels, saved model and PHOTO state."""

        with closing(sqlite3.connect(self.rescue)) as db:
            phases = db.execute(
                "SELECT phase,count(*) FROM measurements "
                "GROUP BY phase ORDER BY phase"
            ).fetchall()
            model = db.execute(
                "SELECT value FROM meta WHERE key=?",
                ("telegram_setting_meter_type",),
            ).fetchone()
            media = db.execute(
                "SELECT media_kind,message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(phases, [("L1", 11), ("L2", 1), ("L3", 1)])
        self.assertEqual(model, ("pro3em",))
        self.assertEqual(media, ("photo", 1000))

    def test_sqlite_backup_includes_uncheckpointed_three_phase_wal(self):
        """Online SQLite backup copies committed pages that main file lacks."""

        writer = self.open_wal()
        shutil.copyfile(self.active, self.bare)
        self.assertEqual(QA.fixture_digest(self.bare)[1], 10)
        self.assertEqual(QA.fixture_digest(self.active)[1], 13)
        QA.sqlite_backup(self.active, self.rescue)
        self.assertEqual(
            QA.fixture_digest(self.active), QA.fixture_digest(self.rescue)
        )
        self.assert_rescue_metadata()
        writer.close()
        self.assertEqual(QA.fixture_digest(self.rescue)[1], 13)

    def test_stale_main_file_rescue_refused_before_swap(self):
        """A bare WAL-mode SQLite file cannot replace newer full triplets."""

        writer = self.open_wal()
        current = QA.fixture_digest(self.active)
        shutil.copyfile(self.active, self.rescue)
        self.assertEqual(QA.fixture_digest(self.rescue)[1], 10)
        writer.close()
        with self.assertRaisesRegex(QA.Blocked, "RESCUE_HISTORY_MISMATCH"):
            QA.recover_fenced(self.folder, current[0], "0.5.25-hw")
        self.assertEqual(QA.fixture_digest(self.active), current)

    def test_fenced_recovery_restores_wal_triplets_and_photo(self):
        """Only verified current rescue can restore phases and saved model."""

        writer = self.open_wal()
        current = QA.fixture_digest(self.active)
        QA.sqlite_backup(self.active, self.rescue)
        writer.close()
        with closing(sqlite3.connect(self.active)) as db, db:
            db.execute("UPDATE measurements SET phase='L1' WHERE id=13")
            db.execute(
                "UPDATE meta SET value='pro_em50' "
                "WHERE key='telegram_setting_meter_type'"
            )
        QA.recover_fenced(self.folder, current[0], "0.5.25-hw")
        self.assertEqual(QA.fixture_digest(self.active), current)
        self.assert_rescue_metadata()
        self.assertEqual(
            (self.folder / "journal.json")
            .read_text()
            .count("RECOVERED_NO_BOT_START"),
            1,
        )

    def test_open_wal_writer_blocks_atomic_restore_before_swap(self):
        """Fixture writer lock cannot make open SQLite files safe."""

        writer = self.open_wal()
        current = QA.fixture_digest(self.active)
        QA.sqlite_backup(self.active, self.rescue)
        with self.assertRaisesRegex(
            QA.Blocked, "ACTIVE_SQLITE_WAL_SIDECAR_UNFENCED"
        ):
            QA.recover_fenced(self.folder, current[0], "0.5.25-hw")
        self.assertEqual(QA.fixture_digest(self.active), current)
        self.assertFalse((self.folder / "journal.json").exists())
        writer.close()

    def test_linked_wal_sidecar_fails_closed(self):
        """Reject untrusted SQLite sidecar paths before an atomic swap."""

        QA.sqlite_backup(self.active, self.rescue)
        expected = QA.fixture_digest(self.active)
        sidecar = Path(str(self.active) + "-wal")
        sidecar.symlink_to(self.folder / "untrusted-wal")
        with self.assertRaisesRegex(
            QA.Blocked, "ACTIVE_SQLITE_WAL_SIDECAR_UNFENCED"
        ):
            QA.recover_fenced(self.folder, expected[0], "0.5.25-hw")
        self.assertFalse((self.folder / "journal.json").exists())
        self.assertEqual(QA.fixture_digest(self.rescue), expected)

    def test_uncommitted_wal_writes_are_not_in_backup(self):
        """An open transaction is never promoted to confirmed history."""

        writer = self.open_wal()
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO measurements (id,voltage,phase,frequency) "
            "VALUES (14,192.0,'L3',50.0)"
        )
        QA.sqlite_backup(self.active, self.rescue)
        self.assertEqual(QA.fixture_digest(self.rescue)[1], 13)
        writer.rollback()
        self.assertEqual(
            QA.fixture_digest(self.active), QA.fixture_digest(self.rescue)
        )


if __name__ == "__main__":
    unittest.main()
