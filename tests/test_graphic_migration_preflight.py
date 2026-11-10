"""Networkless tests for the read-only graphical status migration preflight."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-migration-preflight.py"
)


class GraphicMigrationPreflightTests(unittest.TestCase):
    """Protect the current public status from accidental migration."""

    def setUp(self):
        """Create a private disposable SQLite database."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "voltage.sqlite3"
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            db.commit()
        self.write_meta("telegram_status_message_id", "101")

    def write_meta(self, key: str, value: str) -> None:
        """Insert exactly one source-only fixture metadata value."""

        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value)
            )
            db.commit()

    def run_check(
        self, database: Path | None = None
    ) -> subprocess.CompletedProcess:
        """Execute the actual CLI with a fake channel and no network."""

        env = dict(os.environ)
        env["TEST_GRAPHIC_CHAT_ID"] = "-100123"
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(database or self.database),
                "--chat-env",
                "TEST_GRAPHIC_CHAT_ID",
            ],
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=10,
        )

    def test_unverified_live_style_metadata_is_blocked(self):
        """A valid old ID alone never authorizes a graphic rollout."""

        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("media identity is not verified", result.stdout)
        self.assertNotIn("-100123", result.stdout)

    def test_verified_text_allows_readiness_without_writes(self):
        """A bound, known text status is eligible for a later approval."""

        self.write_meta("telegram_status_media_identity", "text:-100123:101")
        original = self.database.read_bytes()
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("PASS", result.stdout)
        self.assertEqual(self.database.read_bytes(), original)

    def test_verified_photo_passes_with_same_status_id(self):
        """A verified existing photo can be updated with its exact ID."""

        self.write_meta("telegram_status_media_identity", "photo:-100123:101")
        self.assertEqual(self.run_check().returncode, 0)

    def test_wrong_channel_or_stale_id_is_rejected(self):
        """Cross-chat and historic message markers cannot be reused."""

        for marker in ("photo:-100456:101", "text:-100123:102"):
            with self.subTest(marker=marker):
                self.write_meta("telegram_status_media_identity", marker)
                self.assertEqual(self.run_check().returncode, 2)

    def test_uncertain_published_post_blocks_all_followups(self):
        """A timeout marker must require external reconciliation."""

        self.write_meta("telegram_status_media_identity", "text:-100123:101")
        self.write_meta("telegram_graphic_publish_pending", "-100123:101")
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires reconciliation", result.stdout)

    def test_missing_database_is_never_created(self):
        """A nonexistent database path fails without side effects."""

        missing = Path(self.temp.name) / "missing.sqlite3"
        self.assertEqual(self.run_check(missing).returncode, 2)
        self.assertFalse(missing.exists())

    def test_symlinked_database_is_rejected(self):
        """Preflight must not follow ambiguous database symlinks."""

        linked = Path(self.temp.name) / "linked.sqlite3"
        linked.symlink_to(self.database)
        self.assertEqual(self.run_check(linked).returncode, 2)

    def test_malformed_status_id_is_not_ready(self):
        """Zero or arbitrary strings are not accepted as Telegram IDs."""

        for status in ("0", "abc", "-5"):
            with self.subTest(status=status):
                self.write_meta("telegram_status_message_id", status)
                self.write_meta(
                    "telegram_status_media_identity",
                    f"text:-100123:{status}",
                )
                self.assertEqual(self.run_check().returncode, 2)

    def test_help_available_for_noninteractive_tool(self):
        """The CLI always exposes concise command-line usage."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--database", result.stdout)


if __name__ == "__main__":
    unittest.main()
