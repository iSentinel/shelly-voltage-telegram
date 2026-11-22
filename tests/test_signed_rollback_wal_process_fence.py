"""Verify real cross-process SQLite WAL writers block fixture rollback."""

from __future__ import annotations

import importlib.util
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/signed-rollback-disposable-rehearsal.py"
SPEC = importlib.util.spec_from_file_location("wal_process_fence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)

CHILD = """
from pathlib import Path
import fcntl, os, sqlite3, sys
root = Path(sys.argv[1])
lock = (root / 'synthetic-writer.lock').open('w+')
fcntl.flock(lock, fcntl.LOCK_EX)
db = sqlite3.connect(root / 'active.sqlite3', timeout=3)
assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
db.execute('PRAGMA wal_autocheckpoint=0')
db.executemany(
 'INSERT INTO measurements (id,voltage,phase,frequency) VALUES (?,?,?,?)',
 [(11,214.0,'L1',50.0),(12,211.0,'L2',50.0),(13,199.0,'L3',50.0)]
)
db.commit()
(root / 'writer-ready').write_text('READY')
sys.stdin.readline()
db.close()
lock.close()
"""


class CrossProcessWalFenceTests(unittest.TestCase):
    """Never touch production paths, devices, or Telegram."""

    def setUp(self) -> None:
        """Create a private three-phase SQLite fixture."""

        temp = tempfile.TemporaryDirectory(prefix="svt-wal-process-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.active = self.root / "active.sqlite3"
        self.rescue = self.root / "rescue.sqlite3"
        QA.create_fixture(self.active, 10)
        with closing(sqlite3.connect(self.active)) as db, db:
            db.executescript(
                "ALTER TABLE measurements ADD COLUMN phase TEXT "
                "NOT NULL DEFAULT 'L1';"
                "ALTER TABLE measurements ADD COLUMN frequency REAL "
                "NOT NULL DEFAULT 50;"
                "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
                "INSERT INTO meta VALUES ('telegram_setting_meter_type',"
                " 'pro3em');"
            )

    def start_real_writer(self) -> subprocess.Popen:
        """Run one bounded independent Python WAL writer with a POSIX lease."""

        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(self.root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.close_child, child)
        for _ in range(100):
            if (self.root / "writer-ready").exists():
                self.assertIsNone(child.poll())
                return child
            if child.poll() is not None:
                self.fail("Synthetic WAL writer exited before ready")
            time.sleep(0.05)
        self.fail("Synthetic WAL writer did not become ready in 5s")

    @staticmethod
    def close_child(child: subprocess.Popen) -> None:
        """Always reap the isolated child and close pipes."""

        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        if child.stdin is not None:
            child.stdin.close()
        if child.stderr is not None:
            child.stderr.close()

    def test_actual_competing_writer_blocks_even_with_valid_rescue(self):
        """Cross-process file lease prevents rollback before SQLite swap."""

        child = self.start_real_writer()
        expected = QA.fixture_digest(self.active)
        QA.sqlite_backup(self.active, self.rescue)
        self.assertEqual(QA.fixture_digest(self.rescue), expected)
        with self.assertRaisesRegex(QA.Blocked, "WRITER_NOT_FENCED"):
            QA.recover_fenced(self.root, expected[0], "0.5.25-hw")
        self.assertEqual(QA.fixture_digest(self.active), expected)
        self.assertFalse((self.root / "journal.json").exists())
        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.flush()
        self.assertEqual(child.wait(timeout=5), 0)
        QA.recover_fenced(self.root, expected[0], "0.5.25-hw")
        self.assertEqual(QA.fixture_digest(self.active), expected)

    def test_killed_writer_keeps_crash_wal_sidecar_fence(self):
        """SIGKILL frees the lease but cannot authorize WAL replacement."""

        child = self.start_real_writer()
        expected = QA.fixture_digest(self.active)
        QA.sqlite_backup(self.active, self.rescue)
        self.assertEqual(QA.fixture_digest(self.rescue), expected)
        child.send_signal(signal.SIGKILL)
        self.assertEqual(child.wait(timeout=5), -signal.SIGKILL)
        self.assertTrue(Path(str(self.active) + "-wal").exists())
        with self.assertRaisesRegex(
            QA.Blocked, "ACTIVE_SQLITE_WAL_SIDECAR_UNFENCED"
        ):
            QA.recover_fenced(self.root, expected[0], "0.5.25-hw")
        self.assertEqual(QA.fixture_digest(self.active), expected)
        self.assertFalse((self.root / "journal.json").exists())


if __name__ == "__main__":
    unittest.main()
