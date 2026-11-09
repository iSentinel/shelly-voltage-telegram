"""Private widget caption command must never affect channel delivery."""

from __future__ import annotations

import os
import time
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_app import APP
from test_compact_preview import make_monitor, private_update

KYIV = ZoneInfo("Europe/Kyiv")


class PrivateWidgetPreviewTests(unittest.TestCase):
    """Only authorized private admin calls may send widget fallback text."""

    def widget_update(self, sender: int, chat_type: str = "private") -> dict:
        """Construct one harmless preview command fixture."""

        update = private_update(sender, chat_type)
        update["message"]["text"] = "/widget_preview"
        return update

    def test_admin_receives_online_status_silently(self):
        """A private preview is sent without editing the public channel."""

        monitor = make_monitor("ru")
        monitor.state.success(224.1, 50.0, 10.0, 1)
        with patch.dict(
            os.environ,
            {
                "COMMUNITY_OUTAGE_CACHE_FILE": "",
                "COMMUNITY_OUTAGE_GROUP": "",
                "COMMUNITY_OUTAGE_REGION": "",
            },
        ):
            monitor.handle_update(self.widget_update(7))
        monitor.telegram.send.assert_called_once()
        call = monitor.telegram.send.call_args
        self.assertEqual(call.kwargs["chat_id"], "7")
        self.assertFalse(call.kwargs["notify"])
        self.assertIn("Напряжение: 224.1 В", call.args[0])
        monitor.telegram.edit.assert_not_called()
        monitor.telegram.pin.assert_not_called()

    def test_unknown_or_nonadmin_cannot_use_private_command(self):
        """Do not leak telemetry to a different Telegram user."""

        monitor = make_monitor("uk")
        monitor.handle_update(self.widget_update(8))
        monitor.telegram.send.assert_not_called()

    def test_channel_widget_command_stays_silent(self):
        """No private-only preview may trigger a public reply."""

        monitor = make_monitor("uk")
        monitor.handle_update(self.widget_update(7, "channel"))
        monitor.telegram.send.assert_not_called()
        monitor.telegram.edit.assert_not_called()

    def test_offline_caption_does_not_show_last_voltage(self):
        """Last-good voltage cannot become current when Shelly is offline."""

        monitor = make_monitor("en")
        monitor.state.restore(time.time() - 650, 225.9, 50.0)
        monitor.state.error("offline", 1)
        text = APP.private_widget_preview_text(monitor)
        self.assertIn("Disconnected", text)
        self.assertNotIn("225.9", text)

    def test_opt_in_cache_is_read_only_and_unofficial(self):
        """Only an explicitly configured community file may contribute."""

        monitor = make_monitor("uk")
        monitor.state.success(222.0, 50.0, 2.0, 1)
        today = datetime.now(KYIV).date().isoformat()
        fake = ("X" * 4) + ("O" * 44)
        with (
            patch.dict(
                os.environ,
                {
                    "COMMUNITY_OUTAGE_CACHE_FILE": "/tmp/fixture.json",
                    "COMMUNITY_OUTAGE_REGION": "kyiv",
                    "COMMUNITY_OUTAGE_GROUP": "10.1",
                },
            ),
            patch.object(APP, "read_snapshot", return_value={}) as reader,
            patch.object(
                APP,
                "slots_from_community",
                return_value=(fake, today, time.time() - 3),
            ) as parser,
        ):
            text = APP.private_widget_preview_text(monitor)
        reader.assert_called_once_with("/tmp/fixture.json")
        parser.assert_called_once()
        self.assertIn("група 10.1", text)
        self.assertIn("Неофіційне джерело", text)
        self.assertIn("Відключення 00:00–02:00", text)

    def test_corrupt_cache_falls_back_to_measurement(self):
        """Bad schedule must never hide valid local meter readings."""

        monitor = make_monitor("ru")
        monitor.state.success(223.1, 50.0, 3.0, 1)
        with (
            patch.dict(
                os.environ,
                {
                    "COMMUNITY_OUTAGE_CACHE_FILE": "/tmp/missing.json",
                    "COMMUNITY_OUTAGE_REGION": "kyiv",
                    "COMMUNITY_OUTAGE_GROUP": "10.1",
                },
            ),
            patch.object(APP, "read_snapshot", side_effect=ValueError),
        ):
            result = APP.private_widget_preview_text(monitor)
        self.assertIn("223.1", result)
        self.assertNotIn("Неофициальный источник", result)

    def test_all_languages_are_previewable(self):
        """The command reuses bot language, never a global hardcoded one."""

        for lang, label in (
            ("uk", "Напруга"),
            ("ru", "Напряжение"),
            ("en", "Voltage"),
        ):
            with self.subTest(language=lang):
                monitor = make_monitor(lang)
                monitor.state.success(226.2, 50.0, 3.0, 1)
                self.assertIn(label, APP.private_widget_preview_text(monitor))


if __name__ == "__main__":
    unittest.main()
