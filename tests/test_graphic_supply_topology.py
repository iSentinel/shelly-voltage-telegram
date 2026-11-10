"""Keep offline Shelly causality accurate for a pre-ZUBR installation."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from telegram_card_png import FONT, OFFLINE_HINT, label, render_card_png
from telegram_widget_data import widget_public_data

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 10, 18, 5, tzinfo=KYIV).timestamp()
BARS = "O" * 35 + "X" * 13


def state(
    connectivity: str, sample_age: int = 5, bars: str | None = BARS
) -> dict:
    """Build actual-versus-forecast state for a pre-protection meter."""

    health = {
        "connectivity": connectivity,
        "last_success": NOW - sample_age,
        "voltage": 193.8,
        "frequency": 50.0,
    }
    extra = {"now": NOW, "group": "10.1", "language": "uk"}
    if bars is not None:
        extra.update(
            bars=bars,
            day="2026-11-10",
            forecast_source="yasno",
            forecast_updated=NOW - 30,
        )
    return json.loads(widget_public_data(health, **extra))


class ShellySupplyTopologyTests(unittest.TestCase):
    """A Shelly ahead of ZUBR loses connectivity, not measured voltage."""

    def setUp(self):
        """Render only inside disposable test directories."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.png = Path(folder.name) / "status.png"

    def render(self, payload: dict) -> dict:
        """Create a deterministic image for a given source state."""

        report = render_card_png(payload, self.png, now=NOW)
        self.assertTrue(self.png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        return report

    def test_offline_hint_names_power_or_shelly_network_failure(self):
        """No disconnected meter can identify why communication ended."""

        self.assertIn("ЙМОВІРНЕ ЗНЕСТРУМЛЕННЯ", OFFLINE_HINT)
        self.assertIn("ЗБІЙ SHELLY ЧИ МЕРЕЖІ", OFFLINE_HINT)
        self.assertNotIn("ПІДТВЕРДЖЕНО", OFFLINE_HINT)
        self.assertNotIn("ZUBR", OFFLINE_HINT)

    def test_ukrainian_hint_has_glyphs_and_fits_footer(self):
        """The full source label stays inside the 960-pixel PNG."""

        note = "ЛИШЕ ПРОГНОЗ / " + OFFLINE_HINT
        self.assertFalse(set(note) - set(FONT))
        self.assertLessEqual(60 + len(note) * 6 * 2, 932)

    def test_offline_forecast_keeps_possible_cause_visible(self):
        """Offline must show no voltage even if the schedule is red."""

        payload = state("offline")
        with mock.patch("telegram_card_png.label", wraps=label) as painter:
            result = self.render(payload)
        captions = [call.args[1] for call in painter.call_args_list]
        self.assertEqual(result["status"], "offline")
        self.assertFalse(result["live_voltage_visible"])
        self.assertTrue(result["forecast_visible"])
        self.assertIn("ЛИШЕ ПРОГНОЗ / " + OFFLINE_HINT, captions)
        self.assertTrue({"v", "f", "t"}.isdisjoint(payload))

    def test_no_forecast_still_explains_unreachable_meter(self):
        """Missing YASNO must not hide the probable cause disclaimer."""

        with mock.patch("telegram_card_png.label", wraps=label) as painter:
            result = self.render(state("offline", bars=None))
        texts = [call.args[1] for call in painter.call_args_list]
        self.assertIn(OFFLINE_HINT, texts)
        self.assertFalse(result["forecast_visible"])
        self.assertFalse(result["live_voltage_visible"])

    def test_online_low_voltage_does_not_imply_zubr_cut(self):
        """Fresh low voltage remains measured, not a planned outage."""

        payload = state("online")
        report = self.render(payload)
        self.assertEqual(payload["v"], 193.8)
        self.assertEqual(report["status"], "online")
        self.assertTrue(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])

    def test_online_stale_sample_cannot_restore_grid(self):
        """Wi-Fi recovery alone is not a fresh mains measurement."""

        payload = state("online", sample_age=125)
        report = self.render(payload)
        self.assertEqual(report["status"], "stale")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])


if __name__ == "__main__":
    unittest.main()
