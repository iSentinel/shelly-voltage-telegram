"""Test the disposable two-lock single-writer and pending protocol."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/boot-fenced-writer-disposable-qa.py"
)


class DisposableFencedWriterTests(unittest.TestCase):
    """Test synthetic phase history and no live paths or services."""

    def setUp(self) -> None:
        """Prepare a private SQLite file with a synthetic PHOTO marker."""

        temp = tempfile.TemporaryDirectory(prefix="svt-qa-fenced-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "qa-identity").write_text(
            "DISPOSABLE_PRO3EM_SQLITE_TEST_ONLY"
        )
        with closing(sqlite3.connect(self.root / "history.sqlite3")) as db:
            db.executescript(
                "CREATE TABLE measurements (id INTEGER PRIMARY KEY,"
                " ts REAL, phase TEXT, voltage REAL, frequency REAL);"
                "CREATE TABLE telegram_status (media_kind TEXT,"
                " message_id INT);"
                "INSERT INTO telegram_status VALUES ('photo',1000);"
                "PRAGMA journal_mode=WAL;"
            )

    def run_qa(
        self, mode: str, cycles: int = 1
    ) -> subprocess.CompletedProcess:
        """Run a bounded synthetic writer or updater subprocess."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--mode",
                mode,
                "--cycles",
                str(cycles),
                "--interval",
                "0.08",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )

    def start_writer(self) -> subprocess.Popen:
        """Start one background synthetic owner with a lifetime lock."""

        process = subprocess.Popen(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--mode",
                "writer",
                "--cycles",
                "100",
                "--interval",
                "0.1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.close_writer, process)
        for _ in range(100):
            if self.row_count() >= 3:
                self.assertIsNone(process.poll())
                return process
            time.sleep(0.04)
        self.fail("Synthetic writer never acquired its lease")

    @staticmethod
    def close_writer(process: subprocess.Popen) -> None:
        """Reap a bounded isolated process even on test failure."""

        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def row_count(self) -> int:
        """Read only the fixture database, never production."""

        with closing(sqlite3.connect(self.root / "history.sqlite3")) as db:
            return db.execute("SELECT count(*) FROM measurements").fetchone()[
                0
            ]

    def assert_history_integrity(self) -> None:
        """Verify no missing triplet and no PHOTO replacement."""

        with closing(sqlite3.connect(self.root / "history.sqlite3")) as db:
            self.assertEqual(
                db.execute("PRAGMA quick_check").fetchone()[0], "ok"
            )
            grouped = db.execute(
                "SELECT phase,count(*) FROM measurements GROUP BY phase "
                "ORDER BY phase"
            ).fetchall()
            photo = db.execute(
                "SELECT media_kind,message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(len({n for _, n in grouped}), 1)
        self.assertEqual([p for p, _ in grouped], ["L1", "L2", "L3"])
        self.assertEqual(photo, ("photo", 1000))

    def test_pending_before_start_refuses_all_writes(self):
        """A preexisting marker blocks all phases and PHOTO changes."""

        self.assertEqual(self.run_qa("publish").returncode, 0)
        result = self.run_qa("writer", cycles=3)
        self.assertEqual(result.returncode, 2)
        self.assertIn("PENDING_FENCED", result.stdout)
        self.assertEqual(self.row_count(), 0)

    def test_complete_triplet_writes_are_atomic(self):
        """Three successful cycles produce nine correct phase rows."""

        result = self.run_qa("writer", cycles=3)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.row_count(), 9)
        self.assert_history_integrity()

    def test_second_independent_writer_is_rejected(self):
        """The lifetime lease rules out two simultaneously writing owners."""

        writer = self.start_writer()
        another = self.run_qa("writer")
        self.assertEqual(another.returncode, 2)
        self.assertIn("WRITER_ALREADY_ACTIVE", another.stdout)
        self.assertEqual(self.run_qa("publish").returncode, 0)
        self.assertEqual(writer.wait(timeout=5), 2)
        self.assert_history_integrity()

    def test_pending_publisher_stops_running_writer(self):
        """Publication and every phase triplet share the short lock."""

        writer = self.start_writer()
        self.assertEqual(self.run_qa("publish").returncode, 0)
        at_publish = self.row_count()
        self.assertEqual(writer.wait(timeout=5), 2)
        time.sleep(0.2)
        self.assertEqual(self.row_count(), at_publish)
        self.assert_history_integrity()

    def test_symlinked_owner_lock_fails_closed(self):
        """A redirected lifetime lease cannot start any writer."""

        (self.root / "owner.lock").symlink_to(self.root / "history.sqlite3")
        result = self.run_qa("writer")
        self.assertEqual(result.returncode, 2)
        self.assertIn("QA_LINKED_PATH", result.stdout)
        self.assertEqual(self.row_count(), 0)


if __name__ == "__main__":
    unittest.main()
