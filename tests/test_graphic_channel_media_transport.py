"""Verify a source-only editMessageMedia transport without Telegram traffic."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from test_app import APP, config


class GraphicChannelMediaTransportTests(unittest.TestCase):
    """Do not publish, duplicate or edit a real channel during QA."""

    def setUp(self):
        """Construct one fake Telegram bot and one tiny PNG."""

        self.bot = APP.Telegram(
            config(telegram_token="test-only", telegram_chat_id="-100123")
        )
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.card = Path(self.temp.name) / "card.png"
        self.card.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"test")

    def response(self, payload: object) -> None:
        """Return one fake Telegram JSON object from the upload."""

        result = Mock()
        result.json.return_value = payload
        self.bot.api_session.post = Mock(return_value=result)

    def test_edit_existing_channel_message_without_new_post(self):
        """Use multipart editMessageMedia with an unchanged message ID."""

        self.response({"ok": True, "result": {"message_id": 811}})
        self.bot.edit_photo(811, self.card, "План YASNO", "-100123")
        self.bot.api_session.post.assert_called_once()
        call = self.bot.api_session.post.call_args
        self.assertTrue(call.args[0].endswith("/editMessageMedia"))
        self.assertEqual(call.kwargs["data"]["chat_id"], "-100123")
        self.assertEqual(call.kwargs["data"]["message_id"], "811")
        self.assertEqual(
            call.kwargs["data"]["media"],
            json.dumps(
                {
                    "type": "photo",
                    "media": "attach://photo",
                    "caption": "План YASNO",
                },
                ensure_ascii=False,
            ),
        )
        upload = call.kwargs["files"]["photo"]
        self.assertEqual(upload[0], "card.png")
        self.assertEqual(upload[2], "image/png")

    def test_missing_target_never_defaults_to_public_chat(self):
        """A future caller must provide a message ID and explicit chat."""

        self.bot.api_session.post = Mock()
        for id_, chat in ((0, "-100123"), (True, "-100123"), (7, "")):
            with (
                self.subTest(id=id_, chat=chat),
                self.assertRaises(ValueError),
            ):
                self.bot.edit_photo(id_, self.card, "Forecast", chat)
        self.bot.api_session.post.assert_not_called()

    def test_invalid_png_and_symlink_are_rejected_before_network(self):
        """A bogus image cannot reach an authenticated Telegram API call."""

        self.bot.api_session.post = Mock()
        corrupt = Path(self.temp.name) / "invalid.png"
        corrupt.write_bytes(b"not-a-valid-image")
        linked = Path(self.temp.name) / "link.png"
        linked.symlink_to(self.card)
        for path in (corrupt, linked):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bot.edit_photo(811, path, "Caption", "-100123")
        self.bot.api_session.post.assert_not_called()

    def test_media_and_caption_limits_are_fail_closed(self):
        """Do not send over-limit photos or more than 1024 caption chars."""

        self.bot.api_session.post = Mock()
        with self.assertRaises(ValueError):
            self.bot.edit_photo(811, self.card, "x" * 1025, "-100123")
        large = Path(self.temp.name) / "too-large.png"
        with large.open("wb") as file:
            file.truncate(10_000_001)
        with self.assertRaises(ValueError):
            self.bot.edit_photo(811, large, "Forecast", "-100123")
        self.bot.api_session.post.assert_not_called()

    def test_rejected_or_malformed_api_reply_does_not_claim_success(self):
        """Errors stay redacted; the transport cannot create new posts."""

        for payload in (
            {"ok": False, "error_code": 400, "description": "secret"},
            {"ok": True, "result": {"message_id": 812}},
            {"ok": True, "result": True},
            [],
        ):
            with self.subTest(payload=repr(payload)):
                self.response(payload)
                with self.assertRaises(RuntimeError) as failure:
                    self.bot.edit_photo(811, self.card, "Forecast", "-100123")
                self.assertNotIn("secret", str(failure.exception))
                self.bot.api_session.post.assert_called_once()

    def test_repeated_identical_media_edit_keeps_stable_message(self):
        """Telegram no-op edit is success, not a reason to replace the post."""

        self.response(
            {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message is not modified",
            }
        )
        self.bot.edit_photo(811, self.card, "Same card", "-100123")
        self.bot.api_session.post.assert_called_once()

    def test_media_response_must_match_destination_chat(self):
        """Refuse to mark an edit successful for a different channel."""

        self.response(
            {
                "ok": True,
                "result": {"message_id": 811, "chat": {"id": -100999}},
            }
        )
        with self.assertRaisesRegex(RuntimeError, "unexpected edited chat"):
            self.bot.edit_photo(811, self.card, "Card", "-100123")

    def test_blank_channel_target_rejected_before_network(self):
        """Whitespace is never an acceptable media destination."""

        self.bot.api_session.post = Mock()
        with self.assertRaises(ValueError):
            self.bot.edit_photo(811, self.card, "Card", "   ")
        self.bot.api_session.post.assert_not_called()

    def test_future_public_photo_send_respects_sound_policy(self):
        """Explicit public photo sends use the intended chat and cooldown."""

        self.response({"ok": True, "result": {"message_id": 930}})
        self.bot.quiet_now = Mock(return_value=False)
        posted = self.bot.send_status_photo(
            self.card, "Current grid", "-100456", notify=True
        )
        self.assertEqual(posted, 930)
        call = self.bot.api_session.post.call_args
        self.assertTrue(call.args[0].endswith("/sendPhoto"))
        self.assertEqual(call.kwargs["data"]["chat_id"], "-100456")
        self.assertEqual(call.kwargs["data"]["disable_notification"], "false")

    def test_future_public_photo_send_is_quiet_during_quiet_hours(self):
        """Quiet hours override any requested audible photo alert."""

        self.response({"ok": True, "result": {"message_id": 930}})
        self.bot.quiet_now = Mock(return_value=True)
        self.bot.send_status_photo(
            self.card, "Grid status", "-100456", notify=True
        )
        self.assertEqual(
            self.bot.api_session.post.call_args.kwargs["data"][
                "disable_notification"
            ],
            "true",
        )

    def test_future_public_photo_never_defaults_to_private_chat(self):
        """Reject an empty destination or inaccessible caption before API."""

        self.bot.api_session.post = Mock()
        for channel, caption in (("", "Grid"), ("  ", "Grid"), ("-100", "")):
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                self.bot.send_status_photo(self.card, caption, channel)
        self.bot.api_session.post.assert_not_called()

    def test_current_status_loop_does_not_publish_photos_yet(self):
        """Source-only transport must not silently activate public photos."""

        source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
        self.assertNotIn("self.telegram.send_status_photo(", source)

    def media_monitor(self, initial: dict) -> tuple[object, dict]:
        """Build a storage-only monitor without production components."""

        state = dict(initial)
        storage = Mock()
        storage.get_meta.side_effect = state.get
        storage.set_meta.side_effect = state.__setitem__
        monitor = SimpleNamespace(
            storage=storage,
            telegram=SimpleNamespace(status_chat_id="-100123"),
            status_message_key=lambda: "telegram_status_message_id",
        )
        return monitor, state

    def test_status_media_kind_is_unknown_for_legacy_text(self):
        """Never infer photo or text from an unbound old message ID."""

        monitor, state = self.media_monitor(
            {"telegram_status_message_id": "101"}
        )
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "unknown")
        self.assertNotIn("telegram_status_media_identity", state)

    def test_status_media_kind_stays_bound_to_same_message(self):
        """Known text or photo format survives a restart with the same ID."""

        monitor, _ = self.media_monitor({"telegram_status_message_id": "101"})
        APP.Monitor.record_status_media_kind(monitor, 101, "text", "-100123")
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "text")
        APP.Monitor.record_status_media_kind(monitor, 101, "photo", "-100123")
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "photo")

    def test_rotated_message_does_not_inherit_old_media_kind(self):
        """A prior photo marker cannot affect a newly created text status."""

        monitor, state = self.media_monitor(
            {
                "telegram_status_message_id": "102",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "unknown")
        state["telegram_status_message_id"] = "101"
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "photo")

    def test_status_format_write_rejects_unpersisted_identity(self):
        """Never record an ID before Telegram and SQLite agree on it."""

        monitor, state = self.media_monitor(
            {"telegram_status_message_id": "101"}
        )
        for message_id, kind in (
            (102, "photo"),
            (0, "photo"),
            (True, "text"),
            (101, "video"),
        ):
            with (
                self.subTest(message_id=message_id, kind=kind),
                self.assertRaises(ValueError),
            ):
                APP.Monitor.record_status_media_kind(
                    monitor, message_id, kind, "-100123"
                )
        self.assertNotIn("telegram_status_media_identity", state)

    def test_chat_rotation_invalidates_message_media_kind(self):
        """Even an identical numeric message ID is unknown in a new chat."""

        monitor, _ = self.media_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "photo")
        monitor.telegram.status_chat_id = "-100456"
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "unknown")

    def test_refuse_storing_media_for_another_chat(self):
        """Reject cross-channel format metadata before database mutation."""

        monitor, state = self.media_monitor(
            {"telegram_status_message_id": "101"}
        )
        for target in ("-100456", "", "  ", None):
            with (
                self.subTest(target=target),
                self.assertRaises(ValueError),
            ):
                APP.Monitor.record_status_media_kind(
                    monitor, 101, "photo", target
                )
        self.assertNotIn("telegram_status_media_identity", state)

    def test_missing_message_format_is_distinct_from_unknown(self):
        """No message is not the same state as unverified message type."""

        monitor, _ = self.media_monitor({})
        self.assertEqual(APP.Monitor.status_media_kind(monitor), "missing")

    def test_live_status_loop_does_not_call_media_edit_yet(self):
        """Do not silently convert the deployed channel status to a photo."""

        source = (Path(__file__).resolve().parents[1] / "app.py").read_text()
        self.assertNotIn("self.telegram.edit_photo(", source)
        self.assertIn("self.telegram.edit(", source)
        self.assertNotIn("TELEGRAM_GRAPHIC_CHANNEL_ENABLED", source)


if __name__ == "__main__":
    unittest.main()
