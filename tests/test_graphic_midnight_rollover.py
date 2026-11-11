"""Verify Shelly/YASNO PNG safety across Kyiv midnight and DST."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram_card_png import render_card_png, valid_forecast
from telegram_widget_data import widget_public_data
from telegram_widget_fallback import widget_fallback_text

KYIV = ZoneInfo("Europe/Kyiv")
HISTORIC_VOLTAGE = 193.8


def kyiv_time(year: int, month: int, day: int, hour: int, minute=0):
    """Return an exact local timestamp, preserving timezone transitions."""

    return datetime(year, month, day, hour, minute, tzinfo=KYIV).timestamp()


def offline_state(clock: float, day: str, bars: str = "X" * 48) -> dict:
    """Encode a forecast and an unreachable real meter without live data."""

    payload = widget_public_data(
        {
            "connectivity": "offline",
            "voltage": HISTORIC_VOLTAGE,
            "frequency": 50.0,
            "last_success": clock - 10800,
        },
        group="10.1",
        bars=bars,
        day=day,
        forecast_updated=clock - 10,
        forecast_source="yasno",
        now=clock,
        language="uk",
    )
    return json.loads(payload)


class GraphicMidnightRolloverTests(unittest.TestCase):
    """Never show yesterday's schedule or historic voltage as live."""

    def setUp(self):
        """Use only disposable PNG paths, never bot state or Telegram."""

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def render(self, state: dict, clock: float, name: str) -> dict:
        """Render a strictly source-only PNG with an explicit clock."""

        return render_card_png(state, self.folder / name, now=clock)

    def test_last_second_forecast_expires_at_exact_midnight(self):
        """The trusted old-day forecast must disappear at 00:00:00."""

        before = kyiv_time(2026, 11, 10, 23, 59) + 59
        midnight = kyiv_time(2026, 11, 11, 0)
        self.assertEqual(midnight - before, 1)
        state = offline_state(before, "2026-11-10", "X" * 36 + "O" * 12)
        self.assertTrue(valid_forecast(state, before))
        self.assertFalse(valid_forecast(state, midnight))
        accepted = self.render(state, before, "before.png")
        expired = self.render(state, midnight, "after.png")
        self.assertTrue(accepted["forecast_visible"])
        self.assertEqual(accepted["forecast_off_slots"], 36)
        self.assertFalse(expired["forecast_visible"])
        self.assertEqual(expired["forecast_off_slots"], 0)
        self.assertFalse(accepted["live_voltage_visible"])
        self.assertFalse(expired["live_voltage_visible"])

    def test_online_voltage_survives_old_forecast_midnight_expiry(self):
        """Keep fresh physical voltage as yesterday's forecast disappears."""

        before = kyiv_time(2026, 11, 11, 23, 59) + 59
        after = kyiv_time(2026, 11, 12, 0) + 1
        payload = widget_public_data(
            {
                "connectivity": "online",
                "voltage": 213.8,
                "frequency": 50.0,
                "last_success": before - 1,
            },
            group="10.1",
            bars="X" * 48,
            day="2026-11-11",
            forecast_updated=before - 60,
            forecast_source="yasno",
            now=before,
            language="uk",
        )
        state = json.loads(payload)
        old = self.render(state, before, "old-online.png")
        new = self.render(state, after, "new-online.png")
        caption = widget_fallback_text(payload, now=after)
        self.assertEqual(old["status"], "online")
        self.assertTrue(old["forecast_visible"])
        self.assertEqual(new["status"], "online")
        self.assertTrue(new["live_voltage_visible"])
        self.assertFalse(new["forecast_visible"])
        self.assertEqual(new["forecast_off_slots"], 0)
        self.assertIn("213.8", caption)
        self.assertNotIn("Прогноз", caption)

    def test_online_meter_without_new_day_forecast_keeps_voltage(self):
        """Absent tomorrow's YASNO data is not evidence of power loss."""

        next_day = kyiv_time(2026, 11, 12, 0) + 1
        payload = widget_public_data(
            {
                "connectivity": "online",
                "voltage": 214.5,
                "frequency": 50.0,
                "last_success": next_day - 2,
            },
            group="10.1",
            bars=None,
            now=next_day,
            language="uk",
        )
        state = json.loads(payload)
        card = self.render(state, next_day, "online-no-schedule.png")
        caption = widget_fallback_text(payload, now=next_day)
        self.assertEqual(card["status"], "online")
        self.assertTrue(card["live_voltage_visible"])
        self.assertFalse(card["forecast_visible"])
        self.assertNotIn("b", state)
        self.assertIn("214.5", caption)
        self.assertNotIn("Прогноз", caption)

    def test_new_day_fresh_forecast_works_with_shelly_offline(self):
        """A genuinely current forecast can appear with unknown mains."""

        after = kyiv_time(2026, 11, 11, 0) + 1
        state = offline_state(after, "2026-11-11", "O" * 48)
        result = self.render(state, after, "new-day.png")
        self.assertEqual(result["status"], "offline")
        self.assertTrue(result["forecast_visible"])
        self.assertEqual(result["forecast_off_slots"], 0)
        self.assertFalse(result["live_voltage_visible"])
        self.assertTrue({"v", "f", "t"}.isdisjoint(state))

    def test_old_day_cannot_be_relabelled_by_new_timestamp(self):
        """A fresh clock does not authorize a previous day's schedule."""

        after = kyiv_time(2026, 11, 11, 0) + 1
        with self.assertRaisesRegex(ValueError, "forecast day"):
            offline_state(after, "2026-11-10")

    def test_fall_back_dst_day_has_25_hour_forecast_expiry(self):
        """A 25-hour Kyiv calendar day expires at local midnight."""

        start = kyiv_time(2026, 10, 25, 0)
        before = kyiv_time(2026, 10, 25, 23, 59) + 59
        midnight = kyiv_time(2026, 10, 26, 0)
        self.assertEqual(midnight - start, 25 * 3600)
        state = offline_state(before, "2026-10-25")
        self.assertEqual(state["e"], int(midnight))
        self.assertTrue(valid_forecast(state, before))
        self.assertFalse(valid_forecast(state, midnight))
        self.assertFalse(
            self.render(state, midnight, "dst.png")["forecast_visible"]
        )

    def test_spring_forward_dst_day_has_23_hour_expiry(self):
        """YASNO expires at local midnight on a 23-hour Kyiv day."""

        start = kyiv_time(2026, 3, 29, 0)
        before = kyiv_time(2026, 3, 29, 23, 59) + 59
        midnight = kyiv_time(2026, 3, 30, 0)
        self.assertEqual(midnight - start, 23 * 3600)
        state = offline_state(before, "2026-03-29")
        self.assertEqual(state["e"], int(midnight))
        self.assertTrue(valid_forecast(state, before))
        self.assertFalse(valid_forecast(state, midnight))
        self.assertFalse(
            self.render(state, midnight, "spring.png")["forecast_visible"]
        )

    def test_fallback_text_and_png_agree_across_midnight(self):
        """Text clients and PNG cards must discard the old YASNO day."""

        before = kyiv_time(2026, 11, 10, 23, 59) + 59
        midnight = kyiv_time(2026, 11, 11, 0)
        state = offline_state(before, "2026-11-10")
        encoded = json.dumps(state, ensure_ascii=False)
        before_text = widget_fallback_text(encoded, now=before)
        after_text = widget_fallback_text(encoded, now=midnight)
        before_png = self.render(state, before, "text-before.png")
        after_png = self.render(state, midnight, "text-after.png")
        self.assertTrue(before_png["forecast_visible"])
        self.assertIn("Прогноз", before_text)
        self.assertFalse(after_png["forecast_visible"])
        self.assertNotIn("Прогноз", after_text)
        self.assertNotIn("193.8", before_text)
        self.assertNotIn("193.8", after_text)
        self.assertFalse(before_png["live_voltage_visible"])
        self.assertFalse(after_png["live_voltage_visible"])

    def test_reused_old_state_never_revives_historic_voltage(self):
        """Even a post-midnight offline retry cannot reveal 193.8 V."""

        before = kyiv_time(2026, 11, 10, 23, 59) + 59
        after = kyiv_time(2026, 11, 11, 0) + 60
        state = offline_state(before, "2026-11-10")
        for clock, name in ((before, "old.png"), (after, "expired.png")):
            with self.subTest(clock=clock):
                result = self.render(state, clock, name)
                self.assertEqual(result["status"], "offline")
                self.assertFalse(result["live_voltage_visible"])


if __name__ == "__main__":
    unittest.main()
