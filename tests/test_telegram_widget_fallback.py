"""Check that Telegram widget fallback text cannot claim stale power."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram_widget_data import widget_public_data
from telegram_widget_fallback import widget_fallback_text

NOW = datetime(2026, 11, 8, 1, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()


class FallbackCaptionTests(unittest.TestCase):
    """Render safe text when clients cannot display the widget."""

    def test_fresh_meter_value_in_all_languages(self):
        """Use the configured bot locale and real meter values."""

        health = {
            "connectivity": "online",
            "last_success": NOW - 5,
            "voltage": 225.3,
            "frequency": 50,
        }
        for language, expected in (
            ("uk", "Напруга"),
            ("ru", "Напряжение"),
            ("en", "Voltage"),
        ):
            with self.subTest(language=language):
                payload = widget_public_data(
                    health, now=NOW, language=language
                )
                text = widget_fallback_text(payload, now=NOW)
                self.assertIn(expected, text)
                self.assertIn("225.3", text)
                self.assertIn("50.0", text)

    def test_english_caption_uses_latin_units(self):
        """English labels must not inherit Cyrillic unit abbreviations."""

        data = widget_public_data(
            {
                "connectivity": "online",
                "voltage": 223.3,
                "frequency": 50.0,
                "last_success": NOW - 1,
            },
            language="en",
            now=NOW,
        )
        text = widget_fallback_text(data, now=NOW)
        self.assertIn("223.3 V", text)
        self.assertIn("50.0 Hz", text)
        self.assertNotIn("Гц", text)

    def test_offline_does_not_display_historic_voltage(self):
        """Prior measurements do not imply the power state while offline."""

        data = {
            "schema": 1,
            "s": "offline",
            "l": "ru",
            "v": 225.3,
            "f": 50.0,
            "t": NOW - 2,
        }
        caption = widget_fallback_text(json.dumps(data), now=NOW)
        self.assertIn("Нет связи", caption)
        self.assertNotIn("225.3", caption)

    def test_measured_value_ages_out(self):
        """Previously good live data expires after sixty seconds."""

        health = {
            "connectivity": "online",
            "last_success": NOW - 5,
            "voltage": 225.3,
        }
        data = widget_public_data(health, now=NOW, language="ru")
        caption = widget_fallback_text(data, now=NOW + 65)
        self.assertIn("Данные устарели", caption)
        self.assertNotIn("225.3", caption)

    def test_zero_and_tiny_online_voltage_is_not_displayed(self):
        """Never announce a zero as measured live voltage in any locale."""

        for language in ("uk", "ru", "en"):
            for voltage in (0, 0.0, 0.04):
                with self.subTest(language=language, voltage=voltage):
                    state = {
                        "schema": 1,
                        "s": "online",
                        "l": language,
                        "v": voltage,
                        "f": 50.0,
                        "t": NOW - 1,
                    }
                    caption = widget_fallback_text(json.dumps(state), now=NOW)
                    self.assertNotIn("0.0 V", caption)
                    self.assertNotIn("0.0 В", caption)
                    self.assertNotIn("50.0", caption)
        live = {
            "schema": 1,
            "s": "online",
            "l": "uk",
            "v": 176.2,
            "f": 50.0,
            "t": NOW - 1,
        }
        caption = widget_fallback_text(json.dumps(live), now=NOW)
        self.assertIn("176.2", caption)

    def test_zero_secondary_phase_is_hidden(self):
        """A synthetic L2 zero must not be presented as a live phase."""

        data = {
            "schema": 1,
            "s": "offline",
            "l": "uk",
            "p": {
                "L2": {"s": "online", "v": 0.0, "t": NOW - 1},
                "L3": {"s": "online", "v": 176.2, "t": NOW - 1},
            },
        }
        caption = widget_fallback_text(json.dumps(data), now=NOW)
        self.assertIn("L2: —", caption)
        self.assertIn("L3: 176.2 В", caption)

    def test_invalid_voltage_and_frequency_rejected(self):
        """Accept only numeric, finite readings with sensible ranges."""

        for voltage in (True, -1, 450, "230"):
            with self.subTest(voltage=voltage):
                state = {
                    "schema": 1,
                    "s": "online",
                    "l": "uk",
                    "v": voltage,
                    "f": float("nan"),
                    "t": NOW - 1,
                }
                caption = widget_fallback_text(json.dumps(state), now=NOW)
                self.assertNotIn("Гц", caption)
                self.assertNotIn("Напруга:", caption)

    def test_forecast_labels_definite_and_possible_separately(self):
        """A community plan never becomes a measured outage alert."""

        bars = "XX??" + ("O" * 44)
        payload = widget_public_data(
            {"connectivity": "offline"},
            group="10.1",
            bars=bars,
            day="2026-11-08",
            forecast_updated=NOW - 10,
            now=NOW,
            language="uk",
        )
        caption = widget_fallback_text(payload, now=NOW)
        self.assertIn("Відключення 00:00–01:00", caption)
        self.assertIn("Можливо 01:00–02:00", caption)
        self.assertIn("Неофіційне", caption)
        self.assertIn("Немає зв'язку", caption)

    def test_old_forecast_hidden_after_cache_ttl(self):
        """Ignore a schedule if it was not refreshed for two hours."""

        payload = widget_public_data(
            {},
            group="10.1",
            bars="X" * 48,
            day="2026-11-08",
            forecast_updated=NOW - 10,
            now=NOW,
        )
        caption = widget_fallback_text(payload, now=NOW + 7201)
        self.assertNotIn("Прогноз", caption)
        self.assertNotIn("00:00", caption)

    def test_wrong_day_and_schema_hidden(self):
        """Reject source date mismatches and unknown state envelopes."""

        payload = widget_public_data(
            {},
            group="10.1",
            bars="X" * 48,
            day="2026-11-08",
            forecast_updated=NOW - 10,
            now=NOW,
        )
        self.assertNotIn(
            "Прогноз", widget_fallback_text(payload, now=NOW + 86400)
        )
        with self.assertRaisesRegex(ValueError, "schema"):
            widget_fallback_text('{"schema":2}', now=NOW)

    def test_forecast_caption_stays_under_limit(self):
        """Bound readable text even on highly fragmented source data."""

        bars = "XO?O" * 12
        payload = widget_public_data(
            {},
            group="10.1",
            bars=bars,
            day="2026-11-08",
            forecast_updated=NOW - 10,
            now=NOW,
        )
        self.assertLessEqual(
            len(widget_fallback_text(payload, now=NOW)),
            1024,
        )


if __name__ == "__main__":
    unittest.main()
