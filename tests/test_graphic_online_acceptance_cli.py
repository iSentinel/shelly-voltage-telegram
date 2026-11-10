"""Validate source-only live voltage previews without Telegram requests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-online-acceptance.py"
)
NOW = datetime(2026, 11, 10, 16, 15, tzinfo=ZoneInfo("Europe/Kyiv"))


class OnlineAcceptanceCliTests(unittest.TestCase):
    """Fresh samples must remain distinct from outages and stale readings."""

    def setUp(self):
        """Create disposable health snapshots and PNG destination."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.health = folder / "health.json"
        self.forecast = folder / "yasno.json"
        self.output = folder / "status.png"
        self.write_health()

    def write_health(self, **overrides):
        """Generate a fresh online sample with historic outage values."""

        payload = {
            "connectivity": "online",
            "voltage": 217.7,
            "frequency": 50.00,
            "last_success": NOW.timestamp() - 5,
            "last_offline_voltage": 216.9,
            "private_token": "PRIVATE_SHOULD_NOT_LEAK",
        }
        payload.update(overrides)
        self.health.write_text(json.dumps(payload))

    def write_forecast(self, **overrides):
        """Supply a verified local YASNO 48-slot day for group 10.1."""

        payload = {
            "bars": "X" * 10 + "O" * 38,
            "day": NOW.date().isoformat(),
            "updated": NOW.timestamp() - 15,
            "source": "yasno",
        }
        payload.update(overrides)
        self.forecast.write_text(json.dumps(payload))

    def run_check(self, *extra):
        """Execute the local CLI with deterministic Kyiv timestamp."""

        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--health-file",
                str(self.health),
                "--output",
                str(self.output),
                "--now",
                str(NOW.timestamp()),
                "--jsonl",
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=25,
        )

    def test_fresh_voltage_and_frequency_render(self):
        """Only the latest successful reading appears in live state."""

        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["status"], "PASS")
        self.assertEqual(record["voltage_v"], 217.7)
        self.assertEqual(record["frequency_hz"], 50)
        self.assertTrue(record["live_voltage_visible"])
        self.assertFalse(record["forecast_visible"])
        self.assertFalse(record["telegram_sent"])
        self.assertEqual(record["image_size"], "960x560")
        self.assertTrue(
            self.output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        )
        self.assertNotIn("PRIVATE_SHOULD_NOT_LEAK", result.stdout)

    def test_yasno_forecast_cannot_replace_measured_voltage(self):
        """Schedule visibility does not alter a fresh voltage reading."""

        self.write_forecast()
        result = self.run_check("--forecast-file", str(self.forecast))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertTrue(record["forecast_visible"])
        self.assertEqual(record["voltage_v"], 217.7)

    def test_offline_is_rejected_without_png(self):
        """The online acceptance cannot misrepresent a power outage."""

        self.write_health(connectivity="offline")
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("NOT_ONLINE", result.stdout)
        self.assertFalse(self.output.exists())

    def test_stale_sample_is_rejected(self):
        """Old telemetry cannot be shown even if connectivity says online."""

        self.write_health(last_success=NOW.timestamp() - 120)
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO_FRESH_MEASUREMENT", result.stdout)

    def test_invalid_voltage_is_rejected(self):
        """A malformed voltage must never pass a live status check."""

        self.write_health(voltage=None)
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("NO_FRESH_MEASUREMENT", result.stdout)

    def test_repeated_identical_png_is_reused(self):
        """Rechecking a stable sample does not overwrite an output."""

        first = self.run_check()
        before = self.output.read_bytes()
        second = self.run_check()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(json.loads(second.stdout)["output_action"], "reused")
        self.assertEqual(self.output.read_bytes(), before)

    def test_different_existing_png_is_preserved(self):
        """Never overwrite an earlier acceptance without new output path."""

        self.assertEqual(self.run_check().returncode, 0)
        before = self.output.read_bytes()
        self.write_health(voltage=222.4)
        rejected = self.run_check()
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("OUTPUT_EXISTS_DIFFERENT", rejected.stdout)
        self.assertEqual(self.output.read_bytes(), before)

    def test_bad_forecast_does_not_create_image(self):
        """YASNO data of the wrong day is not fresh today's forecast."""

        self.write_forecast(day="2026-11-09")
        result = self.run_check("--forecast-file", str(self.forecast))
        self.assertEqual(result.returncode, 2)
        self.assertIn("STATE_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_linked_health_fixture_refused(self):
        """Do not follow symlinks to another user's private data."""

        source = self.health.with_name("secret.json")
        self.health.rename(source)
        self.health.symlink_to(source)
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

    def test_linked_output_refused(self):
        """An output symlink cannot redirect data to an existing file."""

        source = self.output.with_name("existing.txt")
        source.write_bytes(b"ORIGINAL")
        self.output.symlink_to(source)
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(source.read_bytes(), b"ORIGINAL")

    def test_malformed_json_refused(self):
        """Invalid input returns one safe JSONL line without traceback."""

        self.health.write_text("{not json")
        result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_invalid_clock_fails_closed(self):
        """Non-finite time cannot bypass the stale-sample cutoff."""

        result = self.run_check("--now", "nan")
        self.assertEqual(result.returncode, 2)
        self.assertIn("CLOCK_INVALID", result.stdout)

    def test_help_describes_explicit_inputs(self):
        """Provide -h/--help for operator use and CI."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--health-file", result.stdout)
        self.assertIn("--forecast-file", result.stdout)


if __name__ == "__main__":
    unittest.main()
