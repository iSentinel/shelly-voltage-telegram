"""Read-only Telegram status identity audit regression tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "telegram-status-identity-audit.py"
)


class TelegramIdentityAuditTests(unittest.TestCase):
    """Use disposable SQLite only; never contact Telegram or Shelly."""

    def setUp(self) -> None:
        """Create a temporary database with a confirmed text post."""

        self.temporary = tempfile.TemporaryDirectory(prefix="qa-status-audit-")
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / "status.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
            db.executemany(
                "INSERT INTO meta(key,value) VALUES(?,?)",
                [
                    ("telegram_status_message_id", "101"),
                    ("telegram_status_media_identity", "text:-100999:101"),
                ],
            )

    def set_meta(self, key: str, value: str) -> None:
        """Change one synthetic metadata value."""

        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def run_audit(self, *flags: str, database: Path | None = None):
        """Execute the standalone checker with bounded runtime."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                str(database or self.db),
                *flags,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_matching_text_status_passes(self) -> None:
        """A properly bound text post is valid."""

        result = self.run_audit("--jsonl")
        self.assertEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["status_id"], "101")

    def test_matching_photo_status_passes(self) -> None:
        """Photo markers are checked but never modified."""

        self.set_meta("telegram_status_media_identity", "photo:-100999:101")
        result = self.run_audit("--jsonl")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["media_kind"], "photo")

    def test_stale_marker_fails_and_hides_chat_id(self) -> None:
        """An old post ID is rejected without printing a private chat."""

        self.set_meta("telegram_status_message_id", "102")
        result = self.run_audit("--jsonl")
        self.assertEqual(result.returncode, 1)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["reason"], "media_identity_mismatch")
        self.assertNotIn("-100999", result.stdout)

    def test_pending_publication_fails_closed(self) -> None:
        """An ambiguous publication is not reported as healthy."""

        self.set_meta("telegram_graphic_publish_pending", "pending")
        result = self.run_audit("--jsonl")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            json.loads(result.stdout)["reason"], "publication_pending"
        )

    def test_zero_message_id_fails(self) -> None:
        """Reject an invalid status reference."""

        self.set_meta("telegram_status_message_id", "0")
        result = self.run_audit("--jsonl")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["reason"], "missing_status")

    def test_missing_database_fails_without_creating_it(self) -> None:
        """Opening a nonexistent DB must never create a file."""

        missing = self.db.with_name("missing.sqlite3")
        result = self.run_audit("--jsonl", database=missing)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(missing.exists())
        self.assertEqual(json.loads(result.stdout)["reason"], "db_read_error")

    def test_output_never_contains_channel_identity(self) -> None:
        """Even a custom channel identifier must stay private."""

        self.set_meta(
            "telegram_status_media_identity", "text:PRIVATE_CHANNEL_ABC:101"
        )
        result = self.run_audit()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("PRIVATE_CHANNEL_ABC", result.stdout)

    def test_database_file_remains_unchanged(self) -> None:
        """The checker is strictly read-only."""

        before = hashlib.sha256(self.db.read_bytes()).digest()
        result = self.run_audit()
        after = hashlib.sha256(self.db.read_bytes()).digest()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(before, after)

    def test_help_is_available(self) -> None:
        """Expose a normal CLI help entry point."""

        result = self.run_audit("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--jsonl", result.stdout)


if __name__ == "__main__":
    unittest.main()
