"""Telegram private send/edit acceptance without real network or messages."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "telegram_private_acceptance",
    ROOT / "scripts/telegram-private-acceptance.py",
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def args(mode: str = "dry_run", admin: str = "1234") -> argparse.Namespace:
    """Return an isolated, explicit test configuration."""

    return argparse.Namespace(
        dry_run=mode == "dry_run",
        check=mode == "check",
        execute=mode == "execute",
        recipient_id="1234",
        admin_id=admin,
        token_file="/fake/no-file",
        confirm=QA.CONFIRM,
    )


class PrivateTelegramAcceptanceTests(unittest.TestCase):
    """Prevent messages in public/group chats and accidental sends."""

    def test_dry_run_has_zero_network_and_zero_secret_reads(self):
        """The default exploratory mode cannot access external systems."""

        with (
            patch.object(QA, "api") as request,
            patch.object(QA, "read_token") as token,
        ):
            result = QA.run(args())
        self.assertIn("no_network=true", result)
        request.assert_not_called()
        token.assert_not_called()

    def test_negative_group_or_channel_identifiers_rejected(self):
        """Only a positive exact admin user ID is valid."""

        for ident in ("-1001234", "0", "", "a", "1234.0", "+1234"):
            with (
                self.subTest(ident=ident),
                self.assertRaises(ValueError),
            ):
                QA.recipient_id(ident, ident)

    def test_unapproved_user_cannot_receive_even_a_check(self):
        """The admin ID must exactly match the recipient ID."""

        with (
            patch.object(QA, "api") as network,
            self.assertRaisesRegex(ValueError, "approved admin"),
        ):
            QA.run(args("check", admin="2222"))
        network.assert_not_called()

    def test_execution_requires_exact_confirmation(self):
        """Lacking explicit authorization must block secret reads and API."""

        settings = args("execute")
        settings.confirm = "yes"
        with (
            patch.object(QA, "read_token") as token,
            patch.object(QA, "api") as network,
            self.assertRaisesRegex(ValueError, "confirmation"),
        ):
            QA.run(settings)
        token.assert_not_called()
        network.assert_not_called()

    def test_unlisted_user_cannot_read_secrets_or_send(self):
        """Never permit tests to non-admin recipients."""

        with (
            patch.dict(os.environ, {"TELEGRAM_ADMIN_USER_IDS": "5678"}),
            patch.object(QA, "read_token") as secret,
            patch.object(QA, "api") as network,
            self.assertRaisesRegex(ValueError, "allowlist"),
        ):
            QA.run(args("execute"))
        secret.assert_not_called()
        network.assert_not_called()

    def test_readonly_check_calls_only_getme_getchat(self):
        """A verified private chat is inspected without sending anything."""

        responses = [{"is_bot": True}, {"type": "private", "id": 1234}]
        with (
            patch.dict(os.environ, {"TELEGRAM_ADMIN_USER_IDS": "1234"}),
            patch.object(QA, "read_token", return_value="test-token"),
            patch.object(QA, "api", side_effect=responses) as network,
        ):
            output = QA.run(args("check"))
        self.assertIn("writes=0", output)
        self.assertEqual(
            [call.args[1] for call in network.call_args_list],
            ["getMe", "getChat"],
        )

    def test_channel_group_and_wrong_private_id_fail_closed(self):
        """No Bot API send is permitted if preflight is not private."""

        for type_, ident in (
            ("channel", -1),
            ("supergroup", -2),
            ("private", 999),
        ):
            with self.subTest(type_=type_, ident=ident):
                with (
                    patch.object(
                        QA,
                        "api",
                        side_effect=[
                            {"is_bot": True},
                            {"type": type_, "id": ident},
                        ],
                    ) as network,
                    self.assertRaisesRegex(ValueError, "private chat"),
                ):
                    QA.verify_private("token", 1234)
                self.assertEqual(network.call_count, 2)

    def test_send_and_edit_only_new_silent_private_message(self):
        """Both calls address the exact private recipient, not the channel."""

        with patch.object(
            QA, "api", side_effect=[{"message_id": 721}, {"message_id": 721}]
        ) as network:
            QA.send_and_edit("test-token", 1234)
        self.assertEqual(network.call_count, 2)
        send, edit = [call.args for call in network.call_args_list]
        self.assertEqual(
            [send[1], edit[1]], ["sendMessage", "editMessageText"]
        )
        self.assertEqual(send[2]["chat_id"], 1234)
        self.assertEqual(edit[2]["chat_id"], 1234)
        self.assertTrue(send[2]["disable_notification"])
        self.assertEqual(edit[2]["message_id"], 721)
        self.assertIn("TEST", send[2]["text"])
        self.assertNotIn("Power is off", send[2]["text"])

    def test_missing_message_id_stops_before_edit(self):
        """Never guess which message should be edited."""

        with (
            patch.object(QA, "api", return_value={}) as network,
            self.assertRaisesRegex(ValueError, "message ID"),
        ):
            QA.send_and_edit("test-token", 1234)
        network.assert_called_once()

    def test_token_requires_protected_plain_file(self):
        """No symlink, group-readable or world-readable secrets are valid."""

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "token"
            p.write_text("TEST:dummy", encoding="utf-8")
            p.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "permissions"):
                QA.read_token(str(p))
            p.chmod(0o600)
            self.assertEqual(QA.read_token(str(p)), "TEST:dummy")
            link = Path(tmp) / "link"
            link.symlink_to(p)
            with self.assertRaisesRegex(ValueError, "regular"):
                QA.read_token(str(link))

    def test_unknown_api_methods_do_not_open_network(self):
        """Delete, pin and arbitrary Bot API methods are prohibited."""

        with (
            patch.object(QA.urllib.request, "build_opener") as opener,
            self.assertRaisesRegex(ValueError, "not allowed"),
        ):
            QA.api("token", "deleteMessage", {"chat_id": 1234})
        opener.assert_not_called()

    def test_reports_do_not_print_private_ids(self):
        """Invalid inputs report only safe exception types."""

        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=args("check", "98765"),
            ),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 1)
        text = output.getvalue()
        self.assertIn("FAIL private_telegram_qa", text)
        self.assertNotIn("1234", text)
        self.assertNotIn("98765", text)


if __name__ == "__main__":
    unittest.main()
