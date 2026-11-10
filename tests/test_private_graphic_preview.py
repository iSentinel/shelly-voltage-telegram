"""No-network acceptance for the admin-only graphical PNG preview."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from test_app import APP, config
from test_compact_preview import make_monitor, private_update

KYIV = ZoneInfo("Europe/Kyiv")


def private_graphic_update(sender: int, chat_type: str = "private") -> dict:
    """Build a private or public-command fixture without Telegram traffic."""

    message = private_update(sender, chat_type)
    message["message"]["text"] = "/graphic_preview"
    return message


def today_yasno() -> dict:
    """Create one synthetic, fresh 48-slot Kyiv source cache result."""

    now = datetime.now(KYIV)
    return {
        "group": "10.1",
        "bars": "O" * 17 + "X" * 15 + "?" * 16,
        "day": now.date().isoformat(),
        "forecast_updated": time.time() - 10,
        "forecast_source": "yasno",
    }


class PrivateGraphicPreviewTests(unittest.TestCase):
    """Only an authorized administrator receives a private PNG upload."""

    def test_authorized_admin_receives_png_only_in_direct_chat(self):
        """Private image and caption must not use the channel destination."""

        monitor = make_monitor("uk")
        monitor.state.success(223.0, 50.0, 1.0, 1)
        images = []
        paths = []

        def inspect_upload(method, field, path, caption, chat_id):
            self.assertEqual(
                (method, field, chat_id), ("sendPhoto", "photo", "7")
            )
            self.assertIn("223.0", caption)
            self.assertIn("YASNO", caption)
            images.append(path.read_bytes())
            paths.append(path)
            self.assertLess(path.stat().st_size, 500_000)

        monitor.telegram.send_file = Mock(side_effect=inspect_upload)
        with patch.object(
            APP, "cached_yasno_preview_args", return_value=today_yasno()
        ):
            monitor.handle_update(private_graphic_update(7))
        self.assertEqual(len(images), 1)
        self.assertTrue(images[0].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertFalse(paths[0].exists())
        monitor.telegram.send.assert_not_called()
        monitor.telegram.edit.assert_not_called()
        monitor.telegram.pin.assert_not_called()

    def test_unknown_or_channel_sender_cannot_trigger_image(self):
        """No PNG upload may occur outside the authenticated private chat."""

        for sender, chat_type in ((8, "private"), (7, "channel")):
            with self.subTest(sender=sender, chat_type=chat_type):
                monitor = make_monitor("uk")
                monitor.telegram.send_file = Mock()
                monitor.handle_update(
                    private_graphic_update(sender, chat_type)
                )
                monitor.telegram.send_file.assert_not_called()
                monitor.telegram.send.assert_not_called()
                monitor.telegram.edit.assert_not_called()

    def test_offline_preview_hides_stale_voltage_in_png_and_caption(self):
        """Last-valid voltage is historical, even if the schedule is green."""

        monitor = make_monitor("uk")
        monitor.state.restore(time.time() - 650, 225.9, 50.0)
        monitor.state.error("unreachable", 1)
        snapshots = []
        monitor.telegram.send_file = Mock()
        original = APP.render_card_png

        def capture(state, path):
            status = original(state, path)
            snapshots.append(status)
            return status

        with (
            patch.object(APP, "render_card_png", side_effect=capture),
            patch.object(
                APP, "cached_yasno_preview_args", return_value=today_yasno()
            ),
        ):
            monitor.handle_update(private_graphic_update(7))
        self.assertEqual(len(snapshots), 1)
        self.assertFalse(snapshots[0]["live_voltage_visible"])
        self.assertTrue(snapshots[0]["forecast_visible"])
        caption = monitor.telegram.send_file.call_args.args[3]
        self.assertNotIn("225.9", caption)
        monitor.telegram.edit.assert_not_called()

    def test_bad_cache_hides_timeline_but_keeps_valid_meter(self):
        """Unavailable YASNO must never block the real meter card."""

        monitor = make_monitor("en")
        monitor.state.success(224.3, 50.0, 1.0, 1)
        with patch.object(APP, "cached_yasno_preview_args", return_value=None):
            state = APP.private_graphic_preview_state(monitor)
        self.assertNotIn('"b":', state)
        self.assertIn('"v":224.3', state)

    def test_private_photo_transport_preserves_default_channel_behavior(self):
        """New opt-in chat ID must not change legacy photo uploads."""

        telegram = APP.Telegram(
            config(telegram_token="test-only", telegram_chat_id="-1009")
        )
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 77}}
        telegram.api_session.post = Mock(return_value=response)
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "card.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            telegram.send_file(
                "sendPhoto", "photo", image, "Preview", chat_id="7"
            )
            args = telegram.api_session.post.call_args.kwargs
            self.assertEqual(args["data"]["chat_id"], "7")
            self.assertEqual(args["data"]["disable_notification"], "true")
            telegram.send_photo(image, "Legacy")
            args = telegram.api_session.post.call_args.kwargs
            self.assertEqual(args["data"]["chat_id"], "-1009")

    def test_render_failure_never_falls_back_to_public_channel(self):
        """A rendering exception may only deliver private text feedback."""

        monitor = make_monitor("uk")
        monitor.telegram.send_file = Mock()
        with patch.object(APP, "render_card_png", side_effect=ValueError):
            monitor.handle_update(private_graphic_update(7))
        monitor.telegram.send_file.assert_not_called()
        monitor.telegram.send.assert_called_once()
        self.assertEqual(
            monitor.telegram.send.call_args.kwargs["chat_id"], "7"
        )
        monitor.telegram.edit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
