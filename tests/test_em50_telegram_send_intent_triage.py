"""Test fail-closed read-only triage of Telegram send uncertainty."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/em50-telegram-send-intent-triage.py"
SPEC = importlib.util.spec_from_file_location("em50_triage", SCRIPT)
assert SPEC and SPEC.loader
TRIAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIAGE)


class TelegramIntentTriageTests(unittest.TestCase):
    """Use disposable SQLite databases without Telegram or network access."""

    def setUp(self) -> None:
        """Create one complete synthetic text status and private DB file."""

        tmp = tempfile.TemporaryDirectory(prefix="issue402-triage-")
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "fixture.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            db.executemany(
                "INSERT INTO meta(key,value) VALUES (?,?)",
                (
                    ("telegram_status_message_id", "1139"),
                    ("telegram_status_media_identity", "text:-100123:1139"),
                ),
            )

    def put(self, name: str, value: str) -> None:
        """Write one fixture-only metadata marker."""

        with sqlite3.connect(self.db) as db:
            db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (name, value),
            )

    def command(self, *extra: str) -> subprocess.CompletedProcess:
        """Run only this source file against the disposable test database."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                str(self.db),
                "--jsonl",
                *extra,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

    def test_clear_text_identity_passes_without_delivery_claim(self) -> None:
        """Valid SQLite state never proves recipient-visible delivery."""

        result = TRIAGE.inspect(self.db)
        self.assertEqual(result["status"], "CLEAR")
        self.assertEqual(result["saved_status_id"], "1139")
        self.assertEqual(result["pending_count"], 0)
        self.assertFalse(result["recipient_visible_delivery_verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["photo_authorized"])

    def test_uncertain_send_requires_manual_reconciliation(self) -> None:
        """An accepted-unknown remote send must never be auto-retried."""

        token = "abcdef0123456789abcdef0123456789"
        self.put(TRIAGE.TEXT_PENDING, "send:" + token)
        response = self.command()
        result = json.loads(response.stdout)
        self.assertEqual(response.returncode, 1)
        self.assertEqual(result["reason"], "unknown_remote_send_outcome")
        self.assertTrue(result["owner_reconciliation_required"])
        self.assertEqual(result["pending_kinds"], ["unknown_text_send"])
        self.assertNotIn(token, response.stdout)
        self.assertNotIn("-100123", response.stdout)

    def test_uncertainty_survives_new_sqlite_connection(self) -> None:
        """A simulated process restart must preserve the same HOLD."""

        self.put(TRIAGE.TEXT_PENDING, "send:" + "a" * 32)
        first = TRIAGE.inspect(self.db)
        second = TRIAGE.inspect(self.db)
        self.assertEqual(first, second)
        self.assertEqual(second["status"], "HOLD")

    def test_empty_or_malformed_send_token_is_not_clear(self) -> None:
        """Malformed send intents cannot bypass operator reconciliation."""

        for value in ("send:", "send:zzzz", "send:" + "A" * 32):
            with self.subTest(value=value):
                self.put(TRIAGE.TEXT_PENDING, value)
                result = TRIAGE.inspect(self.db)
                self.assertEqual(result["reason"], "invalid_send_intent")

    def test_legacy_text_pending_is_not_mistaken_for_new_send(self) -> None:
        """Do not call older legacy confirmations an uncertain network send."""

        self.put(TRIAGE.TEXT_PENDING, "pending")
        result = TRIAGE.inspect(self.db)
        self.assertEqual(result["pending_kinds"], ["legacy_text_confirmation"])
        self.assertEqual(result["status"], "HOLD")
        self.assertFalse(result["owner_reconciliation_required"])

    def test_graphic_pending_blocks_clear(self) -> None:
        """PHOTO publication state cannot be considered a clean channel."""

        self.put(TRIAGE.GRAPHIC_PENDING, "owner-pending")
        result = TRIAGE.inspect(self.db)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["reason"], "publication_pending")
        self.assertFalse(result["photo_authorized"])

    def test_recovery_rotation_pending_blocks_clear(self) -> None:
        """An unresolved rotation must remain visible as a hold."""

        self.put(TRIAGE.RECOVERY_PENDING, "1234567890")
        self.assertEqual(TRIAGE.inspect(self.db)["status"], "HOLD")

    def test_competing_text_and_photo_intents_hold(self) -> None:
        """Report both kinds without echoing confidential marker values."""

        self.put(TRIAGE.TEXT_PENDING, "send:" + "b" * 32)
        self.put(TRIAGE.GRAPHIC_PENDING, "PRIVATE-SECRET")
        result = TRIAGE.inspect(self.db)
        self.assertEqual(result["pending_count"], 2)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["reason"], "unknown_remote_send_outcome")
        self.assertNotIn("PRIVATE-SECRET", json.dumps(result))

    def test_media_marker_mismatch_requires_hold(self) -> None:
        """A stale saved message ID must not authorize a fresh post."""

        self.put(TRIAGE.MEDIA_ID, "text:-100123:9999")
        result = TRIAGE.inspect(self.db)
        self.assertEqual(result["reason"], "saved_identity_invalid")
        self.assertFalse(result["saved_identity_valid"])

    def test_missing_message_id_requires_hold(self) -> None:
        """The triage script cannot infer Telegram status from old metadata."""

        with sqlite3.connect(self.db) as db:
            db.execute(
                "DELETE FROM meta WHERE key=?",
                (TRIAGE.STATUS_ID,),
            )
        self.assertEqual(TRIAGE.inspect(self.db)["status"], "HOLD")

    def test_missing_database_does_not_create_one(self) -> None:
        """No database file may be created by a read-only command."""

        self.db.unlink()
        response = self.command()
        self.assertEqual(response.returncode, 2)
        self.assertFalse(self.db.exists())
        self.assertEqual(json.loads(response.stdout)["status"], "ERROR")

    def test_symlinked_database_rejected(self) -> None:
        """Follow neither a local symlink nor an external DB alias."""

        link = self.db.with_name("linked.sqlite3")
        link.symlink_to(self.db)
        with self.assertRaises(ValueError):
            TRIAGE.read_snapshot(link)

    def test_malformed_database_returns_read_error(self) -> None:
        """Unparseable SQLite must not cause a false CLEAR."""

        self.db.write_bytes(b"not an SQLite database")
        response = self.command()
        self.assertEqual(response.returncode, 2)
        self.assertEqual(
            json.loads(response.stdout)["reason"], "database_unreadable"
        )

    def test_readonly_database_bytes_and_meta_are_unchanged(self) -> None:
        """Repeated read-only audits cannot clear a pending send intent."""

        token = "send:" + "c" * 32
        self.put(TRIAGE.TEXT_PENDING, token)
        before = self.db.read_bytes()
        for _ in range(3):
            self.assertEqual(TRIAGE.inspect(self.db)["status"], "HOLD")
        self.assertEqual(self.db.read_bytes(), before)
        with sqlite3.connect(self.db) as conn:
            found = conn.execute(
                "SELECT value FROM meta WHERE key=?",
                (TRIAGE.TEXT_PENDING,),
            ).fetchone()[0]
        self.assertEqual(found, token)

    def test_cli_help_and_clear_exit_code(self) -> None:
        """The CLI must provide help and stable status codes."""

        clear = self.command()
        help_result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(clear.returncode, 0)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--db", help_result.stdout)


if __name__ == "__main__":
    unittest.main()
