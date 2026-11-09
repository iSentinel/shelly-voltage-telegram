"""Standard Bot API channel fallback must preserve existing Shelly status."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_app import APP
from test_compact_preview import make_monitor
from test_yasno_schedule import response

import telegram_channel_yasno as CHANNEL
from yasno_cache import atomic_write, build_cache

KYIV = ZoneInfo("Europe/Kyiv")


def sealed_file(directory: str, age_s: int = 30) -> Path:
    """Create a source-labelled cache for today's YASNO schedule."""

    path = Path(directory) / "schedule.json"
    now = datetime.now(KYIV)
    atomic_write(
        str(path),
        build_cache(response(now), now - timedelta(seconds=age_s)),
    )
    return path


class TelegramChannelYasnoTests(unittest.TestCase):
    """Keep standard Telegram channel edits stable and noninteractive."""

    def test_feature_disabled_preserves_exact_status(self):
        """An unconfigured production feature must not affect the channel."""

        monitor = make_monitor("ru")
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_YASNO_STATUS_ENABLED": "0",
                    "YASNO_TRUSTED_CACHE_FILE": "/not/available",
                },
            ),
            patch.object(
                APP, "multiphase_status_text", return_value="EXISTING MESSAGE"
            ) as original,
            patch.object(APP, "append_yasno_forecast") as appended,
        ):
            actual = APP.channel_status_text(monitor)
        self.assertEqual(actual, "EXISTING MESSAGE")
        original.assert_called_once_with(monitor)
        appended.assert_not_called()

    def test_feature_disabled_if_no_explicit_one(self):
        """Only a literal 1 opt-in may change channel status formatting."""

        monitor = make_monitor("uk")
        for value in ("", "yes", "true", "enabled"):
            with (
                self.subTest(value=value),
                patch.dict(
                    os.environ,
                    {
                        "TELEGRAM_YASNO_STATUS_ENABLED": value,
                        "YASNO_TRUSTED_CACHE_FILE": "/fake/cache",
                    },
                ),
                patch.object(
                    APP, "multiphase_status_text", return_value="BASE"
                ),
            ):
                self.assertEqual(APP.channel_status_text(monitor), "BASE")

    def test_enabled_without_trusted_cache_stays_original(self):
        """Never fall back to a world-writable raw cache in a channel."""

        monitor = make_monitor("en")
        with (
            patch.dict(
                os.environ,
                {
                    "TELEGRAM_YASNO_STATUS_ENABLED": "1",
                    "YASNO_TRUSTED_CACHE_FILE": "",
                    "YASNO_CACHE_FILE": "/tmp/legacy-raw.json",
                },
            ),
            patch.object(APP, "multiphase_status_text", return_value="BASE"),
        ):
            self.assertEqual(APP.channel_status_text(monitor), "BASE")

    def test_real_sealed_cache_appends_localized_forecast(self):
        """Preserve status and append two distinct planned windows."""

        with tempfile.TemporaryDirectory() as directory:
            cache = sealed_file(directory)
            monitor = make_monitor("ru")
            with (
                patch.dict(
                    os.environ,
                    {
                        "TELEGRAM_YASNO_STATUS_ENABLED": "1",
                        "YASNO_TRUSTED_CACHE_FILE": str(cache),
                        "YASNO_GROUP": "10.1",
                    },
                ),
                patch.object(
                    APP,
                    "multiphase_status_text",
                    return_value="CURRENT SHELLY STATS",
                ),
                patch("yasno_cache.fetch_live") as fetch,
            ):
                result = APP.channel_status_text(monitor)
        self.assertTrue(result.startswith("CURRENT SHELLY STATS"))
        self.assertIn("YASNO", result)
        self.assertIn("ДТЕК Київ", result)
        self.assertIn("группа 10.1", result)
        self.assertIn("04:00–11:30", result)
        self.assertIn("14:30–22:00", result)
        self.assertIn("Прогноз, а не измерение", result)
        fetch.assert_not_called()

    def test_cache_stale_does_not_claim_scheduled_outage(self):
        """Expired forecast is replaced by an unavailable message."""

        with tempfile.TemporaryDirectory() as directory:
            cache = sealed_file(directory, age_s=7201)
            actual = CHANNEL.append_yasno_forecast(
                "SHELLY: ONLINE", str(cache), "10.1", "uk"
            )
        self.assertIn("SHELLY: ONLINE", actual)
        self.assertIn("Графік недоступний", actual)
        self.assertNotIn("04:00–11:30", actual)

    def test_corrupt_cache_hides_forecast_but_keeps_shelly(self):
        """Corrupted payload cannot replace physical measurements."""

        with tempfile.TemporaryDirectory() as directory:
            cache = sealed_file(directory)
            cache.write_text("invalid-json", encoding="utf-8")
            result = CHANNEL.append_yasno_forecast(
                "Shelly 220.1 V", str(cache), "10.1", "en"
            )
        self.assertIn("Shelly 220.1 V", result)
        self.assertIn("Schedule unavailable or expired", result)
        self.assertNotIn("04:00", result)

    def test_possible_outage_does_not_become_definite(self):
        """A possible range is not labelled a confirmed outage."""

        bars = "?" * 4 + "O" * 44
        text = CHANNEL.forecast_section("10.1", bars, "en")
        self.assertIn("Possible outages: 00:00–02:00", text)
        self.assertNotIn("Planned outages:", text)

    def test_no_planned_outage_does_not_claim_power_present(self):
        """An all-clear plan is not evidence of actual mains power."""

        text = CHANNEL.forecast_section("10.1", "O" * 48, "en")
        self.assertIn("No planned outages", text)
        self.assertIn("Forecast, not measured mains power", text)
        self.assertNotIn("Power is on", text)

    def test_many_ranges_truncated_to_telegram_safe_count(self):
        """Avoid an unbounded caption when the schedule alternates."""

        bars = "XO" * 24
        text = CHANNEL.forecast_section("10.1", bars, "en")
        self.assertIn("(+18)", text)
        self.assertLess(len(text), 500)

    def test_telegram_message_length_is_bounded(self):
        """Do not exceed Telegram message length when appending a forecast."""

        with tempfile.TemporaryDirectory() as directory:
            cache = sealed_file(directory)
            status = "🔌" * 2030
            result = CHANNEL.append_yasno_forecast(
                status, str(cache), "10.1", "ru"
            )
        self.assertEqual(result, status)

    def test_short_telegram_message_keeps_forecast(self):
        """Compact messages should still include their source and schedule."""

        with tempfile.TemporaryDirectory() as directory:
            cache = sealed_file(directory)
            result = CHANNEL.append_yasno_forecast(
                "Shelly online", str(cache), "10.1", "ru"
            )
        self.assertIn("YASNO", result)
        self.assertLessEqual(len(result.encode("utf-16-le")) // 2, 4096)

    def test_channel_status_edit_path_keeps_existing_message_logic(self):
        """Only the status text input may change; no new bot API method."""

        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "channel_status_text(self), key, self.telegram.status_chat_id",
            source,
        )
        self.assertIn("self.upsert_status_message(", source)
        self.assertNotIn("sendWidget(", source)


if __name__ == "__main__":
    unittest.main()
