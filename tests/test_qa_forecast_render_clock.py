"""Forecast validation and widget serialization share one snapshot clock."""

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
from test_yasno_schedule import response
from test_yasno_widget_preview import settings

from telegram_channel_yasno import append_yasno_forecast
from telegram_widget_fallback import widget_fallback_text
from yasno_cache import atomic_write, build_cache

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 20, 12, tzinfo=KYIV)


class ForecastRenderClockTests(unittest.TestCase):
    """Keep independent live measurements visible at forecast boundaries."""

    def setUp(self):
        """Prepare an isolated three-phase meter and protected cache."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "forecast.json"
        self.monitor = APP.Monitor(config(shelly_meter_type="pro3em"))
        with patch.object(APP.time, "time", return_value=NOW.timestamp()):
            self.monitor.state.success(221.0, 50.0, 1.0)
            self.monitor.phase_states["L2"].success(222.0, 50.0, 1.0)
            self.monitor.phase_states["L3"].success(223.0, 50.0, 1.0)

    def cache(self, age: float, now: datetime = NOW):
        """Seal a provider response with the requested retrieval age."""
        atomic_write(
            str(self.path),
            build_cache(response(now), now - timedelta(seconds=age)),
        )

    def render(self, now: datetime = NOW):
        """Render with a fixed encoding clock, including real cache parsing."""
        env = settings("")
        env["YASNO_TRUSTED_CACHE_FILE"] = str(self.path)
        with (
            patch.dict(os.environ, env),
            patch.object(APP.time, "time", return_value=now.timestamp()),
        ):
            return json.loads(APP.private_graphic_preview_state(self.monitor))

    def test_exact_two_hour_boundary_is_consistent(self):
        """Both processors accept exactly the inclusive forecast limit."""
        self.cache(7200)
        state = self.render()
        self.assertEqual(state["src"], "yasno")
        self.assertEqual(state["v"], 221.0)
        self.assertEqual(state["p"]["L2"]["v"], 222.0)
        self.assertEqual(state["p"]["L3"]["v"], 223.0)

    def test_just_expired_forecast_does_not_hide_telemetry(self):
        """The graph and text drop expired intervals but keep voltage."""
        self.cache(7200.01)
        state = self.render()
        self.assertNotIn("b", state)
        self.assertEqual(state["v"], 221.0)
        self.assertEqual(state["p"]["L3"]["v"], 223.0)
        text = append_yasno_forecast(
            "Shelly 221.0 V", str(self.path), "10.1", "en", now=NOW
        )
        self.assertIn("Schedule unavailable or expired", text)
        self.assertIn("221.0 V", text)
        self.assertNotIn("04:00", text)

    def test_caption_rechecks_age_after_state_created(self):
        """A saved widget snapshot cannot revive an expired forecast."""
        self.cache(7200)
        state = self.render()
        output = widget_fallback_text(
            json.dumps(state), now=NOW.timestamp() + 1, language="en"
        )
        self.assertNotIn("YASNO", output)
        self.assertIn("221.0 V", output)

    def test_text_preview_at_boundary_uses_same_clock(self):
        """Private captions do not fail when the forecast is at expiry."""
        self.cache(7200)
        env = settings("")
        env["YASNO_TRUSTED_CACHE_FILE"] = str(self.path)
        with (
            patch.dict(os.environ, env),
            patch.object(APP.time, "time", return_value=NOW.timestamp()),
        ):
            text = APP.private_widget_preview_text(self.monitor)
        self.assertIn("YASNO", text)
        self.assertIn("221.0", text)

    def test_actual_png_rechecks_expired_forecast(self):
        """Both PNG styles hide stale timelines but still display live data."""
        self.cache(7200)
        state = self.render()
        for style in ("v4", "v5"):
            with self.subTest(style=style):
                target = self.path.parent / f"{style}.png"
                with patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": style}):
                    result = APP.render_card_png(
                        state, target, now=NOW.timestamp() + 1
                    )
                self.assertFalse(result["forecast_visible"])
                self.assertTrue(result["live_voltage_visible"])
                self.assertTrue(target.read_bytes().startswith(b"\x89PNG"))

    def test_kyiv_midnight_rejects_yesterdays_forecast(self):
        """A valid checksum does not authorize yesterday's schedule."""
        then = NOW.replace(hour=23, minute=59)
        self.cache(20, then)
        next_day = then + timedelta(minutes=1)
        state = self.render(next_day)
        self.assertNotIn("b", state)


if __name__ == "__main__":
    unittest.main()
