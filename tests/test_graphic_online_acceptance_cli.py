"""Validate source-only live voltage previews without Telegram requests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

import yasno_cache as Y

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
        self.cache = folder / "verified-cache.json"
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

    def write_three_phase(self, **overrides):
        """Supply the exact three-phase structure returned by /health."""

        phase1 = {
            "connectivity": "online",
            "voltage": 217.7,
            "frequency": 50.0,
            "last_success": NOW.timestamp() - 5,
        }
        primary = {
            **phase1,
            "line_labels": ["L1", "L2", "L3"],
            "phases": {
                "L1": dict(phase1),
                "L2": {
                    "connectivity": "online",
                    "voltage": 213.8,
                    "frequency": 50.0,
                    "last_success": NOW.timestamp() - 5,
                },
                "L3": {
                    "connectivity": "online",
                    "voltage": 202.3,
                    "frequency": 50.0,
                    "last_success": NOW.timestamp() - 5,
                },
            },
        }
        primary.update(overrides)
        self.health.write_text(json.dumps(primary), encoding="utf-8")

    def run_v5(self):
        """Force explicit tri-phase renderer only in this QA subprocess."""

        with patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": "v5"}):
            return self.run_check()

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

    def write_verified(
        self,
        retrieved_age: int = 20,
        published_age_hours: int = 16,
        corrupt: bool = False,
    ):
        """Make an authentic-schema signed-source checksum QA envelope."""

        payload = response(NOW)
        published = NOW - timedelta(hours=published_age_hours)
        payload["10.1"]["updatedOn"] = published.isoformat()
        fetched = NOW - timedelta(seconds=retrieved_age)
        envelope = Y.build_cache(payload, fetched)
        if corrupt:
            envelope["sha256"] = "0" * 64
        Y.atomic_write(str(self.cache), envelope)

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
        self.assertEqual(record["image_size"], "960x800")
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

    def test_three_phase_v5_preview_verifies_all_live_tiles(self):
        """The acceptance cannot pass with an L1-only screenshot."""

        self.write_three_phase()
        result = self.run_v5()
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["verified_phases"], ["L1", "L2", "L3"])
        self.assertEqual(data["voltage_v"], 217.7)
        self.assertEqual(data["status"], "PASS")
        self.assertFalse(data["telegram_sent"])
        self.assertEqual(data["image_size"], "960x800")
        self.assertTrue(self.output.read_bytes().startswith(b"\x89PNG"))

    def test_three_phase_secondary_stale_fails_before_png(self):
        """A disconnected or stale L2 must never appear fresh."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        data["phases"]["L2"]["last_success"] = NOW.timestamp() - 75
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_NOT_FRESH", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_secondary_offline_fails_before_png(self):
        """A cached L3 voltage cannot pass when phase is offline."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        data["phases"]["L3"]["connectivity"] = "offline"
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_NOT_FRESH", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_missing_frequency_fails_before_png(self):
        """A secondary tile needs a complete measured sample."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        del data["phases"]["L2"]["frequency"]
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_NOT_FRESH", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_primary_frequency_mismatch_fails_before_png(self):
        """Top-level frequency must agree with the L1 sample."""

        self.write_three_phase(frequency=49.8)
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_PRIMARY_MISMATCH", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_missing_l3_fails_before_png(self):
        """Never show a normal-looking single-phase fallback."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        del data["phases"]["L3"]
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_TOPOLOGY_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_extra_l4_fails_before_png(self):
        """Unexpected phase maps cannot silently trim extra channels."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        data["phases"]["L4"] = data["phases"]["L3"]
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_TOPOLOGY_INVALID", result.stdout)

    def test_three_phase_primary_disagreement_fails_before_png(self):
        """The L1 phase snapshot must match the published primary."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        data["phases"]["L1"]["voltage"] = 180.0
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_PRIMARY_MISMATCH", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_primary_stale_fails_before_png(self):
        """A stale L1 in the phase map cannot inherit live global data."""

        self.write_three_phase()
        data = json.loads(self.health.read_text())
        data["phases"]["L1"]["last_success"] = NOW.timestamp() - 90
        self.health.write_text(json.dumps(data))
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_PRIMARY_MISMATCH", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_requires_v5_style(self):
        """Do not let the single-phase v4 renderer claim triple PASS."""

        self.write_three_phase()
        with patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": "v4"}):
            result = self.run_check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("THREE_PHASE_STYLE_REQUIRED", result.stdout)
        self.assertFalse(self.output.exists())

    def test_three_phase_inconsistent_labels_fails_before_png(self):
        """Do not accept one-phase line label with three-phase map."""

        self.write_three_phase(line_labels=["L"])
        result = self.run_v5()
        self.assertEqual(result.returncode, 2)
        self.assertIn("PHASE_TOPOLOGY_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

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

    def test_verified_cache_uses_retrieval_not_old_publication(self):
        """Accept a fresh checksum-verified cache with an older update."""

        self.write_verified()
        result = self.run_check("--verified-yasno-cache", str(self.cache))
        self.assertEqual(result.returncode, 0, result.stdout)
        data = json.loads(result.stdout)
        self.assertTrue(data["forecast_visible"])
        self.assertEqual(data["voltage_v"], 217.7)
        self.assertFalse(data["telegram_sent"])

    def test_corrupt_verified_cache_is_rejected(self):
        """A changed checksum cannot generate a fake forecast."""

        self.write_verified(corrupt=True)
        result = self.run_check("--verified-yasno-cache", str(self.cache))
        self.assertEqual(result.returncode, 2)
        self.assertIn("FORECAST_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_stale_verified_cache_is_rejected(self):
        """Published slots do not forgive an expired trusted retrieval."""

        self.write_verified(retrieved_age=7201)
        result = self.run_check("--verified-yasno-cache", str(self.cache))
        self.assertEqual(result.returncode, 2)
        self.assertIn("FORECAST_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_verified_cache_and_manual_forecast_are_exclusive(self):
        """Do not allow a manual fixture to override verified provenance."""

        self.write_verified()
        self.write_forecast()
        result = self.run_check(
            "--verified-yasno-cache",
            str(self.cache),
            "--forecast-file",
            str(self.forecast),
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

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
        self.assertIn("--verified-yasno-cache", result.stdout)


if __name__ == "__main__":
    unittest.main()
