"""Protect Telegram error logging while keeping status cleanup semantics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_app import APP, config


class TelegramApiErrorRedactionTests(unittest.TestCase):
    """Exercise only mocked Bot API responses, never actual chat content."""

    def setUp(self):
        """Use a nonfunctional Bot API token and local fake chat IDs."""

        self.telegram = APP.Telegram(
            config(
                telegram_token="fake-private-token", telegram_chat_id="-100123"
            )
        )

    def test_unknown_descriptions_are_redacted(self):
        """Remote raw text may contain secrets or personal information."""

        for raw in (
            "Forbidden: fake-private-token -100123 private-user",
            "Bad Request: unknown secret='private-marker'",
            {"secret": "private-marker"},
            ["secret"],
            None,
            True,
        ):
            with self.subTest(raw=repr(raw)):
                text = APP.safe_telegram_api_error(
                    {"error_code": 403, "description": raw}
                )
                self.assertEqual(text, "Telegram API error 403")
                for secret in (
                    "private-marker",
                    "fake-private-token",
                    "-100123",
                    "private-user",
                ):
                    self.assertNotIn(secret, text)

    def test_error_code_is_not_untrusted_text(self):
        """Only known numeric HTTP-style codes may be shown in errors."""

        for code in (True, "403-private-marker", -1, 10000, None, {}):
            with self.subTest(code=repr(code)):
                self.assertEqual(
                    APP.safe_telegram_api_error(
                        {"error_code": code, "description": "secret"}
                    ),
                    "Telegram API error",
                )

    def test_message_not_modified_marker_survives(self):
        """Identical edit success must not become a false send failure."""

        for desc in (
            "Bad Request: message is not modified",
            "Bad Request: message is not modified: details are private",
        ):
            with self.subTest(desc=desc):
                text = APP.safe_telegram_api_error(
                    {"error_code": 400, "description": desc}
                )
                self.assertTrue(APP.message_not_modified(RuntimeError(text)))
                self.assertNotIn("details are private", text)

    def test_missing_message_markers_survive(self):
        """Known missing/edit-forbidden states require safe recreation."""

        for desc in (
            "Bad Request: message to edit not found",
            "Bad Request: message can't be edited",
            "Bad Request: message_id_invalid",
        ):
            with self.subTest(desc=desc):
                safe = APP.safe_telegram_api_error(
                    {"error_code": 400, "description": desc}
                )
                self.assertTrue(APP.message_missing(RuntimeError(safe)))

    def test_already_unpinned_marker_survives(self):
        """Normal unpin cleanup must continue after common Telegram 400."""

        text = APP.safe_telegram_api_error(
            {
                "error_code": 400,
                "description": "Bad Request: message to unpin not found",
            }
        )
        self.assertIn("message to unpin not found", text)

    def test_untrusted_prefix_never_triggers_special_handling(self):
        """A random exception mentioning a marker cannot bypass cleanup."""

        for secret in (
            "secret: message is not modified",
            "secret: message to edit not found",
            "secret: message to unpin not found",
        ):
            with self.subTest(secret=secret):
                result = APP.safe_telegram_api_error(
                    {"error_code": 400, "description": secret}
                )
                self.assertEqual(result, "Telegram API error 400")
                self.assertFalse(
                    APP.message_not_modified(RuntimeError(result))
                )
                self.assertFalse(APP.message_missing(RuntimeError(result)))

    def test_regular_api_call_does_not_expose_error_description(self):
        """The general transport wraps rejected Bot API replies safely."""

        reply = {
            "ok": False,
            "error_code": 403,
            "description": "blocked fake-private-token -100123",
        }
        self.telegram._post = Mock(return_value=reply)
        with self.assertRaises(RuntimeError) as raised:
            self.telegram.call("getMe", {})
        self.assertEqual(str(raised.exception), "Telegram API error 403")

    def test_upload_error_and_malformed_json_are_safe(self):
        """File uploads use the same error redaction as regular calls."""

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fake.csv"
            path.write_text("example,1\n")
            response = Mock()
            self.telegram.api_session.post = Mock(return_value=response)
            for payload in (
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "secret fake-private-token",
                },
                ["private-marker"],
            ):
                with self.subTest(payload=repr(payload)):
                    response.json.return_value = payload
                    with self.assertRaises(RuntimeError) as raised:
                        self.telegram.send_document(path)
                    message = str(raised.exception)
                    self.assertNotIn("private-marker", message)
                    self.assertNotIn("fake-private-token", message)
                    self.assertNotIn("-100123", message)


if __name__ == "__main__":
    unittest.main()
