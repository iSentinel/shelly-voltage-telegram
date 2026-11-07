"""Validate Telegram channel posting/editing permissions without sending."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from test_app import APP, config


class TelegramChannelRightsTests(unittest.TestCase):
    """Use mocked Telegram API calls, never real channel operations."""

    def setUp(self):
        """Provide safe, unused tokens and test-only channel references."""

        self.cfg = config(
            telegram_token="private-test-token",
            telegram_chat_id="-100123",
            telegram_status_chat_id="-100456",
        )

    def verify(self, member, chat_type="channel", user_id=123):
        """Call rights check against mocked API with no mutation."""

        output = io.StringIO()
        with (
            patch.object(APP.Telegram, "get_me", return_value={"id": user_id}),
            patch.object(
                APP.Telegram, "get_chat", return_value={"type": chat_type}
            ),
            patch.object(APP.Telegram, "call", return_value=member) as call,
            patch.object(
                APP.Telegram,
                "send",
                side_effect=AssertionError("send forbidden"),
            ),
            patch.object(
                APP.Storage,
                "initialize",
                side_effect=AssertionError("SQLite write forbidden"),
            ),
            redirect_stdout(output),
        ):
            rc = APP.telegram_channel_rights_check(self.cfg)
        return rc, output.getvalue(), call.call_args_list

    def test_admin_with_both_rights_passes_without_leaking_ids(self):
        """Only explicit true booleans grant channel permission PASS."""

        member = {
            "status": "administrator",
            "can_post_messages": True,
            "can_edit_messages": True,
        }
        rc, stdout, calls = self.verify(member)
        self.assertEqual(rc, 0)
        self.assertIn("RIGHTS=PASS post=yes edit=yes", stdout)
        self.assertIn("not verified", stdout)
        for private in ("-100123", "-100456", "private-test-token", "123"):
            self.assertNotIn(private, stdout)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], "getChatMember")

    def test_missing_post_right_fails_closed(self):
        """An administrator without posting rights cannot publish."""

        member = {
            "status": "administrator",
            "can_post_messages": False,
            "can_edit_messages": True,
        }
        rc, stdout, _ = self.verify(member)
        self.assertEqual(rc, 2)
        self.assertIn("posting_not_allowed", stdout)

    def test_missing_edit_right_fails_closed(self):
        """An administrator without editing rights needs a warning."""

        member = {
            "status": "administrator",
            "can_post_messages": True,
            "can_edit_messages": False,
        }
        rc, stdout, _ = self.verify(member)
        self.assertEqual(rc, 2)
        self.assertIn("editing_not_allowed", stdout)

    def test_omitted_rights_are_not_assumed(self):
        """Missing Telegram rights are not interpreted as granted."""

        rc, stdout, _ = self.verify({"status": "administrator"})
        self.assertEqual(rc, 2)
        self.assertIn("posting_not_allowed", stdout)

    def test_creator_can_post_and_edit(self):
        """The owner role has full channel permissions."""

        rc, stdout, _ = self.verify({"status": "creator"})
        self.assertEqual(rc, 0)
        self.assertIn("RIGHTS=PASS", stdout)

    def test_removed_or_regular_members_do_not_pass(self):
        """A removed or non-admin bot is not ready to publish."""

        for role in ("left", "kicked", "member", "restricted", "unknown"):
            with self.subTest(role=role):
                rc, stdout, _ = self.verify({"status": role})
                self.assertEqual(rc, 2)
                self.assertIn("not_administrator", stdout)

    def test_invalid_membership_shape_fails(self):
        """Unexpected API data cannot satisfy the permission gate."""

        for value in (None, [], "admin", True):
            with self.subTest(value=repr(value)):
                rc, stdout, _ = self.verify(value)
                self.assertEqual(rc, 2)
                self.assertIn("invalid_membership", stdout)

    def test_non_channel_config_is_skipped(self):
        """Do not claim channel administration for private/group chats."""

        rc, stdout, calls = self.verify({}, chat_type="private")
        self.assertEqual(rc, 2)
        self.assertIn("SKIP", stdout)
        self.assertEqual(calls, [])

    def test_bad_bot_identifier_is_rejected(self):
        """Bad getMe identities must never be passed to getChatMember."""

        for bad in (True, None, "123", -1, 0):
            with self.subTest(id=repr(bad)):
                rc, stdout, calls = self.verify({}, user_id=bad)
                self.assertEqual(rc, 2)
                self.assertIn("ValueError", stdout)
                self.assertEqual(calls, [])

    def test_api_error_does_not_reveal_exception_text(self):
        """Even malicious error details must not leak tokens or IDs."""

        output = io.StringIO()
        with (
            patch.object(
                APP.Telegram,
                "get_me",
                side_effect=RuntimeError("secret-token-and-chat-id"),
            ),
            redirect_stdout(output),
        ):
            rc = APP.telegram_channel_rights_check(self.cfg)
        self.assertEqual(rc, 2)
        self.assertIn("RuntimeError", output.getvalue())
        self.assertNotIn("secret-token-and-chat-id", output.getvalue())

    def test_disabled_configuration_skips_network(self):
        """Disabled Telegram runs must not contact remote APIs."""

        cfg = config(telegram_token="", telegram_chat_id="")
        output = io.StringIO()
        with (
            patch.object(
                APP.Telegram,
                "get_me",
                side_effect=AssertionError("network forbidden"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(APP.telegram_channel_rights_check(cfg), 0)
        self.assertIn("DISABLED", output.getvalue())

    def test_cli_parser_has_readonly_command(self):
        """The CLI exposes the command and help without side effects."""

        args = APP.parser().parse_args(["telegram-channel-rights"])
        self.assertEqual(args.command, "telegram-channel-rights")


if __name__ == "__main__":
    unittest.main()
