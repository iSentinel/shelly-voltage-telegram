"""Telegram transport must reject malformed JSON and 429 retry hints."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from test_app import APP, config


class TelegramResponseSafetyTests(unittest.TestCase):
    """Exercise mocked Bot API traffic without any real network requests."""

    def setUp(self):
        """Build a Telegram instance using nonfunctional test credentials."""

        self.bot = APP.Telegram(
            config(telegram_token="test-only", telegram_chat_id="-100123")
        )

    def test_invalid_json_types_are_rejected(self):
        """Successful JSON parsing alone does not guarantee an API object."""

        for payload in ([], [1], None, 3, "not an API object", True):
            with self.subTest(payload=repr(payload)):
                result = Mock()
                result.json.return_value = payload
                self.bot.api_session.post = Mock(return_value=result)
                with self.assertRaisesRegex(
                    RuntimeError, "invalid JSON object"
                ):
                    self.bot.call("getMe", {})

    def test_bad_rate_limit_retry_values_use_one_second(self):
        """Reject NaN, infinity, negatives and invalid retry objects."""

        invalid = (None, [], {}, True, -1, "-2", "nan", "Infinity", "bad")
        for value in invalid:
            with self.subTest(value=repr(value)):
                first = {
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": value},
                }
                second = {"ok": True, "result": {"id": 8}}
                with (
                    patch.object(
                        self.bot, "_post", side_effect=[first, second]
                    ) as post,
                    patch.object(APP.time, "sleep") as sleep,
                ):
                    self.assertEqual(self.bot.call("getMe", {}), {"id": 8})
                post.assert_called()
                self.assertEqual(post.call_count, 2)
                sleep.assert_called_once_with(1.0)

    def test_malformed_rate_limit_parameters_use_default(self):
        """Null, list or text parameters are not valid retry objects."""

        for params in (None, [], "corrupted"):
            with self.subTest(params=repr(params)):
                self.assertEqual(
                    self.bot.rate_limit_delay({"parameters": params}), 1.0
                )

    def test_valid_delay_is_bounded(self):
        """Retain valid short waits and cap excessively long waits."""

        for value, expected in (
            (0, 0.0),
            (2, 2.0),
            ("2.5", 2.5),
            (9999999, 30.0),
            (1e300, 30.0),
        ):
            with self.subTest(value=value):
                self.assertEqual(
                    self.bot.rate_limit_delay(
                        {"parameters": {"retry_after": value}}
                    ),
                    expected,
                )

    def test_plain_success_does_not_sleep_or_retry(self):
        """A normal Telegram response must not trigger extra requests."""

        response = {"ok": True, "result": {"id": 9}}
        with (
            patch.object(self.bot, "_post", return_value=response) as post,
            patch.object(APP.time, "sleep") as sleep,
        ):
            self.assertEqual(self.bot.call("getMe", {}), {"id": 9})
        post.assert_called_once_with("getMe", {})
        sleep.assert_not_called()

    def test_repeated_429_does_not_retry_forever(self):
        """The transport must not loop indefinitely after another 429."""

        repeated = {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 1},
            "description": "Too Many Requests",
        }
        with (
            patch.object(self.bot, "_post", return_value=repeated) as post,
            patch.object(APP.time, "sleep"),
            self.assertRaises(RuntimeError),
        ):
            self.bot.call("getMe", {})
        self.assertEqual(post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
