"""Preserve an unrelated pinned post across an owner-approved cutover."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-unrelated-pin-continuity.py"
)


class UnrelatedPinContinuityTests(unittest.TestCase):
    """Inspect synthetic getChat replies without Telegram or production."""

    def setUp(self):
        """Create independent SQLite, pinned message and private baseline."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / "voltage.sqlite3"
        self.response = self.folder / "getchat.json"
        self.baseline = self.folder / "pin-baseline.json"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("create table meta(key text primary key,value text)")
            db.execute(
                "insert into meta values ('telegram_status_message_id','101')"
            )
        self.set_pin(909)

    def set_pin(self, message_id=909, channel=-100123, pinned_chat=None):
        """Make one read-only response with a dedicated unrelated pin."""

        response = {
            "ok": True,
            "result": {
                "id": channel,
                "type": "channel",
                "pinned_message": {
                    "message_id": message_id,
                    "chat": {"id": pinned_chat or channel},
                    "text": "Other pinned channel announcement",
                },
            },
        }
        self.response.write_text(json.dumps(response), encoding="utf8")

    def cli(self, *flags):
        """Call the CLI with only fixture files and a synthetic channel."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--database",
                str(self.db),
                "--response-file",
                str(self.response),
                "--baseline",
                str(self.baseline),
                "--chat-env",
                "TEST_PIN_STATUS_CHANNEL",
                "--jsonl",
                *flags,
            ],
            env={**os.environ, "TEST_PIN_STATUS_CHANNEL": "-100123"},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def capture(self):
        """Create a protected pin baseline, without storing raw IDs."""

        result = self.cli("--capture")
        self.assertEqual(result.returncode, 0, result.stdout)
        return json.loads(result.stdout)

    def test_capture_verify_and_redact_identifiers(self):
        """A stable unrelated pin is observable but not rollout approval."""

        captured = self.capture()
        self.assertEqual(captured["status"], "FIXTURE_PIN_BASELINE_ONLY")
        self.assertFalse(captured["deployment_allowed"])
        self.assertFalse(captured["exact_status_media_verified"])
        self.assertEqual(stat.S_IMODE(self.baseline.stat().st_mode), 0o600)
        private = self.baseline.read_text()
        self.assertNotIn("-100123", private)
        self.assertNotIn('"message_id"', private)
        self.assertNotIn('"chat_id"', private)
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["status"], "FIXTURE_PIN_UNCHANGED_ONLY")
        self.assertFalse(data["telegram_modified"])
        self.assertNotIn("-100123", result.stdout)

    def test_fixture_baseline_cannot_be_presented_as_live(self):
        """A synthetic fixture is not a getChat proof for rollout."""

        self.capture()
        source = __import__("importlib.util", fromlist=["util"])
        spec = source.spec_from_file_location("pin_continuity", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = source.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(
            module.PinAuditError, "BASELINE_SOURCE_MISMATCH"
        ):
            module.verify(self.baseline, "-100123", 909, live=True)

    def test_changed_pinned_post_blocks_without_overwrite(self):
        """A different pinned ID is a non-retryable operator issue."""

        self.capture()
        before = self.baseline.read_bytes()
        self.set_pin(910)
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["reason"], "UNRELATED_PIN_CHANGED"
        )
        self.assertEqual(before, self.baseline.read_bytes())

    def test_no_capture_flag_never_creates_evidence(self):
        """The default CLI must remain read-only."""

        response = self.cli()
        self.assertEqual(response.returncode, 2)
        self.assertFalse(self.baseline.exists())
        self.assertNotIn("-100123", response.stdout)

    def test_pinned_status_itself_cannot_be_unrelated(self):
        """The status post cannot masquerade as its own other pin."""

        self.set_pin(101)
        response = self.cli("--capture")
        self.assertEqual(response.returncode, 2)
        self.assertEqual(
            json.loads(response.stdout)["reason"], "PIN_IS_STATUS_MESSAGE"
        )
        self.assertFalse(self.baseline.exists())

    def test_wrong_channel_or_message_channel_is_blocked(self):
        """Reject an unrelated or forged getChat origin."""

        for kwargs in (
            {"channel": -100999},
            {"pinned_chat": -100999},
        ):
            with self.subTest(kwargs=kwargs):
                self.set_pin(**kwargs)
                response = self.cli("--capture")
                self.assertEqual(response.returncode, 2)
                self.assertFalse(self.baseline.exists())

    def test_legacy_confirmation_pending_refuses_readiness(self):
        """An uncertain text edit cannot attest channel continuity."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "insert into meta values "
                "('telegram_legacy_text_confirm_pending','pending')"
            )
        result = self.cli("--capture")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["reason"],
            "UNRESOLVED_TEXT_CONFIRMATION",
        )

    def test_malformed_or_expired_baseline_is_blocked(self):
        """Reject stale, world-readable and tampered pin evidence."""

        self.capture()
        state = json.loads(self.baseline.read_text())
        state["captured_unix"] = int(time.time()) - 100000
        self.baseline.write_text(json.dumps(state))
        response = self.cli()
        self.assertEqual(response.returncode, 2)
        self.assertEqual(
            json.loads(response.stdout)["reason"], "BASELINE_EXPIRED"
        )
        self.baseline.chmod(0o644)
        self.assertEqual(self.cli().returncode, 2)

    def test_baseline_cannot_be_replaced_or_linked(self):
        """Refuse to overwrite captured proof or follow linked files."""

        self.capture()
        self.assertEqual(self.cli("--capture").returncode, 2)
        original = self.baseline.with_suffix(".original")
        self.baseline.rename(original)
        self.baseline.symlink_to(original)
        self.assertEqual(self.cli().returncode, 2)

    def test_help_exposes_capture_and_verify_controls(self):
        """Make explicit capture mode discoverable without secrets."""

        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--capture", proc.stdout)


if __name__ == "__main__":
    unittest.main()
