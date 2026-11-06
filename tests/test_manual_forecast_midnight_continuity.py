"""Midnight forecast transitions cannot close an open Shelly loss."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

CHECK_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/manual-forecast-check.py"
)
SPEC = importlib.util.spec_from_file_location(
    "midnight_forecast_check", CHECK_PATH
)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)

DAY_6 = [["00:00", "05:30"], ["10:30", "18:00"], ["20:00", "24:00"]]
DAY_7 = [["00:00", "02:30"], ["07:00", "14:30"], ["17:30", "24:00"]]


def epoch(value: str) -> float:
    """Use an explicitly offset-aware Kyiv event time."""

    return datetime.fromisoformat(value).timestamp()


class MidnightShellyOutageTests(unittest.TestCase):
    """Keep plan boundaries separate from natural device connectivity."""

    def setUp(self):
        """Build two manual forecasts and one unfinished network gap."""

        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        folder = Path(workspace.name)
        self.database = folder / "shelly.sqlite3"
        self.files = {}
        self.write_forecasts(folder)
        self.create_measurements()

    def write_forecasts(self, folder: Path):
        """Preserve per-day source identity and unverified provenance."""

        for day, periods in (("2026-11-06", DAY_6), ("2026-11-07", DAY_7)):
            path = folder / f"{day}.json"
            path.write_text(
                json.dumps(
                    {
                        "date_kyiv": day,
                        "group": "10.1",
                        "source": "user-supplied forecast, unverified",
                        "source_verified": False,
                        "provider_verified": False,
                        "user_reported_windows": periods,
                    }
                )
            )
            self.files[day] = path

    def create_measurements(self):
        """Record one valid sample and a still-open incident 15."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "create table measurements "
                "(ts real, voltage real, frequency real)"
            )
            db.execute(
                "create table outages "
                "(id integer, started_at real, ended_at real)"
            )
            db.execute(
                "insert into measurements values (?,?,?)",
                (epoch("2026-11-06T20:30:25+02:00"), 234.8, 50.0),
            )
            db.execute(
                "insert into outages values (?,?,?)",
                (15, epoch("2026-11-06T20:31:07+02:00"), None),
            )

    def snapshot(self, now: str, day: str) -> dict:
        """Read an existing event without changing the stored power state."""

        forecast = CHECK.load_manual(self.files[day])
        stamp = datetime.fromisoformat(now)
        return CHECK.compare(forecast, CHECK.read_latest(self.database), stamp)

    def assert_unknown(self, report: dict):
        """An open connection gap cannot become a live voltage claim."""

        self.assertEqual(report["connectivity_gaps_open"], 1)
        self.assertEqual(report["measurement"], "unknown_or_stale")
        self.assertIsNone(report["voltage_v"])
        self.assertIsNone(report["frequency_hz"])
        self.assertFalse(report["actual_mains_outage_verified"])
        self.assertTrue(report["forecast_window_does_not_trigger_outage"])
        self.assertFalse(report["source_verified"])

    def test_end_of_november_6_is_still_planned_window(self):
        """23:59:59 uses day six's final tentative interval."""

        result = self.snapshot("2026-11-06T23:59:59+02:00", "2026-11-06")
        self.assertEqual(result["predicted_window"], ["20:00", "24:00"])
        self.assert_unknown(result)

    def test_midnight_day_six_has_exclusive_end(self):
        """At 00:00 the previous day's window is no longer active."""

        result = self.snapshot("2026-11-07T00:00:00+02:00", "2026-11-06")
        self.assertIsNone(result["predicted_window"])
        self.assert_unknown(result)

    def test_midnight_day_seven_starts_separate_window(self):
        """A new forecast window is not a real power-off or recovery event."""

        result = self.snapshot("2026-11-07T00:00:00+02:00", "2026-11-07")
        self.assertEqual(result["predicted_window"], ["00:00", "02:30"])
        self.assert_unknown(result)

    def test_first_second_after_midnight_never_restores_power(self):
        """Open event 15 survives the boundary without fresh voltage."""

        result = self.snapshot("2026-11-07T00:00:01+02:00", "2026-11-07")
        self.assertEqual(result["predicted_window"], ["00:00", "02:30"])
        self.assert_unknown(result)

    def test_first_window_ending_does_not_end_connection_gap(self):
        """02:30 is only a forecast edge, not network recovery."""

        prior = self.snapshot("2026-11-07T02:29:59+02:00", "2026-11-07")
        later = self.snapshot("2026-11-07T02:30:00+02:00", "2026-11-07")
        self.assertEqual(prior["predicted_window"], ["00:00", "02:30"])
        self.assertIsNone(later["predicted_window"])
        self.assert_unknown(prior)
        self.assert_unknown(later)

    def test_new_seven_am_window_does_not_create_outage(self):
        """Scheduled starts require independent hardware observation."""

        report = self.snapshot("2026-11-07T07:00:00+02:00", "2026-11-07")
        self.assertEqual(report["predicted_window"], ["07:00", "14:30"])
        self.assert_unknown(report)

    def test_end_of_day_seven_window_never_closes_connectivity(self):
        """17:30 window and midnight ending cannot alter the event row."""

        before = self.snapshot("2026-11-07T23:59:59+02:00", "2026-11-07")
        after = self.snapshot("2026-11-08T00:00:00+02:00", "2026-11-07")
        self.assertEqual(before["predicted_window"], ["17:30", "24:00"])
        self.assertIsNone(after["predicted_window"])
        self.assert_unknown(before)
        self.assert_unknown(after)

    def test_multiple_midnight_queries_are_strictly_read_only(self):
        """Forecast review never adds synthetic recovery or outage records."""

        original = self.database.read_bytes()
        for value, day in (
            ("2026-11-06T23:59:59+02:00", "2026-11-06"),
            ("2026-11-07T00:00:00+02:00", "2026-11-07"),
            ("2026-11-07T02:30:00+02:00", "2026-11-07"),
        ):
            self.assert_unknown(self.snapshot(value, day))
        self.assertEqual(original, self.database.read_bytes())
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute("select id,ended_at from outages").fetchall()
        self.assertEqual(rows, [(15, None)])

    def test_window_history_does_not_claim_continuous_supply(self):
        """No real samples after the network loss means no supply proof."""

        forecast = CHECK.load_manual(self.files["2026-11-07"])
        observed = datetime.fromisoformat("2026-11-07T00:10:00+02:00")
        readings = CHECK.observed_voltage_in_window(
            self.database, forecast, observed
        )
        self.assertEqual(readings["samples"], 0)
        self.assertIsNone(readings["voltage_min_v"])
        self.assertFalse(readings["proves_continuous_supply"])


if __name__ == "__main__":
    unittest.main()
