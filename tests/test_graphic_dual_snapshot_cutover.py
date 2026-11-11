"""Two source+SQLite checkpoints are required for Telegram photo cutover."""

from __future__ import annotations

import gzip
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from test_app import APP

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate-sqlite-archive.py"
ROLLBACK_GUARD = ROOT / "scripts/graphic-rollback-media-guard.py"


class DualSnapshotCutoverTests(unittest.TestCase):
    """Prove safe SQLite checkpoints with no Bot API or real writer."""

    def setUp(self):
        """Create offline telemetry and one pre-existing status ID."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / "live.sqlite3"
        self.storage = APP.Storage(self.path, "Europe/Kyiv")
        self.storage.initialize()
        self.storage.add(1000.0, 217.5, 50.0, "L1")
        self.storage.add(1015.0, 218.1, 50.0, "L1")
        self.storage.set_meta("telegram_status_message_id", "101")

    def copy_archive(self, label: str) -> Path:
        """Capture all committed SQLite pages, including WAL content."""

        restored = self.folder / f"{label}.sqlite3"
        with (
            closing(sqlite3.connect(self.path)) as source,
            closing(sqlite3.connect(restored)) as output,
        ):
            source.backup(output)
        archived = self.folder / f"{label}.sqlite3.gz"
        archived.write_bytes(gzip.compress(restored.read_bytes(), mtime=0))
        return archived

    def compare(self, archived: Path) -> subprocess.CompletedProcess:
        """Use the same whole-database validator as the rollout gate."""

        return subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--archive",
                str(archived),
                "--compare-db",
                str(self.path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def rollback_media_decision(self) -> dict:
        """Query the actual rollback guard without any mutation."""

        proc = subprocess.run(
            [
                sys.executable,
                str(ROLLBACK_GUARD),
                "--database",
                str(self.path),
                "--jsonl",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
        return {"exit": proc.returncode, **json.loads(proc.stdout)}

    def test_first_snapshot_becomes_stale_after_text_identity(self):
        """The confirmation metadata write invalidates original A parity."""

        a = self.copy_archive("before-telegram-edit")
        self.assertEqual(self.compare(a).returncode, 0)
        self.storage.set_meta(
            "telegram_status_media_identity", "text:-100123:101"
        )
        self.assertEqual(self.compare(a).returncode, 2)
        self.assertEqual(
            self.rollback_media_decision()["reason"], "confirmed_text"
        )

    def test_second_snapshot_is_exact_after_verified_confirmation(self):
        """B contains the verified media marker and full prior history."""

        a = self.copy_archive("original-legacy-text")
        self.storage.set_meta(
            "telegram_status_media_identity", "text:-100123:101"
        )
        b = self.copy_archive("confirmed-legacy-text")
        self.assertEqual(self.compare(a).returncode, 2)
        self.assertEqual(self.compare(b).returncode, 0)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(
                db.execute("select count(*) from measurements").fetchone()[0],
                2,
            )
            self.assertEqual(
                db.execute(
                    "select value from meta "
                    "where key='telegram_status_message_id'"
                ).fetchone()[0],
                "101",
            )

    def test_photo_converted_status_invalidates_b_and_blocks_old_code(self):
        """A and B cannot restore a public Telegram photo as text."""

        self.storage.set_meta(
            "telegram_status_media_identity", "text:-100123:101"
        )
        b = self.copy_archive("text-confirmed")
        self.assertEqual(self.compare(b).returncode, 0)
        self.storage.set_meta(
            "telegram_status_media_identity", "photo:-100123:101"
        )
        self.assertEqual(self.compare(b).returncode, 2)
        decision = self.rollback_media_decision()
        self.assertEqual(decision["exit"], 6)
        self.assertEqual(decision["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
