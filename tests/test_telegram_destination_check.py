"""Read-only Telegram destination diagnostics must not expose private IDs."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from test_app import APP, config


class TelegramDestinationCheckTests(unittest.TestCase):
    """Verify safe check output and absence of state-changing calls."""

    def check(self, control: dict, status: dict, bot: dict | None = None):
        """Run destination check against mocked Bot API GET responses."""

        cfg = config(
            telegram_token="private-test-token",
            telegram_chat_id="-100123",
            telegram_status_chat_id="-100456",
        )
        output = io.StringIO()
        with (
            patch.object(
                APP.Telegram,
                "get_me",
                return_value=bot or {"username": "mock_bot"},
            ),
            patch.object(
                APP.Telegram, "get_chat", side_effect=[control, status]
            ) as get_chat,
            patch.object(
                APP.Telegram,
                "send",
                side_effect=AssertionError("message sent"),
            ),
            patch.object(
                APP.Storage,
                "initialize",
                side_effect=AssertionError("SQLite changed"),
            ),
            redirect_stdout(output),
        ):
            rc = APP.telegram_destinations_check(cfg)
        self.assertEqual(get_chat.call_count, 2)
        return rc, output.getvalue()

    def test_separate_status_channel_passes_without_identifiers(self):
        """A valid separate status channel is recognized without its ID."""

        code, output = self.check(
            {"id": -100123, "type": "private"},
            {"id": -100456, "type": "channel"},
        )
        self.assertEqual(code, 0)
        self.assertIn("TELEGRAM_DESTINATIONS=PASS", output)
        self.assertIn("separate=yes", output)
        self.assertIn("type=channel", output)
        for private in ("-100123", "-100456", "private-test-token"):
            self.assertNotIn(private, output)
        self.assertIn("not verified", output)

    def test_shared_chat_stays_a_valid_configuration(self):
        """Use of one chat for control and status is supported."""

        code, output = self.check({"id": 42}, {"id": 42, "type": "group"})
        self.assertEqual(code, 0)
        self.assertIn("separate=no", output)

    def test_missing_chat_id_does_not_become_false_pass(self):
        """Incomplete getChat responses must be rejected."""

        code, output = self.check({"id": 1}, {"type": "channel"})
        self.assertEqual(code, 2)
        self.assertIn("FAIL", output)

    def test_unexpected_chat_type_does_not_leak_provider_text(self):
        """Unknown chat types fail without echoing untrusted data."""

        marker = "arbitrary-sensitive-text"
        code, output = self.check({"id": 1}, {"id": 2, "type": marker})
        self.assertEqual(code, 2)
        self.assertNotIn(marker, output)

    def test_missing_bot_identity_fails_closed(self):
        """The expected bot identity must exist before claiming PASS."""

        code, output = self.check(
            {"id": 1}, {"id": 2, "type": "channel"}, bot={"username": ""}
        )
        self.assertEqual(code, 2)
        self.assertIn("FAIL", output)

    def test_network_failure_prints_only_exception_type(self):
        """A transport failure never exposes API tokens or chat values."""

        cfg = config(
            telegram_token="private-test-token", telegram_chat_id="-100123"
        )
        output = io.StringIO()
        with (
            patch.object(
                APP.Telegram,
                "get_me",
                side_effect=TimeoutError("secret response"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(APP.telegram_destinations_check(cfg), 2)
        self.assertIn("TimeoutError", output.getvalue())
        self.assertNotIn("secret response", output.getvalue())

    def test_disabled_telegram_is_reported_without_network(self):
        """Disabled deployments must not attempt Bot API requests."""

        cfg = config(telegram_token="", telegram_chat_id="")
        output = io.StringIO()
        with (
            patch.object(
                APP.Telegram,
                "get_me",
                side_effect=AssertionError("network called"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(APP.telegram_destinations_check(cfg), 0)
        self.assertEqual(
            output.getvalue().strip(), "TELEGRAM_DESTINATIONS=DISABLED"
        )

    def test_command_is_available_from_existing_parser(self):
        """Administrative CLI includes a named diagnostic mode."""

        self.assertEqual(
            APP.parser().parse_args(["telegram-check"]).command,
            "telegram-check",
        )


if __name__ == "__main__":
    unittest.main()
