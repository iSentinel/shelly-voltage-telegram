"""Guarded Telegram text verification with no production API calls."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-confirm-legacy-text.py"
)


def module():
    """Import the one-shot cutover helper without executing its CLI."""

    spec = importlib.util.spec_from_file_location("confirm_legacy", SCRIPT)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


CONFIRM = module()


class GraphicLegacyConfirmationTests(unittest.TestCase):
    """Exercise the existing-message-only one-shot workflow offline."""

    def prepare_database(self) -> None:
        """Create one disposable status ID and historical voltage sample."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("create table meta (key text primary key,value text)")
            db.execute(
                "insert into meta values (?,?)",
                ("telegram_status_message_id", "101"),
            )
            db.execute(
                "create table measurements "
                "(id integer primary key,ts real,phase text,voltage real)"
            )
            db.execute(
                "insert into measurements values (1,?,?,?)",
                (time.time() - 5, "L1", 219.7),
            )

    def stub_release_gate(self) -> None:
        """Replace all external approvals with isolated test doubles."""

        self.gate = mock.Mock()
        self.gate.owner_approval_verified.return_value = True
        self.gate.snapshot_issues.return_value = []
        self.gate.version_issues.return_value = []
        self.gate.snapshot_archives.return_value = (
            self.dir / "source.tar.gz",
            self.dir / "backup.sqlite3.gz",
        )
        self.gate.run_archive_validator.return_value = True
        env = mock.patch.dict(
            os.environ, {"TELEGRAM_STATUS_CHAT_ID": "-100123"}
        )
        env.start()
        self.addCleanup(env.stop)
        guard = mock.patch.object(
            CONFIRM, "gate_module", return_value=self.gate
        )
        guard.start()
        self.addCleanup(guard.stop)

    def setUp(self):
        """Construct isolated SQLite, file paths and guarded API doubles."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.db = self.dir / "voltage.sqlite3"
        self.prepare_database()
        self.args = argparse.Namespace(
            database=self.db,
            snapshot=self.dir,
            installed_version_file=self.dir / "VERSION",
            owner_approval_file=self.dir / "approval.json",
            token_file=self.dir / "token",
            chat_env="TELEGRAM_STATUS_CHAT_ID",
            execute=False,
            jsonl=True,
        )
        self.stub_release_gate()

    def pending_marker(self):
        """Return the crash-safe one-shot marker without writing."""

        with closing(sqlite3.connect(self.db)) as db:
            return db.execute(
                "select value from meta "
                "where key='telegram_legacy_text_confirm_pending'"
            ).fetchone()

    def marker(self):
        """Read the media marker without mutating the test database."""

        with closing(sqlite3.connect(self.db)) as db:
            return db.execute(
                "select value from meta "
                "where key='telegram_status_media_identity'"
            ).fetchone()

    def test_help_and_default_do_not_contact_telegram(self):
        """Help requires no credentials and dry-run never reads bot token."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--execute", result.stdout)
        with (
            mock.patch.object(CONFIRM, "writer_stopped", return_value=True),
            mock.patch.object(CONFIRM, "safe_token") as secret,
            mock.patch.object(CONFIRM, "edit_text_once") as edit,
        ):
            result = CONFIRM.run(self.args)
        self.assertEqual(result["status"], "DRY_RUN_PASS")
        secret.assert_not_called()
        edit.assert_not_called()
        self.assertIsNone(self.marker())

    def test_writer_must_be_stopped_before_any_edit(self):
        """Active production cannot be touched by the one-shot helper."""

        with (
            mock.patch.object(CONFIRM, "writer_stopped", return_value=False),
            mock.patch.object(CONFIRM, "edit_text_once") as edit,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "WRITER_NOT_STOPPED"
            ),
        ):
            CONFIRM.run(self.args)
        edit.assert_not_called()
        self.assertIsNone(self.marker())

    def test_unsafe_database_path_blocks_before_any_network(self):
        """Only an unlinked regular SQLite source is eligible."""

        self.gate.safe_regular_file.return_value = False
        with (
            mock.patch.object(CONFIRM, "writer_stopped") as stopped,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "DATABASE_PATH_UNSAFE"
            ),
        ):
            CONFIRM.run(self.args)
        stopped.assert_not_called()

    def test_execute_requires_approved_production_chat_environment(self):
        """A caller cannot switch the editing target by env-name override."""

        self.args.execute = True
        self.args.chat_env = "TEST_OTHER_CHAT"
        with (
            mock.patch.object(CONFIRM, "writer_stopped") as stopped,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "CHAT_ENV_NOT_PRODUCTION"
            ),
        ):
            CONFIRM.run(self.args)
        stopped.assert_not_called()

    def test_owner_approval_is_required_before_writer_checks(self):
        """Missing owner evidence never invokes Telegram."""

        self.gate.owner_approval_verified.return_value = False
        with (
            mock.patch.object(CONFIRM, "writer_stopped") as stopped,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "OWNER_APPROVAL_INVALID"
            ),
        ):
            CONFIRM.run(self.args)
        stopped.assert_not_called()

    def test_exact_snapshot_parity_is_mandatory(self):
        """Source archives alone cannot approve metadata changes."""

        self.gate.run_archive_validator.return_value = False
        with (
            mock.patch.object(CONFIRM, "writer_stopped", return_value=True),
            mock.patch.object(CONFIRM, "edit_text_once") as edit,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "SNAPSHOT_PARITY_FAIL"
            ),
        ):
            CONFIRM.run(self.args)
        edit.assert_not_called()

    def test_exact_receipt_and_one_time_marker(self):
        """Only one confirmed edit binds text to existing channel and ID."""

        self.args.execute = True
        with (
            mock.patch.object(CONFIRM, "writer_stopped", return_value=True),
            mock.patch.object(CONFIRM, "safe_token", return_value="test"),
            mock.patch.object(CONFIRM, "edit_text_once") as edit,
        ):
            result = CONFIRM.run(self.args)
            self.assertEqual(result["status"], "PASS")
            edit.assert_called_once()
            self.assertEqual(edit.call_args.args[1:3], ("-100123", 101))
            with self.assertRaisesRegex(
                CONFIRM.VerificationError, "MEDIA_IDENTITY_ALREADY_SET"
            ):
                CONFIRM.run(self.args)
        self.assertEqual(self.marker(), ("text:-100123:101",))
        self.assertIsNone(self.pending_marker())

    def test_writer_restart_after_snapshot_blocks_before_reservation(self):
        """A stale stopped-writer result must not authorize Telegram edits."""

        self.args.execute = True
        with (
            mock.patch.object(
                CONFIRM, "writer_stopped", side_effect=[True, False]
            ) as writer,
            mock.patch.object(CONFIRM, "safe_token", return_value="test"),
            mock.patch.object(CONFIRM, "edit_text_once") as edit,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "WRITER_RESTARTED"
            ),
        ):
            CONFIRM.run(self.args)
        self.assertEqual(writer.call_count, 2)
        self.assertIsNone(self.pending_marker())
        self.assertIsNone(self.marker())
        edit.assert_not_called()

    def test_writer_restart_after_reservation_blocks_api_request(self):
        """Block editing after writer restart; preserve pending intent."""

        self.args.execute = True
        with (
            mock.patch.object(
                CONFIRM, "writer_stopped", side_effect=[True, True, False]
            ) as writer,
            mock.patch.object(CONFIRM, "safe_token", return_value="test"),
            mock.patch.object(CONFIRM, "edit_text_once") as edit,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "WRITER_RESTARTED"
            ),
        ):
            CONFIRM.run(self.args)
        self.assertEqual(writer.call_count, 3)
        self.assertEqual(self.pending_marker(), ("pending",))
        self.assertIsNone(self.marker())
        edit.assert_not_called()

    def test_status_rotation_during_edit_never_confirms_wrong_id(self):
        """An externally rotated status cannot inherit old media identity."""

        self.args.execute = True

        def rotate_during_edit(*_):
            """Simulate a status-ID update while the API call is underway."""
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute(
                    "UPDATE meta SET value='102' "
                    "WHERE key='telegram_status_message_id'"
                )

        with (
            mock.patch.object(CONFIRM, "writer_stopped", return_value=True),
            mock.patch.object(CONFIRM, "safe_token", return_value="test"),
            mock.patch.object(
                CONFIRM, "edit_text_once", side_effect=rotate_during_edit
            ) as edit,
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "STATUS_ID_CHANGED"
            ),
        ):
            CONFIRM.run(self.args)
        edit.assert_called_once()
        self.assertEqual(self.pending_marker(), ("pending",))
        self.assertIsNone(self.marker())

    def test_timeout_leaves_unverified_marker_absent(self):
        """A possibly successful Telegram request cannot be auto-retried."""

        self.args.execute = True
        with (
            mock.patch.object(CONFIRM, "writer_stopped", return_value=True),
            mock.patch.object(CONFIRM, "safe_token", return_value="test"),
            mock.patch.object(
                CONFIRM,
                "edit_text_once",
                side_effect=CONFIRM.VerificationError(
                    "TELEGRAM_REPLY_UNCERTAIN"
                ),
            ),
            self.assertRaises(CONFIRM.VerificationError),
        ):
            CONFIRM.run(self.args)
        self.assertIsNone(self.marker())
        self.assertEqual(self.pending_marker(), ("pending",))
        with self.assertRaisesRegex(
            CONFIRM.VerificationError, "PUBLICATION_UNRESOLVED"
        ):
            CONFIRM.current_status(self.db)

    def test_invalid_status_id_and_pending_send_block(self):
        """Never edit an ID if the persisted migration is unresolved."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "update meta set value='0' "
                "where key='telegram_status_message_id'"
            )
        with self.assertRaisesRegex(
            CONFIRM.VerificationError, "STATUS_ID_INVALID"
        ):
            CONFIRM.current_status(self.db)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "update meta set value='101' "
                "where key='telegram_status_message_id'"
            )
            db.execute(
                "insert into meta values "
                "('telegram_graphic_publish_pending','')"
            )
        with self.assertRaisesRegex(
            CONFIRM.VerificationError, "PUBLICATION_UNRESOLVED"
        ):
            CONFIRM.current_status(self.db)

    def test_text_receipt_validates_target_and_plain_content(self):
        """A malformed Bot API success is never valid media evidence."""

        valid = {
            "ok": True,
            "result": {
                "message_id": 101,
                "chat": {"id": -100123},
                "text": "Confirmation",
            },
        }
        CONFIRM.verify_receipt(valid, 101, "-100123", "Confirmation")
        for result in (
            {**valid, "result": {**valid["result"], "message_id": 102}},
            {**valid, "result": {**valid["result"], "chat": {"id": -10099}}},
            {**valid, "result": {**valid["result"], "text": "Wrong"}},
            {**valid, "result": {**valid["result"], "photo": [{}]}},
            {"ok": False, "result": valid["result"]},
            True,
        ):
            with (
                self.subTest(result_type=type(result).__name__),
                self.assertRaises(CONFIRM.VerificationError),
            ):
                CONFIRM.verify_receipt(result, 101, "-100123", "Confirmation")

    def test_edit_uses_only_one_text_update_with_matching_receipt(self):
        """Do not send, delete, pin or duplicate the status post."""

        body = "One-time verification"
        receipt = json.dumps(
            {
                "ok": True,
                "result": {
                    "message_id": 101,
                    "chat": {"id": -100123},
                    "text": body,
                },
            }
        ).encode("utf8")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = receipt
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(
            CONFIRM.urllib.request, "build_opener", return_value=opener
        ):
            CONFIRM.edit_text_once(
                "12345:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
                "-100123",
                101,
                body,
            )
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/editMessageText"))
        payload = json.loads(request.data)
        self.assertEqual(set(payload), {"chat_id", "message_id", "text"})
        self.assertEqual(payload["message_id"], 101)

    def test_network_timeout_does_not_authorize_unknown_text_edit(self):
        """One Telegram timeout must not retry or confirm a post."""

        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("network deadline")
        with (
            mock.patch.object(
                CONFIRM.urllib.request, "build_opener", return_value=opener
            ),
            self.assertRaisesRegex(
                CONFIRM.VerificationError, "TELEGRAM_REPLY_UNCERTAIN"
            ),
        ):
            CONFIRM.edit_text_once(
                "12345:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
                "-100123",
                101,
                "Confirmation",
            )
        opener.open.assert_called_once()

    def test_token_file_must_be_private_and_regular(self):
        """No world-readable or linked credentials may cross the gate."""

        token = self.dir / "bot-token"
        token.write_text(
            "12345:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh", encoding="ascii"
        )
        token.chmod(0o600)
        self.gate.safe_regular_file.return_value = True
        self.assertTrue(CONFIRM.safe_token(token, self.gate))
        token.chmod(0o644)
        with self.assertRaisesRegex(
            CONFIRM.VerificationError, "TOKEN_PATH_UNSAFE"
        ):
            CONFIRM.safe_token(token, self.gate)
        token.chmod(0o600)
        linked = self.dir / "linked-token"
        linked.symlink_to(token)
        self.gate.safe_regular_file.return_value = False
        with self.assertRaisesRegex(
            CONFIRM.VerificationError, "TOKEN_PATH_UNSAFE"
        ):
            CONFIRM.safe_token(linked, self.gate)

    def test_writer_gate_requires_stopped_service_and_container(self):
        """An inactive service is not enough if Docker is still running."""

        def result(value: str, code: int = 0):
            return subprocess.CompletedProcess([], code, stdout=value)

        for service, container, allowed in (
            ("active\n", "true\n", False),
            ("inactive\n", "true\n", False),
            ("active\n", "false\n", False),
            ("inactive\n", "false\n", True),
        ):
            with (
                self.subTest(service=service, container=container),
                mock.patch.object(
                    CONFIRM.subprocess,
                    "run",
                    side_effect=[result(service), result(container)],
                ),
            ):
                self.assertEqual(CONFIRM.writer_stopped(), allowed)

    def test_confirmation_body_marks_voltage_historical(self):
        """An intentional writer stop cannot make a last sample live."""

        text = CONFIRM.confirmation_text(time.time() - 300, 217.2)
        self.assertIn("історичні дані", text)
        self.assertIn("\n", text)
        self.assertNotIn("\\n", text)
        self.assertIn("217.2 В", text)

    def test_unknown_telegram_outcome_is_not_reported_as_no_edit(self):
        """A database failure after API transport is explicitly uncertain."""

        self.args.execute = True
        output = io.StringIO()
        with (
            mock.patch.object(CONFIRM, "arguments", return_value=self.args),
            mock.patch.object(
                CONFIRM,
                "run",
                side_effect=CONFIRM.VerificationError("DB_COMMIT_UNCERTAIN"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(CONFIRM.main(), 2)
        state = json.loads(output.getvalue())
        self.assertEqual(state["telegram_edited"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
