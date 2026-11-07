"""Networkless acceptance for the preview-only Telegram widget state."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from test_outage_data_ua import fake_snapshot

import telegram_widget_data as WIDGET

NOW = datetime(2026, 11, 8, 1, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()


class WidgetStateTests(unittest.TestCase):
    """A widget must not turn historic or forecast data into live voltage."""

    def test_online_measurement_is_visible(self):
        """Fresh, valid physical readings retain the measured units."""

        health = {
            "connectivity": "online",
            "voltage": 218.3,
            "frequency": 50,
            "last_success": NOW - 5,
        }
        result = json.loads(WIDGET.widget_public_data(health, now=NOW))
        self.assertEqual(result["s"], "online")
        self.assertEqual(result["v"], 218.3)
        self.assertEqual(result["f"], 50.0)

    def test_offline_meter_hides_cached_voltage(self):
        """A last known value is not a live reading during lost contact."""

        health = {
            "connectivity": "offline",
            "voltage": 218,
            "last_success": NOW - 20,
        }
        result = json.loads(WIDGET.widget_public_data(health, now=NOW))
        self.assertEqual(result, {"schema": 1, "s": "offline"})
        self.assertNotIn("v", result)

    def test_old_sample_hides_voltage(self):
        """A seemingly connected meter with old telemetry is stale."""

        health = {
            "connectivity": "online",
            "voltage": 218,
            "last_success": NOW - 70,
        }
        data = json.loads(WIDGET.widget_public_data(health, now=NOW))
        self.assertEqual(data["s"], "stale")
        self.assertNotIn("v", data)

    def test_unknown_and_boolean_inputs_fail_closed(self):
        """Do not accept booleans, NaN or missing samples as measurements."""

        for voltage in (True, float("nan"), None):
            with self.subTest(voltage=voltage):
                data = json.loads(
                    WIDGET.widget_public_data(
                        {
                            "connectivity": "online",
                            "voltage": voltage,
                            "last_success": NOW,
                        },
                        now=NOW,
                    )
                )
                self.assertEqual(data["s"], "unknown")
                self.assertNotIn("v", data)

    def test_valid_community_slots_are_separate(self):
        """Forecasts show provenance and never mutate meter readings."""

        bars, day, source_time = WIDGET.slots_from_community(
            fake_snapshot({"1": "no", "2": "first"}),
            "kyiv",
            "10.1",
            datetime.fromtimestamp(NOW, ZoneInfo("Europe/Kyiv")),
        )
        self.assertEqual(bars[:4], "XXXO")
        data = json.loads(
            WIDGET.widget_public_data(
                {"connectivity": "offline"},
                group="10.1",
                bars=bars,
                day=day,
                forecast_updated=source_time,
                now=NOW,
            )
        )
        self.assertEqual(data["s"], "offline")
        self.assertEqual(data["src"], "community")
        self.assertNotIn("v", data)
        self.assertLessEqual(len(json.dumps(data).encode()), 1024)

    def test_forecast_invalid_day_size_or_unknown_state(self):
        """Reject untrustworthy schedule input without hiding the problem."""

        good = "O" * 48
        invalid = [
            ("O" * 47, "2026-11-08", NOW - 30),
            ("#" + "O" * 47, "2026-11-08", NOW - 30),
            (good, "2026-11-07", NOW - 30),
            (good, "2026-11-08", NOW - 7201),
        ]
        for bars, day, updated in invalid:
            with (
                self.subTest(day=day, updated=updated, bars=bars[:3]),
                self.assertRaises(ValueError),
            ):
                WIDGET.widget_public_data(
                    {},
                    bars=bars,
                    day=day,
                    forecast_updated=updated,
                    now=NOW,
                )

    def test_group_is_validated(self):
        """Protect source and layout from unexpectedly large group strings."""

        with self.assertRaisesRegex(ValueError, "group"):
            WIDGET.widget_public_data({}, group="<tag>", now=NOW)


if __name__ == "__main__":
    unittest.main()
