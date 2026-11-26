"""Real process-kill QA for newest synthetic WAL, PHOTO and send intent."""

from __future__ import annotations

import importlib.util
import json
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/fenced-newest-rescue.py"
SPEC = importlib.util.spec_from_file_location("svt_real_wal_rescue", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
RESCUE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESCUE)

# Child is separate to enforce a real SIGKILL rather than catching Exception.
CHILD = """
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("rescue_child", sys.argv[1])
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
source = Path(sys.argv[2])
backup = Path(sys.argv[3])
receipt = Path(sys.argv[4])
phase = sys.argv[5]
digest = helper.backup_consistently(source, backup)
with backup.open("rb") as stream:
    os.fsync(stream.fileno())
if phase == "after_backup":
    os.kill(os.getpid(), signal.SIGKILL)
with receipt.open("w", encoding="ascii") as output:
    json.dump({"logical_sha256": digest, "qa_only": True}, output)
    output.flush()
    os.fsync(output.fileno())
os.kill(os.getpid(), signal.SIGKILL)
"""


def sqlite_digest(path: Path) -> str:
    """Read a committed logical snapshot without modifying database files."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return RESCUE.logical_digest(db)


def telegram_state(path: Path) -> dict:
    """Read synthetic Telegram metadata without issuing a network request."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        meta = dict(db.execute("SELECT key,value FROM meta").fetchall())
        queue = db.execute(
            "SELECT chat_id,message_id,needs_unpin,retry_at "
            "FROM telegram_status_cleanup"
        ).fetchall()
        phases = dict(
            db.execute(
                "SELECT phase,count(*) FROM measurements GROUP BY phase"
            ).fetchall()
        )
    return {"meta": meta, "cleanup": queue, "phases": phases}


class ForwardWalSigkillTests(unittest.TestCase):
    """Exercise only private synthetic SQLite files and killed child PIDs."""

    def setUp(self) -> None:
        """Keep WAL open while a child snapshots committed pages."""
        fixture = tempfile.TemporaryDirectory(prefix="svt-forward-wal-qa-")
        self.addCleanup(fixture.cleanup)
        self.folder = Path(fixture.name)
        self.active = self.folder / "active.sqlite3"
        self.backup = self.folder / "rescue.sqlite3"
        self.receipt = self.folder / "receipt.json"
        self.writer = sqlite3.connect(self.active)
        self.addCleanup(self.writer.close)
        self.writer.execute("PRAGMA journal_mode=WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint=0")
        self.seed_state()
        self.expected_digest = RESCUE.logical_digest(self.writer)
        self.expected_state = telegram_state(self.active)
        self.assertTrue(Path(str(self.active) + "-wal").exists())

    def seed_state(self) -> None:
        """Populate synthetic three-phase and Telegram WAL state."""
        self.writer.executescript(
            "CREATE TABLE measurements("
            "id INTEGER PRIMARY KEY,phase TEXT,ts REAL,voltage REAL);"
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
            "CREATE TABLE telegram_status_cleanup("
            "chat_id TEXT,message_id INTEGER,needs_unpin INTEGER,"
            "retry_at REAL);"
        )
        self.writer.executemany(
            "INSERT INTO measurements(phase,ts,voltage) VALUES(?,?,?)",
            [
                ("L1", 123.0, 224.0),
                ("L2", 123.0, 225.0),
                ("L3", 123.0, 226.0),
            ],
        )
        self.writer.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            [
                ("telegram_setting_meter_type", "pro3em"),
                ("telegram_status_media_identity", "photo:QA:1001"),
                ("telegram_legacy_text_confirm_pending", "send:QA-TOKEN"),
                ("telegram_graphic_publish_pending", "QA-UNKNOWN"),
            ],
        )
        self.writer.execute(
            "INSERT INTO telegram_status_cleanup VALUES(?,?,?,?)",
            ("QA", 1000, 1, 200.0),
        )
        self.writer.commit()

    def kill_after(self, phase: str) -> subprocess.CompletedProcess:
        """Terminate only a disposable Python child after durable file I/O."""
        return subprocess.run(
            [
                sys.executable,
                "-c",
                CHILD,
                str(SCRIPT),
                str(self.active),
                str(self.backup),
                str(self.receipt),
                phase,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_real_kill_after_durable_wal_backup(self) -> None:
        """Newest committed L1/L2/L3 and PHOTO survive a real SIGKILL."""
        result = self.kill_after("after_backup")
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(sqlite_digest(self.backup), self.expected_digest)
        self.assertEqual(telegram_state(self.backup), self.expected_state)

    def test_real_kill_after_durable_metadata(self) -> None:
        """The retry process sees the same full rescue and immutable digest."""
        result = self.kill_after("after_receipt")
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        proof = json.loads(self.receipt.read_text(encoding="ascii"))
        self.assertEqual(proof["logical_sha256"], self.expected_digest)
        self.assertTrue(proof["qa_only"])
        self.assertEqual(sqlite_digest(self.backup), self.expected_digest)

    def test_rescue_uses_committed_wal_not_database_file_only(self) -> None:
        """A backup API includes uncheckpointed writes in its snapshot."""
        self.assertGreater(Path(str(self.active) + "-wal").stat().st_size, 0)
        result = self.kill_after("after_backup")
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        with closing(sqlite3.connect(self.backup)) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM measurements").fetchone()[0],
                3,
            )

    def test_new_live_samples_make_old_rescue_stale(self) -> None:
        """An earlier rescue must never be restored atop newer measurements."""
        self.assertEqual(self.kill_after("after_backup").returncode, -9)
        self.writer.executemany(
            "INSERT INTO measurements(phase,ts,voltage) VALUES(?,?,?)",
            [("L1", 124, 221), ("L2", 124, 222), ("L3", 124, 223)],
        )
        self.writer.commit()
        self.assertNotEqual(
            sqlite_digest(self.active), sqlite_digest(self.backup)
        )
        self.assertEqual(
            telegram_state(self.active)["phases"],
            {"L1": 2, "L2": 2, "L3": 2},
        )

    def test_pending_token_change_detected_by_logical_digest(self) -> None:
        """Changing a Telegram send intent must change rescue data."""
        self.assertEqual(self.kill_after("after_backup").returncode, -9)
        with closing(sqlite3.connect(self.backup)) as db, db:
            db.execute(
                "UPDATE meta SET value='send:OTHER' "
                "WHERE key='telegram_legacy_text_confirm_pending'"
            )
        self.assertNotEqual(sqlite_digest(self.backup), self.expected_digest)

    def test_missing_photo_blocks_newest_rescue(self) -> None:
        """An absent PHOTO marker refuses a forward repair snapshot."""
        self.writer.execute(
            "UPDATE meta SET value='' "
            "WHERE key='telegram_status_media_identity'"
        )
        self.writer.commit()
        with self.assertRaisesRegex(
            RESCUE.RescueBlocked, "SVT_RESCUE_IDENTITY_MISMATCH"
        ):
            RESCUE.backup_consistently(self.active, self.backup)

    def test_cleanup_queue_survives_uncheckpointed_wal(self) -> None:
        """Unpin/delete retries must remain pending after crash."""
        self.assertEqual(self.kill_after("after_receipt").returncode, -9)
        self.assertEqual(
            telegram_state(self.backup)["cleanup"], [("QA", 1000, 1, 200.0)]
        )
        self.assertEqual(
            telegram_state(self.backup)["meta"][
                "telegram_graphic_publish_pending"
            ],
            "QA-UNKNOWN",
        )


if __name__ == "__main__":
    unittest.main()
