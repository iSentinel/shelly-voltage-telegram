"""Outage-safe channel/YASNO acceptance with no Telegram or Shelly traffic."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from test_app import APP, config
from test_yasno_schedule import response

import telegram_channel_yasno as CHANNEL
from yasno_cache import atomic_write, build_cache

KYIV = ZoneInfo("Europe/Kyiv")


class OfflineYasnoAcceptanceTests(unittest.TestCase):
    """Separate measured current power from YASNO planned outage forecasts."""

    def setUp(self) -> None:
        """Use only an isolated SQLite and mocked Telegram transport."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.monitor = APP.Monitor(
            config(
                database_path=self.folder / "offline.sqlite3",
                telegram_language="ru",
            )
        )
        self.monitor.storage.initialize()
        self.monitor.telegram.send = Mock()
        self.monitor.telegram.edit = Mock()
        self.monitor.telegram.pin = Mock()
        self.monitor.state.restore(time.time() - 8100, 229.3, 50.0)
        self.monitor.state.error("No route to host", offline_after=1)
        self.assertEqual(
            self.monitor.state.snapshot()["connectivity"], "offline"
        )

    def cache_file(
        self, *, age_seconds: int = 45, source: datetime | None = None
    ) -> str:
        """Write a sealed current-day test forecast into private QA storage."""

        current = source or datetime.now(KYIV)
        path = self.folder / "yasno.json"
        atomic_write(
            str(path),
            build_cache(
                response(current),
                current - timedelta(seconds=age_seconds),
            ),
        )
        return str(path)

    def render(self, language: str, cache: str, enabled: str = "1") -> str:
        """Call the real source status compositor with no network writes."""

        self.monitor.language = language
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_YASNO_STATUS_ENABLED": enabled,
                "YASNO_TRUSTED_CACHE_FILE": cache,
                "YASNO_GROUP": "10.1",
            },
        ):
            return APP.channel_status_text(self.monitor)

    def assert_no_stale_voltage(self, text: str) -> None:
        """Never claim that an hours-old sample is currently measured."""

        self.assertNotIn("229.3", text)
        self.assertNotIn("50.00", text)
        self.assertNotIn("220.0 В", text)

    def test_current_yasno_forecast_during_offline_is_only_a_plan(self):
        """Even valid YASNO windows cannot prove a physical outage."""

        result = self.render("ru", self.cache_file())
        self.assertIn(APP.tr("ru", "status_offline"), result)
        self.assertIn("YASNO", result)
        self.assertIn("группа 10.1", result)
        self.assertIn("Прогноз, а не измерение", result)
        self.assertIn("04:00–11:30", result)
        self.assert_no_stale_voltage(result)

    def test_ru_uk_en_keep_offline_and_forecast_labels(self):
        """All supported languages separate source forecast and last sample."""

        expected = {
            "ru": "Прогноз, а не измерение",
            "uk": "Прогноз, а не показання",
            "en": "Forecast, not measured",
        }
        cache = self.cache_file()
        for lang, label in expected.items():
            with self.subTest(language=lang):
                result = self.render(lang, cache)
                self.assertIn(APP.tr(lang, "status_offline"), result)
                self.assertIn(label, result)
                self.assert_no_stale_voltage(result)

    def test_stale_schedule_is_hidden_but_offline_survives(self):
        """Long offline duration must not make an old forecast current."""

        result = self.render("ru", self.cache_file(age_seconds=7201))
        self.assertIn(APP.tr("ru", "status_offline"), result)
        self.assertIn("График недоступен или устарел", result)
        self.assertNotIn("04:00–11:30", result)
        self.assert_no_stale_voltage(result)

    def test_corrupt_forecast_stays_fail_closed_and_offline(self):
        """Cache corruption cannot erase real offline state or invent power."""

        path = Path(self.cache_file())
        path.write_text("broken", encoding="utf-8")
        result = self.render("ru", str(path))
        self.assertIn(APP.tr("ru", "status_offline"), result)
        self.assertIn("График недоступен или устарел", result)
        self.assertNotIn("04:00–11:30", result)
        self.assert_no_stale_voltage(result)

    def test_disabled_feature_does_not_change_offline_status(self):
        """Absent opt-in must preserve existing channel status text exactly."""

        original = APP.multiphase_status_text(self.monitor)
        result = self.render("ru", self.cache_file(), enabled="0")
        self.assertEqual(result, original)
        self.assertNotIn("YASNO", result)

    def test_no_cache_does_not_change_existing_status(self):
        """Missing recipient mount is harmless when feature is disabled."""

        original = APP.multiphase_status_text(self.monitor)
        result = self.render("ru", str(self.folder / "missing"))
        self.assertIn(APP.tr("ru", "status_offline"), result)
        self.assertIn("График недоступен или устарел", result)
        self.assertIn(original, result)

    def test_yesterday_plan_is_not_today_after_midnight(self):
        """Near-midnight date rollover must reject yesterday's forecast."""

        previous = datetime(2026, 11, 8, 23, 45, tzinfo=KYIV)
        current = previous + timedelta(minutes=25)
        path = self.cache_file(age_seconds=10, source=previous)
        result = CHANNEL.append_yasno_forecast(
            "Shelly offline",
            path,
            "10.1",
            "ru",
            now=current.astimezone(timezone.utc),
        )
        self.assertIn("Shelly offline", result)
        self.assertIn("График недоступен или устарел", result)
        self.assertNotIn("04:00–11:30", result)

    def test_rendering_never_writes_telegram_or_sqlite(self):
        """Building the message is side-effect-free during a power outage."""

        self.monitor.storage.set_meta = Mock(
            side_effect=AssertionError("unexpected metadata write")
        )
        self.monitor.storage.add = Mock(
            side_effect=AssertionError("unexpected measurement write")
        )
        _ = self.render("ru", self.cache_file())
        self.monitor.storage.set_meta.assert_not_called()
        self.monitor.storage.add.assert_not_called()
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
