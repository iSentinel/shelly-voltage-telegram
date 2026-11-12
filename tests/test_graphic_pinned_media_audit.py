"""Verify an untrusted getChat receipt never authorizes a channel edit."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-pinned-media-audit.py"
)


class FakeReply(io.BytesIO):
    """Return a fake Bot API JSON response from the mocked opener."""

    def __enter__(self):
        """Support urllib.response context management."""

        return self

    def __exit__(self, *unused):
        """Close the local fake response."""

        self.close()


class GraphicPinnedMediaAuditTests(unittest.TestCase):
    """Only exact existing Telegram media can be marked for owner review."""

    def setUp(self):
        """Prepare an isolated SQLite database and local getChat fixture."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.database = folder / "voltage.sqlite3"
        self.response_file = folder / "response.json"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_status_message_id", "101"),
            )
        self.write_response()

    def write_response(self, message_id=101, kind="text", **changes):
        """Return a minimal synthetic Telegram ChatFullInfo payload."""

        pinned = {
            "message_id": message_id,
            "chat": {"id": -100123},
        }
        if kind == "text":
            pinned["text"] = "Existing unchanged channel status"
        elif kind == "photo":
            pinned["photo"] = [{"file_id": "synthetic-only"}]
        response = {
            "ok": True,
            "result": {
                "id": -100123,
                "type": "channel",
                "pinned_message": pinned,
            },
        }
        response["result"].update(changes)
        self.response_file.write_text(json.dumps(response))

    def run_audit(self, *extra):
        """Invoke the CLI with no token and no possible network request."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(self.database),
                "--response-file",
                str(self.response_file),
                "--chat-env",
                "TEST_GRAPHIC_CHAT_ID",
                "--jsonl",
                *extra,
            ],
            env={**os.environ, "TEST_GRAPHIC_CHAT_ID": "-100123"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    def assert_blocked(self, reason):
        """Enforce no SQL writes, new messages or private identifiers."""

        result = self.run_audit()
        self.assertEqual(result.returncode, 2, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["status"], "BLOCKED")
        self.assertEqual(record["reason"], reason)
        self.assertFalse(record["deployment_allowed"])
        self.assertFalse(record["database_modified"])
        self.assertFalse(record["telegram_messages_modified"])
        self.assertNotIn("-100123", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_exact_pinned_text_fixture_is_only_provisional(self):
        """A local receipt is never proof of live Telegram state."""

        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["status"], "FIXTURE_MATCH_ONLY")
        self.assertEqual(record["media_kind"], "text")
        self.assertFalse(record["live_attestation"])
        self.assertFalse(record["deployment_allowed"])

    def test_exact_pinned_photo_fixture_is_only_provisional(self):
        """Photo receipts do not trigger conversion or replacement."""

        self.write_response(kind="photo")
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["media_kind"], "photo")

    def test_latest_pinned_other_message_is_blocked(self):
        """Pinned posts cannot attest to unrelated stored status IDs."""

        self.write_response(message_id=102)
        self.assert_blocked("PINNED_MESSAGE_MISMATCH")

    def test_missing_pinned_message_blocks_migration(self):
        """A channel without the exact pinned post needs another proof."""

        self.write_response(pinned_message=None)
        self.assert_blocked("PINNED_MESSAGE_UNAVAILABLE")

    def test_different_chat_id_is_blocked(self):
        """A signed-looking receipt from another channel has no authority."""

        self.write_response(id=-100999)
        self.assert_blocked("CHANNEL_MISMATCH")

    def test_unknown_media_type_is_blocked(self):
        """Only an unambiguous photo or plain text post is supported."""

        self.write_response(kind="unknown")
        self.assert_blocked("MEDIA_TYPE_AMBIGUOUS")

    def test_both_text_and_photo_are_ambiguous(self):
        """A mixed or malformed receipt cannot select the edit method."""

        self.write_response(kind="photo")
        value = json.loads(self.response_file.read_text())
        value["result"]["pinned_message"]["text"] = "unexpected"
        self.response_file.write_text(json.dumps(value))
        self.assert_blocked("MEDIA_TYPE_AMBIGUOUS")

    def test_empty_pending_marker_still_blocks(self):
        """The presence of unresolved history wins over any valid fixture."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_graphic_publish_pending", ""),
            )
        self.assert_blocked("UNRESOLVED_PUBLICATION")

    def test_conflicting_media_marker_blocks(self):
        """Do not overwrite a previously persisted photo identity."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_status_media_identity", "photo:-100123:101"),
            )
        self.assert_blocked("CONFLICTING_MEDIA_MARKER")

    def test_broken_json_is_fully_redacted(self):
        """Truncated API receipts never print full paths or credentials."""

        self.response_file.write_text("{broken")
        self.assert_blocked("AUDIT_UNAVAILABLE")

    def test_linked_response_file_is_rejected(self):
        """The fixture must be a local, unlinked, bounded file."""

        original = self.response_file.with_name("real.json")
        self.response_file.rename(original)
        self.response_file.symlink_to(original)
        self.assert_blocked("INPUT_UNSAFE")

    def test_missing_db_is_not_created(self):
        """Read-only audits cannot silently initialize SQLite."""

        self.database.unlink()
        self.assert_blocked("DATABASE_INVALID")
        self.assertFalse(self.database.exists())

    def test_live_getchat_calls_only_read_only_endpoint(self):
        """Direct token mode fetches getChat, never a Telegram edit."""

        token_file = self.response_file.with_name("token-secret")
        token_file.write_text("123456:abcdefghijklmnopqrstuvwxyzABCD")
        source = importlib.util.spec_from_file_location("pinned_audit", SCRIPT)
        self.assertIsNotNone(source)
        module = importlib.util.module_from_spec(source)
        source.loader.exec_module(module)
        response = self.response_file.read_bytes()

        def fake_open(request, timeout):
            self.assertEqual(request.get_method(), "POST")
            self.assertTrue(request.full_url.endswith("/getChat"))
            self.assertEqual(json.loads(request.data)["chat_id"], "-100123")
            self.assertEqual(timeout, 12)
            return FakeReply(response)

        args = SimpleNamespace(
            database=self.database,
            chat_env="TEST_GRAPHIC_CHAT_ID",
            token_file=token_file,
            response_file=None,
        )
        with (
            mock.patch.dict(os.environ, {"TEST_GRAPHIC_CHAT_ID": "-100123"}),
            mock.patch.object(module.urllib.request, "urlopen", fake_open),
        ):
            result = module.audit(args)
        self.assertEqual(result["status"], "MATCH_FOR_REVIEW")
        self.assertTrue(result["live_attestation"])
        self.assertFalse(result["deployment_allowed"])
        self.assertFalse(result["telegram_messages_modified"])

    def test_live_wrong_pin_is_still_blocked(self):
        """A read-only API call cannot turn a different pin into evidence."""

        self.write_response(message_id=102)
        token_file = self.response_file.with_name("bot-token")
        token_file.write_text("123456:abcdefghijklmnopqrstuvwxyzABCD")
        source = importlib.util.spec_from_file_location("pinned_audit", SCRIPT)
        module = importlib.util.module_from_spec(source)
        source.loader.exec_module(module)

        args = SimpleNamespace(
            database=self.database,
            chat_env="TEST_GRAPHIC_CHAT_ID",
            token_file=token_file,
            response_file=None,
        )
        data = self.response_file.read_bytes()
        with (
            mock.patch.dict(os.environ, {"TEST_GRAPHIC_CHAT_ID": "-100123"}),
            mock.patch.object(
                module.urllib.request,
                "urlopen",
                return_value=FakeReply(data),
            ),
            self.assertRaisesRegex(ValueError, "PINNED_MESSAGE_MISMATCH"),
        ):
            module.audit(args)

    def test_help_names_both_audit_modes(self):
        """CLI has discoverable -h/--help without external calls."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--token-file", result.stdout)
        self.assertIn("--response-file", result.stdout)


if __name__ == "__main__":
    unittest.main()
