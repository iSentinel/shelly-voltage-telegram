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
