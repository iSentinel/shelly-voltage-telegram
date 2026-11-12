"""Networkless acceptance tests for the local offline status PNG CLI."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from test_yasno_schedule import response

import yasno_cache as Y

SCRIPT = Path(__file__).resolve().parents[1] / (
    "scripts/graphic-offline-acceptance.py"
)
NOW = datetime(2026, 11, 10, 14, 30, tzinfo=ZoneInfo("Europe/Kyiv"))


class OfflineAcceptanceCliTests(unittest.TestCase):
    """A disconnected meter must never display historic voltage as current."""

    def setUp(self):
        """Create disposable fixtures for each scenario."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.health = root / "health.json"
        self.forecast = root / "forecast.json"
        self.verified = root / "verified-yasno.json"
        self.output = root / "card.png"
        self.set_health("offline")

    def set_health(self, connectivity: str):
        """Record an old, historical measurement with a known state."""

        health = {
            "connectivity": connectivity,
            "voltage": 216.9,
            "frequency": 50,
            "last_success": NOW.timestamp() - 3600,
        }
        self.health.write_text(json.dumps(health))

    def set_forecast(self, age: int = 30, day: str | None = None):
        """Supply a local verified YASNO day or an invalid old day."""

        self.forecast.write_text(
            json.dumps(
                {
                    "bars": "O" * 48,
                    "day": day or NOW.date().isoformat(),
                    "updated": NOW.timestamp() - age,
                    "source": "yasno",
                }
            )
        )

    def set_verified(
        self,
        age: int = 20,
        publication_hours: int = 16,
        corrupt: bool = False,
    ):
        """Construct a local checksum-checked source snapshot for QA."""

        data = response(NOW)
        data["10.1"]["updatedOn"] = (
            NOW - timedelta(hours=publication_hours)
        ).isoformat()
        envelope = Y.build_cache(data, NOW - timedelta(seconds=age))
        if corrupt:
            envelope["sha256"] = "0" * 64
        Y.atomic_write(str(self.verified), envelope)

    def check(self, *extras: str):
        """Run source-only CLI; no Telegram or network endpoints used."""

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
                *extras,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

    def test_offline_png_hides_historic_voltage(self):
        """No current reading must be shown from old sensor values."""

        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["status"], "PASS")
        self.assertFalse(record["live_voltage_visible"])
        self.assertFalse(record["forecast_visible"])
        self.assertFalse(record["telegram_sent"])
        self.assertTrue(
            self.output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        )

    def test_forecast_does_not_invent_live_voltage(self):
        """A green YASNO forecast is independent of Shelly connectivity."""

        self.set_forecast()
        result = self.check("--forecast-file", str(self.forecast))
        self.assertEqual(result.returncode, 0, result.stdout)
        record = json.loads(result.stdout)
        self.assertTrue(record["forecast_visible"])
        self.assertFalse(record["live_voltage_visible"])

    def test_same_png_can_be_rechecked_without_overwrite(self):
        """Repeated acceptance with same inputs is safely idempotent."""

        self.assertEqual(self.check().returncode, 0)
        before = self.output.read_bytes()
        result = self.check()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["output_action"], "reused")
        self.assertEqual(before, self.output.read_bytes())

    def test_existing_different_png_is_not_replaced(self):
        """Acceptance must not overwrite prior output silently."""

        self.assertEqual(self.check().returncode, 0)
        before = self.output.read_bytes()
        self.set_forecast()
        result = self.check("--forecast-file", str(self.forecast))
        self.assertEqual(result.returncode, 2)
        self.assertIn("OUTPUT_EXISTS_DIFFERENT", result.stdout)
        self.assertEqual(before, self.output.read_bytes())

    def test_oversized_existing_output_is_preserved(self):
        """Huge existing output cannot exhaust memory or be overwritten."""

        with self.output.open("wb") as output:
            output.truncate(3_000_000)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("OUTPUT_UNSAFE", result.stdout)
        self.assertEqual(self.output.stat().st_size, 3_000_000)

    def test_online_reading_cannot_pass_offline_gate(self):
        """A connected Shelly requires a separate online acceptance."""

        self.set_health("online")
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("NOT_OFFLINE", result.stdout)
        self.assertFalse(self.output.exists())

    def test_expired_forecast_fails_closed(self):
        """Never certify an expired YASNO plan as fresh."""

        self.set_forecast(age=9000)
        result = self.check("--forecast-file", str(self.forecast))
        self.assertEqual(result.returncode, 2)
        self.assertIn("STATE_INVALID", result.stdout)

    def test_invalid_group_is_rejected(self):
        """Only supported outage groups can be rendered."""

        result = self.check("--group", "10.1-injected")
        self.assertEqual(result.returncode, 2)
        self.assertIn("STATE_INVALID", result.stdout)

    def test_malformed_health_is_not_a_traceback(self):
        """Invalid JSON gives one concise, private-free FAIL record."""

        self.health.write_text("{broken")
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn("INPUT_INVALID", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_linked_health_is_rejected(self):
        """Do not follow links to arbitrary host-owned data."""

        original = self.health.with_name("real-health.json")
        self.health.rename(original)
        self.health.symlink_to(original)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("INPUT_UNSAFE", result.stdout)

    def test_linked_output_is_not_overwritten(self):
        """A symlink at the target must preserve the original file."""

        original = self.output.with_name("keep.png")
        original.write_bytes(b"keep-original")
        self.output.symlink_to(original)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(original.read_bytes(), b"keep-original")

    def test_large_health_input_is_bounded(self):
        """Reject oversized data before any expensive processing."""

        self.health.write_bytes(b"x" * 65537)
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("INPUT_TOO_LARGE", result.stdout)

    def test_incorrect_forecast_day_is_refused(self):
        """An old YASNO schedule cannot count as current."""

        self.set_forecast(day="2026-11-09")
        result = self.check("--forecast-file", str(self.forecast))
        self.assertEqual(result.returncode, 2)
        self.assertIn("STATE_INVALID", result.stdout)

    def test_invalid_clock_never_certifies_recovery(self):
        """NaN dates cannot bypass the stale-data policy."""

        result = self.check("--now", "nan")
        self.assertEqual(result.returncode, 2)
        self.assertIn("CLOCK_INVALID", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_output_directory_is_not_created(self):
        """Do not create arbitrary directory trees during read-only checks."""

        self.output = self.output.parent / "not-present" / "card.png"
        result = self.check()
        self.assertEqual(result.returncode, 2)
        self.assertIn("OUTPUT_UNSAFE", result.stdout)
        self.assertFalse(self.output.parent.exists())

    def test_private_health_metadata_never_reaches_jsonl(self):
        """Ignore unrelated private fields in diagnostic health records."""

        health = json.loads(self.health.read_text())
        health["local_debug_token"] = "REDACT_ME_DIAGNOSTIC"
        self.health.write_text(json.dumps(health))
        result = self.check()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("REDACT_ME_DIAGNOSTIC", result.stdout)

    def test_verified_yasno_offline_keeps_old_voltage_hidden(self):
        """Recent retrieval shows schedule, never last physical voltage."""

        self.set_verified()
        result = self.check("--verified-yasno-cache", str(self.verified))
        self.assertEqual(result.returncode, 0, result.stdout)
        record = json.loads(result.stdout)
        self.assertTrue(record["forecast_visible"])
        self.assertFalse(record["live_voltage_visible"])
        self.assertFalse(record["telegram_sent"])

    def test_invalid_verified_checksum_blocks_image(self):
        """Forged cache cannot make an offline forecast appear trusted."""

        self.set_verified(corrupt=True)
        result = self.check("--verified-yasno-cache", str(self.verified))
        self.assertEqual(result.returncode, 2)
        self.assertIn("FORECAST_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_stale_verified_retrieval_blocks_image(self):
        """Even a valid old cache must not be shown as a current day."""

        self.set_verified(age=7201)
        result = self.check("--verified-yasno-cache", str(self.verified))
        self.assertEqual(result.returncode, 2)
        self.assertIn("FORECAST_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_expired_verified_publication_blocks_image(self):
        """Fresh download cannot resurrect an expired provider schedule."""

        self.set_verified(publication_hours=37)
        result = self.check("--verified-yasno-cache", str(self.verified))
        self.assertEqual(result.returncode, 2)
        self.assertIn("FORECAST_INVALID", result.stdout)
        self.assertFalse(self.output.exists())

    def test_verified_yasno_and_manual_forecast_are_exclusive(self):
        """Refuse ambiguous forecast origins rather than mixing them."""

        self.set_verified()
        self.set_forecast()
        result = self.check(
            "--verified-yasno-cache",
            str(self.verified),
            "--forecast-file",
            str(self.forecast),
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

    def test_help_explains_explicit_local_inputs(self):
        """The tool must support noninteractive -h/--help."""

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--health-file", result.stdout)
        self.assertIn("--forecast-file", result.stdout)
        self.assertIn("--verified-yasno-cache", result.stdout)


if __name__ == "__main__":
    unittest.main()
