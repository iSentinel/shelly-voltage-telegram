"""Daily Telegram digests use one Kyiv-day observed time interval."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from test_app import APP, config

from telegram_i18n import tr


def seconds(value: str) -> float:
    """Parse deterministic Kyiv timestamps, not the current server clock."""

    return datetime.fromisoformat(value).timestamp()


class CalendarDayDailySummaryTests(unittest.TestCase):
    """Exclude yesterday and future measurements from today's digest."""

    def setUp(self):
        """Build an isolated monitor with timestamps around midnight."""

        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.now = seconds("2026-11-07T00:10:00+02:00")
        self.previous = seconds("2026-11-06T23:59:50+02:00")
        self.today = seconds("2026-11-07T00:02:00+02:00")
        self.future = seconds("2026-11-07T18:30:00+02:00")
        self.monitor = APP.Monitor(
            config(database_path=self.root / "mon.sqlite3")
        )
        self.monitor.storage.initialize()

    def summary(self):
        """Render without any actual Telegram send or Shelly request."""

        with patch.object(APP.time, "time", return_value=self.now):
            return self.monitor.daily_summary_text()

    def test_yesterday_not_included_in_day_seven_voltage(self):
        """A rolling 24-hour average would wrongly include 250 V."""

        self.monitor.storage.add(self.previous, 250.0, 50.0)
        self.monitor.storage.add(self.today, 225.0, 50.0)
        text = self.summary()
        self.assertIn("225.0", text)
        self.assertNotIn("250.0", text)
        self.assertNotIn("237.5", text)

    def test_future_dated_sample_not_included(self):
        """An erroneous timestamp beyond observation is not evidence."""

        self.monitor.storage.add(self.today, 225.0, 50.0)
        self.monitor.storage.add(self.future, 260.0, 50.0)
        text = self.summary()
        self.assertIn("225.0", text)
        self.assertNotIn("260.0", text)
        self.assertNotIn("242.5", text)

    def test_open_gap_crossing_midnight_is_todays_elapsed_time(self):
        """A natural loss remains one event with just today's elapsed."""

        self.assertTrue(
            self.monitor.storage.begin_outage(
                seconds("2026-11-06T20:31:07+02:00")
            )
        )
        result = self.summary()
        self.assertIn("потерь связи: 1", result)
        self.assertIn("10м 0с", result)
        self.assertEqual(self.monitor.storage.outage_total(), 1)

    def test_no_samples_never_invents_zero_voltage(self):
        """A network-only loss cannot synthesize a voltage value."""

        self.monitor.storage.begin_outage(self.previous)
        rendered = self.summary()
        self.assertNotIn("0.0–0.0 V", rendered)
        self.assertIn("неизвестно", rendered)
        self.assertEqual(self.monitor.storage.outage_total(), 1)

    def test_localized_disclaimer_has_same_observation_semantics(self):
        """All 3 language versions explain observed vs current readings."""

        expected = {
            "ru": ("измерения", "неизвестно"),
            "uk": ("вимірювання", "невідома"),
            "en": ("Observed samples", "unknown"),
        }
        for language, words in expected.items():
            with self.subTest(language=language):
                text = tr(language, "summary_observation_notice")
                self.assertTrue(all(word in text for word in words))

    def test_phase_stats_optional_upper_bound_preserves_old_callers(self):
        """The existing no-upper-bound phase helper remains compatible."""

        self.monitor.storage.add(self.today, 225.0, 50.0)
        self.monitor.storage.add(self.future, 260.0, 50.0)
        path = self.monitor.cfg.database_path
        old = APP.phase_stats(path, "L1", self.today)
        bounded = APP.phase_stats(path, "L1", self.today, until=self.now)
        self.assertEqual(old["count"], 2)
        self.assertEqual(bounded["count"], 1)
        self.assertEqual(bounded["avg"], 225.0)


if __name__ == "__main__":
    unittest.main()
