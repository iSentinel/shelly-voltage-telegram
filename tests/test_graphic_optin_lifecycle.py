"""Source-only opt-in and status lifecycle tests; Telegram is fully mocked."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from test_app import APP

METHODS = (
    "status_message_key",
    "status_media_kind",
    "record_status_media_kind",
    "graphic_channel_opted_in",
    "send_graphic_candidate",
    "publish_graphic_status",
    "_publish_graphic_status_locked",
    "update_graphic_status",
    "_update_graphic_status_locked",
    "edit_existing_status_media",
    "replace_graphic_voltage_status",
    "edit_voltage_status",
    "upsert_status_message",
    "upsert_text_status_message",
    "legacy_media_guard",
    "rotate_status_after_recovery",
    "rotate_graphic_status_after_recovery",
    "rotate_legacy_photo_after_recovery",
    "replace_voltage_status",
    "maybe_rotate_stable_recovery_status",
    "maybe_finalize_voltage_episode",
)


class GraphicOptinLifecycleTests(unittest.TestCase):
    """Exercise isolated handlers without real credentials or channel posts."""

    def setUp(self):
        """Use private temporary media and fake in-memory SQLite metadata."""

        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.card = Path(self.folder.name) / "status.png"
        self.card.write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"test")

    def make_monitor(self, initial: dict | None = None) -> tuple[object, dict]:
        """Wire existing Monitor methods to a fake storage and Telegram API."""

        state = dict(initial or {})
        storage = Mock()
        storage.get_meta.side_effect = state.get
        storage.set_meta.side_effect = state.__setitem__
        storage.delete_meta.side_effect = lambda key: state.pop(key, None)
        telegram = Mock()
        telegram.enabled = True
        telegram.status_chat_id = "-100123"
        telegram.send_status_photo.return_value = 555
        telegram.send.return_value = 555
        monitor = SimpleNamespace(
            cfg=SimpleNamespace(telegram_status_chat_id="-100123"),
            status_publish_lock=threading.RLock(),
            storage=storage,
            telegram=telegram,
            graphic_status_media=Mock(return_value=(self.card, "Caption")),
            delete_previous_voltage_status=Mock(),
            ensure_status_pinned=Mock(),
            finish_voltage_status_update=Mock(),
            voltage_status_edit_window_active=Mock(return_value=False),
            stable_recovery_status_due=Mock(return_value=True),
            clear_stable_recovery_context=Mock(),
            status_edit_window_min=30,
        )
        for name in METHODS:
            setattr(
                monitor, name, MethodType(getattr(APP.Monitor, name), monitor)
            )
        return monitor, state

    def test_graphics_requires_two_explicit_flags_and_real_channel(self):
        """Neither accidental env values nor private-only bots can activate."""

        monitor, _ = self.make_monitor()
        for enabled, approved in (
            ("0", "0"),
            ("1", "0"),
            ("0", "1"),
            ("true", "1"),
            ("1", "yes"),
        ):
            with (
                self.subTest(enabled=enabled, approved=approved),
                patch.dict(
                    os.environ,
                    {
                        "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": enabled,
                        "TELEGRAM_GRAPHIC_OWNER_APPROVED": approved,
                    },
                ),
            ):
                self.assertFalse(monitor.graphic_channel_opted_in())
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "1",
                "TELEGRAM_GRAPHIC_OWNER_APPROVED": "1",
            },
        ):
            self.assertTrue(monitor.graphic_channel_opted_in())
            monitor.cfg.telegram_status_chat_id = ""
            self.assertFalse(monitor.graphic_channel_opted_in())

    def test_plain_text_route_is_unchanged_when_graphics_disabled(self):
        """Production defaults always retain the legacy status writer."""

        monitor, _ = self.make_monitor()
        monitor.upsert_text_status_message = Mock(return_value=True)
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "0",
                "TELEGRAM_GRAPHIC_OWNER_APPROVED": "0",
            },
        ):
            self.assertTrue(
                monitor.upsert_status_message(
                    "Grid", "telegram_status_message_id", "-100123"
                )
            )
        monitor.upsert_text_status_message.assert_called_once()
        monitor.telegram.send_status_photo.assert_not_called()

    def test_opted_in_new_status_sends_only_one_photo_and_persists_id(self):
        """Successful photo send stores destination, format, and pin once."""

        monitor, state = self.make_monitor()
        self.assertTrue(monitor.publish_graphic_status("Grid status"))
        monitor.telegram.send_status_photo.assert_called_once_with(
            self.card, "Caption", "-100123", notify=False
        )
        self.assertEqual(state["telegram_status_message_id"], "555")
        self.assertEqual(
            state["telegram_status_media_identity"], "photo:-100123:555"
        )
        self.assertNotIn("telegram_graphic_publish_pending", state)
        monitor.ensure_status_pinned.assert_called_once_with(555)

    def test_unknown_existing_status_blocks_rotation_without_posts(self):
        """Never duplicate a historic text post without verified format."""

        monitor, state = self.make_monitor(
            {"telegram_status_message_id": "101"}
        )
        self.assertFalse(monitor.publish_graphic_status("Grid status"))
        monitor.telegram.send_status_photo.assert_not_called()
        monitor.telegram.send.assert_not_called()
        self.assertNotIn("telegram_graphic_publish_pending", state)

    def test_empty_pending_marker_blocks_new_status_publication(self):
        """Malformed existing marker must not create a duplicate message."""

        monitor, state = self.make_monitor(
            {"telegram_graphic_publish_pending": ""}
        )
        self.assertFalse(monitor.publish_graphic_status("Grid"))
        monitor.telegram.send_status_photo.assert_not_called()
        monitor.telegram.send.assert_not_called()
        self.assertIn("telegram_graphic_publish_pending", state)

    def test_empty_pending_marker_blocks_status_refresh(self):
        """No graphics edit or new post while reconciliation is unresolved."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
                "telegram_graphic_publish_pending": "",
            }
        )
        self.assertFalse(monitor.update_graphic_status("Grid"))
        monitor.telegram.edit_photo.assert_not_called()
        monitor.telegram.send_status_photo.assert_not_called()
        self.assertIn("telegram_graphic_publish_pending", state)

    def test_empty_pending_marker_blocks_legacy_rollback(self):
        """Disabling graphics cannot bypass even a malformed marker."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
                "telegram_graphic_publish_pending": "",
            }
        )
        self.assertFalse(monitor.legacy_media_guard("Offline"))
        monitor.telegram.edit_photo_caption.assert_not_called()
        monitor.telegram.send.assert_not_called()
        self.assertIn("telegram_graphic_publish_pending", state)

    def test_timeout_persists_uncertain_marker_and_blocks_retry(self):
        """Unacknowledged Telegram send must not generate a second post."""

        monitor, state = self.make_monitor()
        monitor.telegram.send_status_photo.side_effect = TimeoutError("down")
        self.assertFalse(monitor.publish_graphic_status("Grid"))
        self.assertEqual(
            state["telegram_graphic_publish_pending"], "-100123:new"
        )
        self.assertFalse(monitor.publish_graphic_status("Grid"))
        monitor.telegram.send_status_photo.assert_called_once()
        monitor.ensure_status_pinned.assert_not_called()

    def test_existing_photo_fallback_edits_caption_without_new_send(self):
        """Render failure must keep outage changes without duplicates."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        monitor.graphic_status_media.return_value = (None, "Shelly offline")
        self.assertTrue(monitor.update_graphic_status("Shelly offline"))
        monitor.telegram.edit_photo_caption.assert_called_once_with(
            101, "Shelly offline", "-100123"
        )
        monitor.telegram.send.assert_not_called()
        self.assertEqual(state["telegram_status_message_id"], "101")

    def test_existing_text_converts_in_place_then_remembers_photo_kind(self):
        """Switch to photo by editing the old message, never republishing."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "text:-100123:101",
            }
        )
        self.assertTrue(monitor.update_graphic_status("Grid"))
        monitor.telegram.edit_photo.assert_called_once_with(
            101, self.card, "Caption", "-100123"
        )
        monitor.telegram.send_status_photo.assert_not_called()
        self.assertEqual(
            state["telegram_status_media_identity"], "photo:-100123:101"
        )

    def test_concurrent_refreshes_cannot_publish_two_status_posts(self):
        """Two simultaneous new-status updates share one message identity."""

        monitor, state = self.make_monitor()

        def slow_send(*args, **kwargs):
            time.sleep(0.04)
            return 555

        monitor.telegram.send_status_photo.side_effect = slow_send
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(
                workers.map(
                    lambda _: monitor.update_graphic_status("Grid"), range(2)
                )
            )
        self.assertEqual(results, [True, True])
        monitor.telegram.send_status_photo.assert_called_once()
        monitor.telegram.edit_photo.assert_called_once()
        self.assertEqual(state["telegram_status_message_id"], "555")

    def test_rollback_photo_uses_caption_not_text_or_new_post(self):
        """Disabling graphics must preserve the existing channel message."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "0",
                "TELEGRAM_GRAPHIC_OWNER_APPROVED": "0",
            },
        ):
            self.assertTrue(
                monitor.upsert_status_message(
                    "Offline", "telegram_status_message_id", "-100123"
                )
            )
        monitor.telegram.edit_photo_caption.assert_called_once_with(
            101, "Offline", "-100123"
        )
        monitor.telegram.send.assert_not_called()
        self.assertEqual(state["telegram_status_message_id"], "101")

    def test_rollback_unknown_media_refuses_any_public_send(self):
        """Uncertain photo conversion cannot publish a duplicate status."""

        monitor, _ = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "unknown:-100123:101",
            }
        )
        self.assertFalse(monitor.legacy_media_guard("Offline"))
        monitor.telegram.send.assert_not_called()
        monitor.telegram.edit.assert_not_called()

    def test_pending_publish_blocks_rollback_text_updates(self):
        """A timed-out photo send blocks writes even if graphics is off."""

        monitor, _ = self.make_monitor(
            {"telegram_graphic_publish_pending": "-100123:new"}
        )
        self.assertFalse(monitor.legacy_media_guard("Offline"))
        monitor.telegram.send.assert_not_called()

    def test_unmarked_legacy_message_keeps_plain_text_route(self):
        """The currently deployed text message has no media marker."""

        monitor, _ = self.make_monitor({"telegram_status_message_id": "101"})
        self.assertIsNone(monitor.legacy_media_guard("Text"))

    def test_voltage_alert_publishes_once_and_keeps_episode_metadata(self):
        """New opt-in voltage alert carries the existing audible policy."""

        monitor, state = self.make_monitor()
        with patch.object(APP, "multiphase_status_text", return_value="Low V"):
            result = monitor.replace_graphic_voltage_status(True, 1000.0)
        self.assertTrue(result)
        monitor.telegram.send_status_photo.assert_called_once_with(
            self.card, "Caption", "-100123", notify=True
        )
        self.assertEqual(state["last_voltage_notification_at"], "1000.0")
        self.assertEqual(
            state["voltage_status_edit_window_started_at"], "1000.0"
        )
        monitor.finish_voltage_status_update.assert_called_once()

    def test_voltage_alert_in_window_edits_photo_without_duplicate(self):
        """Repeated transitions reuse the stable Telegram photo ID."""

        monitor, _ = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        monitor.voltage_status_edit_window_active.return_value = True
        monitor.graphic_channel_opted_in = Mock(return_value=True)
        with patch.object(APP, "multiphase_status_text", return_value="Low V"):
            self.assertTrue(
                monitor.replace_graphic_voltage_status(True, 1000.0)
            )
        monitor.telegram.edit_photo.assert_called_once()
        monitor.telegram.send_status_photo.assert_not_called()
        monitor.finish_voltage_status_update.assert_called_once()

    def test_recovery_uses_silent_photo_and_clears_pending_event(self):
        """Recovered service preserves publish-first replacement behavior."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
                APP.RECOVERY_ROTATION_PENDING_KEY: "1000",
            }
        )
        monitor.graphic_channel_opted_in = Mock(return_value=True)
        with patch.object(
            APP, "multiphase_status_text", return_value="Online"
        ):
            monitor.rotate_status_after_recovery(20.0)
        monitor.telegram.send_status_photo.assert_called_once_with(
            self.card, "Caption", "-100123", notify=False
        )
        self.assertNotIn(APP.RECOVERY_ROTATION_PENDING_KEY, state)
        monitor.delete_previous_voltage_status.assert_called_once_with(
            "101", 555
        )
        monitor.finish_voltage_status_update.assert_called_once()

    def test_ambiguous_recovery_never_creates_second_message(self):
        """Timeout preserves recovery work while blocking duplicate publish."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        monitor.graphic_channel_opted_in = Mock(return_value=True)
        monitor.telegram.send_status_photo.side_effect = TimeoutError("down")
        with patch.object(
            APP, "multiphase_status_text", return_value="Online"
        ):
            monitor.rotate_status_after_recovery(20.0)
            monitor.rotate_status_after_recovery(20.0)
        self.assertEqual(monitor.telegram.send_status_photo.call_count, 1)
        self.assertIn(APP.RECOVERY_ROTATION_PENDING_KEY, state)

    def test_stable_recovery_updates_without_audible_notification(self):
        """A completed episode silently publishes and clears event context."""

        monitor, _ = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        monitor.graphic_channel_opted_in = Mock(return_value=True)
        with patch.object(APP, "multiphase_status_text", return_value="Now"):
            monitor.maybe_rotate_stable_recovery_status()
        monitor.telegram.send_status_photo.assert_called_once_with(
            self.card, "Caption", "-100123", notify=False
        )
        monitor.clear_stable_recovery_context.assert_called_once()

    def test_finished_episode_uses_graphic_update_instead_of_text_edit(self):
        """Finalized voltage episode keeps the existing photo identity."""

        monitor, state = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
                "voltage_episode": json.dumps({"start_at": 1.0}),
            }
        )
        monitor.graphic_channel_opted_in = Mock(return_value=True)
        with patch.object(APP, "multiphase_status_text", return_value="Final"):
            monitor.maybe_finalize_voltage_episode()
        monitor.telegram.edit.assert_not_called()
        monitor.telegram.edit_photo.assert_called_once()
        self.assertIn("finalized_at", json.loads(state["voltage_episode"]))

    def test_finished_episode_after_rollback_edits_caption_only(self):
        """A rollback does not call editMessageText on the old photo."""

        monitor, _ = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
                "voltage_episode": json.dumps({"start_at": 1.0}),
            }
        )
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "0",
                    "TELEGRAM_GRAPHIC_OWNER_APPROVED": "0",
                },
            ),
            patch.object(APP, "multiphase_status_text", return_value="Final"),
        ):
            monitor.maybe_finalize_voltage_episode()
        monitor.telegram.edit_photo_caption.assert_called_once()
        monitor.telegram.edit.assert_not_called()

    def test_stable_recovery_rollback_keeps_single_photo(self):
        """Return to text-only mode without publishing a duplicate post."""

        monitor, _ = self.make_monitor(
            {
                "telegram_status_message_id": "101",
                "telegram_status_media_identity": "photo:-100123:101",
            }
        )
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "0",
                    "TELEGRAM_GRAPHIC_OWNER_APPROVED": "0",
                },
            ),
            patch.object(APP, "multiphase_status_text", return_value="Now"),
        ):
            monitor.maybe_rotate_stable_recovery_status()
        monitor.telegram.edit_photo_caption.assert_called_once()
        monitor.telegram.send.assert_not_called()
        monitor.clear_stable_recovery_context.assert_called_once()

    def test_opted_in_upsert_checks_exact_destination(self):
        """A bad target cannot silently redirect a graphical status."""

        monitor, _ = self.make_monitor()
        monitor.update_graphic_status = Mock(return_value=True)
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_GRAPHIC_CHANNEL_ENABLED": "1",
                "TELEGRAM_GRAPHIC_OWNER_APPROVED": "1",
            },
        ):
            self.assertFalse(
                monitor.upsert_status_message(
                    "Grid", "telegram_message_id", "-100123"
                )
            )
            self.assertTrue(
                monitor.upsert_status_message(
                    "Grid", "telegram_status_message_id", "-100123"
                )
            )
        monitor.update_graphic_status.assert_called_once_with("Grid")


if __name__ == "__main__":
    unittest.main()
