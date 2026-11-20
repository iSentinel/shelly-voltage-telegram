"""An expired official cancellation must not revive unofficial plans."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_app import APP, config
from test_qa_yasno_emergency_state import emergency_response
from test_yasno_schedule import response
from test_yasno_widget_preview import settings

from yasno_cache import atomic_write, build_cache

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 20, 15, tzinfo=KYIV)


class ExpiredEmergencyFallbackTests(unittest.TestCase):
    """Distinguish old official notices from stale ordinary plans."""

    def setUp(self):
        """Create a disposable sealed cache and three-phase test monitor."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "forecast.json"
        self.env = settings("")
        self.env["YASNO_TRUSTED_CACHE_FILE"] = str(self.path)
        self.monitor = APP.Monitor(
            config(shelly_meter_type="pro3em", telegram_language="en")
        )
        with patch.object(APP.time, "time", return_value=NOW.timestamp()):
            for index, phase in enumerate(("L1", "L2", "L3")):
                self.monitor.phase_states[phase].success(
                    221.0 + index, 50.0, 1.0
                )

    def save(self, data: dict, captured: datetime):
        """Build the same checksum envelope that the real handoff uses."""
        atomic_write(str(self.path), build_cache(data, captured))

    def preview(self, now: datetime = NOW):
        """Return actual caption/state and whether community was queried."""
        alternate = {
            "group": "10.1",
            "bars": "X" * 2 + "O" * 46,
            "day": now.date().isoformat(),
            "forecast_updated": now.timestamp() - 20,
        }
        with (
            patch.dict(os.environ, self.env),
            patch.object(APP.time, "time", return_value=now.timestamp()),
            patch.object(
                APP, "cached_community_preview_args", return_value=alternate
            ) as fallback,
        ):
            caption = APP.private_widget_preview_text(self.monitor)
            state = json.loads(APP.private_graphic_preview_state(self.monitor))
            return caption, state, fallback.called

    def test_expired_notice_blocks_community_not_telemetry(self):
        """No stale notice or unofficial bars on the same calendar day."""
        captured = NOW - timedelta(seconds=7201)
        self.save(emergency_response(captured), captured)
        caption, state, called = self.preview()
        self.assertFalse(called)
        self.assertNotIn("Emergency shutdowns", caption)
        self.assertNotIn("outage-data-ua", caption)
        self.assertNotIn("b", state)
        self.assertNotIn("n", state)
        self.assertEqual(state["v"], 221.0)
        self.assertEqual(state["p"]["L3"]["v"], 223.0)

    def test_recent_notice_still_visible(self):
        """A current notice is explicit rather than hidden."""
        captured = NOW - timedelta(seconds=7200)
        self.save(emergency_response(captured), captured)
        caption, state, called = self.preview()
        self.assertFalse(called)
        self.assertIn("Emergency shutdowns", caption)
        self.assertEqual(state["n"], "emergency")

    def test_later_verified_plan_restores_intervals(self):
        """A fresh plan replaces the cancellation without any fallback."""
        captured = NOW - timedelta(hours=3)
        self.save(emergency_response(captured), captured)
        self.save(response(NOW), NOW)
        caption, state, called = self.preview()
        self.assertFalse(called)
        self.assertIn("YASNO", caption)
        self.assertEqual(len(state["b"]), 48)

    def test_yesterday_notice_not_used_to_suppress_new_day(self):
        """The suppression has a Kyiv-day scope, not an indefinite latch."""
        captured = NOW - timedelta(days=1)
        self.save(emergency_response(captured), captured)
        caption, state, called = self.preview()
        self.assertTrue(called)
        self.assertIn("outage-data-ua", caption)
        self.assertNotIn("n", state)

    def test_expired_ordinary_plan_allows_labelled_fallback(self):
        """Do not change the existing fallback contract for ordinary plans."""
        captured = NOW - timedelta(seconds=7201)
        self.save(response(captured), captured)
        caption, _, called = self.preview()
        self.assertTrue(called)
        self.assertIn("outage-data-ua", caption)

    def test_corrupt_notice_is_not_a_verified_history(self):
        """A corrupted checksum is not trusted evidence of cancellation."""
        captured = NOW - timedelta(seconds=7201)
        envelope = build_cache(emergency_response(captured), captured)
        envelope["sha256"] = "0" * 64
        atomic_write(str(self.path), envelope)
        _, _, called = self.preview()
        self.assertTrue(called)


if __name__ == "__main__":
    unittest.main()
