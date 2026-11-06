"""Read-only acceptance checks for a user-provided planned outage."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/manual-forecast-check.py"
)
SPEC = importlib.util.spec_from_file_location("manual_check", SCRIPT)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)

WINDOWS = [
    ["00:00", "05:30"],
    ["10:30", "18:00"],
    ["20:00", "24:00"],
]


def timestamp(clock: str) -> float:
    """Build an offset-aware ISO clock for the fixture date."""

    return datetime.fromisoformat(f"2026-11-06T{clock}+02:00").timestamp()


class ManualForecastCheckerTests(unittest.TestCase):
    """Verify forecasts never become fictional mains measurements."""

    def setUp(self):
        """Create temporary read-only input snapshots and SQLite fixtures."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        self.forecast = folder / "forecast.json"
        self.database = folder / "shelly.sqlite3"
        self.forecast.write_text(
            json.dumps(
                {
                    "date_kyiv": "2026-11-06",
                    "group": "10.1",
                    "source": "user-supplied forecast, unverified",
                    "source_verified": False,
                    "user_reported_windows": WINDOWS,
                }
            )
        )
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "create table measurements "
                "(ts real, voltage real, frequency real)"
            )
            db.execute("create table outages (started_at real, ended_at real)")
            db.execute(
                "insert into measurements values (?,?,?)",
                (timestamp("19:59:58"), 227.8, 50.0),
            )

    def compare(self, clock: str):
        """Apply a simulated observation clock without any network access."""

        forecast = CHECK.load_manual(self.forecast)
        reading = CHECK.read_latest(self.database)
        observed = datetime.fromisoformat(f"2026-11-06T{clock}+02:00")
        return CHECK.compare(forecast, reading, observed)

    def test_before_20_no_scheduled_window(self):
        """A 20:00 forecast cannot begin before 20:00."""

        result = self.compare("19:59:59")
        self.assertIsNone(result["predicted_window"])
        self.assertEqual(result["measurement"], "recent_valid_voltage")
        self.assertEqual(result["voltage_v"], 227.8)

    def test_exactly_20_scheduled_window_starts(self):
        """Mark the planned interval, not a verified mains failure."""

        result = self.compare("20:00:00")
        self.assertEqual(result["predicted_window"], ["20:00", "24:00"])
        self.assertFalse(result["actual_mains_outage_verified"])
        self.assertFalse(result["source_verified"])

    def test_after_20_stale_voltage_hidden(self):
        """Never display the old reading as current when Shelly is absent."""

        result = self.compare("20:05:00")
        self.assertEqual(result["predicted_window"], ["20:00", "24:00"])
        self.assertEqual(result["measurement"], "unknown_or_stale")
        self.assertIsNone(result["voltage_v"])

    def test_open_gap_is_only_connectivity_evidence(self):
        """Open outage rows do not prove utility power loss."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into outages values (?,NULL)",
                (timestamp("20:01:00"),),
            )
        result = self.compare("20:06:00")
        self.assertEqual(result["connectivity_gaps_open"], 1)
        self.assertFalse(result["actual_mains_outage_verified"])
        self.assertIsNone(result["voltage_v"])

    def test_open_gap_hides_even_very_recent_measurement(self):
        """Connection loss makes voltage unknown regardless of sample age."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into outages values (?,NULL)",
                (timestamp("20:00:02"),),
            )
        result = self.compare("20:00:05")
        self.assertEqual(result["predicted_window"], ["20:00", "24:00"])
        self.assertEqual(result["measurement"], "unknown_or_stale")
        self.assertIsNone(result["voltage_v"])
        self.assertIsNone(result["sample_age_sec"])
        self.assertEqual(result["connectivity_gaps_open"], 1)

    def test_non_object_json_is_rejected(self):
        """Malformed manual cache objects fail with an actionable error."""

        self.forecast.write_text("[]")
        with self.assertRaisesRegex(ValueError, "object"):
            CHECK.load_manual(self.forecast)

    def test_provider_verified_flag_rejected(self):
        """Manual data must not masquerade as official DTEK information."""

        data = json.loads(self.forecast.read_text())
        data["source_verified"] = True
        self.forecast.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "verification"):
            CHECK.load_manual(self.forecast)

    def test_wrong_provenance_rejected(self):
        """Reject a cached provider object in the manual forecast reader."""

        data = json.loads(self.forecast.read_text())
        data["source"] = "verified_dtek"
        self.forecast.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "source"):
            CHECK.load_manual(self.forecast)

    def test_other_day_has_no_active_window(self):
        """The forecast must not recur on a later day."""

        forecast = CHECK.load_manual(self.forecast)
        observed = datetime.fromisoformat("2026-11-07T20:01:00+02:00")
        self.assertIsNone(CHECK.current_window(forecast, observed))

    def test_window_boundary_midnight(self):
        """The 24:00 endpoint belongs to the next day and is exclusive."""

        forecast = CHECK.load_manual(self.forecast)
        observed = datetime.fromisoformat("2026-11-07T00:00:00+02:00")
        self.assertIsNone(CHECK.current_window(forecast, observed))

    def test_cli_no_sqlite_writes(self):
        """A successful CLI invocation must leave DB bytes unchanged."""

        original = self.database.read_bytes()
        args = [
            "manual-forecast-check",
            "--forecast",
            str(self.forecast),
            "--db",
            str(self.database),
            "--at",
            "2026-11-06T20:00:00+02:00",
            "--json",
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(output):
            exit_code = CHECK.main()
        self.assertEqual(exit_code, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["device_controls_or_telegram_writes"])
        self.assertEqual(original, self.database.read_bytes())

    def test_bad_time_format_fails_closed(self):
        """A naive timestamp must not silently inherit the host TZ."""

        args = [
            "manual-forecast-check",
            "--forecast",
            str(self.forecast),
            "--db",
            str(self.database),
            "--at",
            "2026-11-06T20:00:00",
            "--json",
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(output):
            self.assertEqual(CHECK.main(), 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "fail")

    def test_localized_summary_distinguishes_forecast_and_voltage(self):
        """Expose unverified graph and actual voltage as separate evidence."""

        report = self.compare("20:00:05")
        messages = {
            "uk": ("Неперевірений", "не звірено з ДТЕК"),
            "ru": ("Непроверенный", "не сверено с ДТЭК"),
            "en": ("Unverified", "not cross-checked with DTEK"),
        }
        for lang, words in messages.items():
            with self.subTest(lang=lang):
                text = CHECK.operator_summary(report, lang)
                self.assertIn("20:00–24:00", text)
                self.assertIn("227.8 V", text)
                self.assertIn(words[0], text)
                self.assertIn(words[1], text)

    def test_forecast_boundaries_are_not_switching_commands(self):
        """Scheduled times cannot assert or trigger a mains outage."""

        for clock in ("19:59:59", "20:00:00", "20:01:00"):
            with self.subTest(clock=clock):
                result = self.compare(clock)
                self.assertTrue(result["forecast_is_approximate"])
                self.assertTrue(
                    result["forecast_window_does_not_trigger_outage"]
                )
                self.assertFalse(result["actual_mains_outage_verified"])
                self.assertFalse(result["device_controls_or_telegram_writes"])

    def test_summary_marks_planned_times_as_approximate(self):
        """No locale should imply exact DTEK power switching times."""

        report = self.compare("20:00:05")
        expected = {
            "uk": ("Орієнтовне", "Час може змінитися"),
            "ru": ("Ориентировочный", "Время может измениться"),
            "en": ("Approximate", "Times may shift"),
        }
        for language, (title, caution) in expected.items():
            with self.subTest(language=language):
                summary = CHECK.operator_summary(report, language)
                self.assertIn(title, summary)
                self.assertIn(caution, summary)

    def test_outside_forecast_not_equivalent_to_power_available(self):
        """A missed planned window does not guarantee mains availability."""

        report = self.compare("19:59:59")
        report["measurement"] = "unknown_or_stale"
        report["voltage_v"] = None
        for language in ("uk", "ru", "en"):
            with self.subTest(language=language):
                summary = CHECK.operator_summary(report, language)
                self.assertNotIn("227.8 V", summary)
                self.assertNotIn("electricity is on", summary.lower())

    def test_localized_summary_hides_voltage_when_offline(self):
        """A fresh pre-gap reading must never look like current voltage."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into outages values (?,NULL)",
                (timestamp("20:00:02"),),
            )
        report = self.compare("20:00:05")
        for lang, phrase in (
            ("uk", "невідома"),
            ("ru", "неизвестно"),
            ("en", "unknown"),
        ):
            with self.subTest(lang=lang):
                text = CHECK.operator_summary(report, lang)
                self.assertIn(phrase, text)
                self.assertNotIn("227.8", text)

    def test_operator_summary_fails_closed_on_fake_source(self):
        """Never show a forged official source as an operator report."""

        report = self.compare("20:00:05")
        report["source_verified"] = True
        with self.assertRaises(ValueError):
            CHECK.operator_summary(report, "uk")

    def test_cli_text_uses_explicit_unverified_label(self):
        """Human CLI output always distinguishes measured and predicted."""

        args = [
            "manual-forecast-check",
            "--forecast",
            str(self.forecast),
            "--db",
            str(self.database),
            "--at",
            "2026-11-06T20:00:05+02:00",
            "--language",
            "ru",
        ]
        output = io.StringIO()
        with patch.object(sys, "argv", args), redirect_stdout(output):
            self.assertEqual(CHECK.main(), 0)
        self.assertIn("Непроверенный прогноз", output.getvalue())
        self.assertIn("227.8 V", output.getvalue())

    def test_only_observed_voltage_counts_in_planned_window(self):
        """Observed samples prove presence at points, not continuous supply."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.executemany(
                "insert into measurements values (?,?,?)",
                [
                    (timestamp("20:00:06"), 229.1, 50.0),
                    (timestamp("20:00:16"), 231.4, 50.0),
                    (timestamp("20:00:26"), 0.0, 50.0),
                ],
            )
        forecast = CHECK.load_manual(self.forecast)
        observed = datetime.fromisoformat("2026-11-06T20:00:30+02:00")
        result = CHECK.observed_voltage_in_window(
            self.database, forecast, observed
        )
        self.assertEqual(result["samples"], 2)
        self.assertEqual(result["voltage_min_v"], 229.1)
        self.assertEqual(result["voltage_max_v"], 231.4)
        self.assertFalse(result["proves_continuous_supply"])

    def test_no_samples_cannot_prove_mains_failure(self):
        """An active forecast interval does not imply power loss."""

        forecast = CHECK.load_manual(self.forecast)
        observed = datetime.fromisoformat("2026-11-06T20:00:30+02:00")
        report = CHECK.observed_voltage_in_window(
            self.database, forecast, observed
        )
        self.assertEqual(report["samples"], 0)
        self.assertIsNone(report["voltage_min_v"])
        self.assertFalse(report["proves_continuous_supply"])

    def test_observations_outside_forecast_not_attributed(self):
        """Never attribute historical measurements to another forecast day."""

        forecast = CHECK.load_manual(self.forecast)
        observed = datetime.fromisoformat("2026-11-07T20:01:00+02:00")
        report = CHECK.observed_voltage_in_window(
            self.database, forecast, observed
        )
        self.assertEqual(report["samples"], 0)
        self.assertIsNone(report["voltage_max_v"])

    def test_history_report_never_overstates_continuous_supply(self):
        """The human report distinguishes observations from an uptime claim."""

        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "insert into measurements values (?,?,?)",
                (timestamp("20:00:05"), 230.0, 50.0),
            )
        forecast = CHECK.load_manual(self.forecast)
        observed = datetime.fromisoformat("2026-11-06T20:00:10+02:00")
        result = CHECK.compare(
            forecast, CHECK.read_latest(self.database), observed
        )
        result["observed_in_planned_window"] = (
            CHECK.observed_voltage_in_window(self.database, forecast, observed)
        )
        summary = CHECK.operator_summary(result, "en")
        self.assertIn("1 Shelly samples", summary)
        self.assertIn("not proof of continuous supply", summary)

    def test_help_is_available(self):
        """Make standalone CLI options discoverable with --help."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["manual-forecast-check", "--help"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as outcome,
        ):
            CHECK.parse_args()
        self.assertEqual(outcome.exception.code, 0)
        self.assertIn("--forecast", output.getvalue())


if __name__ == "__main__":
    unittest.main()
