"""Validate the advisory replay without opening the live SQLite DB."""

from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from voltage_warning import WarningPolicy

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/warning-replay.py"
SPEC = importlib.util.spec_from_file_location("warning_replay", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class WarningReplayTests(unittest.TestCase):
    """Run the read-only tool against disposable historical fixtures."""

    def setUp(self):
        """Create an isolated, file-backed measurement database."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "measurements.db"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("create table measurements (ts real, voltage real)")
            db.executemany(
                "insert into measurements (ts, voltage) values (?, ?)",
                [(1000.0 + i * 10, 205.0) for i in range(25)]
                + [(1250.0 + i * 10, 211.0) for i in range(15)],
            )

    def run_cli(self, *options):
        """Execute the command-line entrypoint in an isolated process state."""

        argv = ["warning-replay", "--db", str(self.path), *options]
        output = io.StringIO()
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            exit_code = CLI.main()
        return exit_code, output.getvalue()

    def test_readonly_simulation_has_start_and_clear(self):
        """Replay events without writing the source database."""

        rows = CLI.read_samples(self.path, 1)
        report = CLI.replay(rows, WarningPolicy(enabled=True))
        self.assertEqual(report["warning_started"], 1)
        self.assertEqual(report["warning_cleared"], 1)
        self.assertFalse(report["warning_still_active"])

    def test_cli_json_output(self):
        """Expose a reproducible machine-readable outcome."""

        code, output = self.run_cli("--json")
        self.assertEqual(code, 0)
        result = json.loads(output)
        self.assertTrue(result["read_only"])
        self.assertEqual(result["samples"], 40)
        self.assertEqual(result["warning_started"], 1)
        self.assertEqual(result["warning_cleared"], 1)

    def test_cli_human_output(self):
        """Keep simple PASS counts legible for a human operator."""

        code, output = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("PASS", output)
        self.assertIn("Started: 1", output)
        self.assertIn("Cleared: 1", output)

    def test_reads_do_not_change_measurements(self):
        """Verify the source DB content is byte-for-byte unchanged."""

        before = self.path.read_bytes()
        self.run_cli("--json")
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_duration_is_rejected(self):
        """Deny a potentially unbounded historical replay window."""

        code, output = self.run_cli("--hours", "999", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "fail")

    def test_invalid_voltage_bounds_are_rejected(self):
        """Do not quietly accept inverted warning/recovery thresholds."""

        code, output = self.run_cli(
            "--below-v", "250", "--clear-v", "210", "--json"
        )
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "fail")

    def test_missing_db_fails_closed(self):
        """Never create a new DB when the requested path is absent."""

        self.path.unlink()
        code, output = self.run_cli("--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["reason"], "ValueError")
        self.assertFalse(self.path.exists())

    def test_symlink_database_is_rejected(self):
        """Avoid reading arbitrary files through user-supplied symlinks."""

        other = self.path.with_name("real.db")
        self.path.rename(other)
        self.path.symlink_to(other)
        code, output = self.run_cli("--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "fail")

    def test_sample_limit_fails_closed(self):
        """Too many rows must not create an oversized report."""

        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany(
                "insert into measurements (ts, voltage) values (?, ?)",
                [
                    (2000.0 + i * 0.01, 205.0)
                    for i in range(CLI.MAX_SAMPLES + 1)
                ],
            )
        with self.assertRaisesRegex(ValueError, "sample count"):
            CLI.read_samples(self.path, 1)

    def test_help_is_supported(self):
        """Every standalone operator tool must provide --help."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["warning-replay", "--help"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as stopped,
        ):
            CLI.parse_args()
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("--hours", output.getvalue())


if __name__ == "__main__":
    unittest.main()
