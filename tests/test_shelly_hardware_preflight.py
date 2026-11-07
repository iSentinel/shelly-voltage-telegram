"""No-network checks for the explicit Shelly hardware preflight."""

from __future__ import annotations

import argparse
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/shelly-hardware-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("hardware_preflight", SOURCE)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def args(model: str = "pro_em50", samples: int = 2, interval: float = 0.0):
    """Make isolated CLI arguments with no production configuration."""

    return argparse.Namespace(model=model, samples=samples, interval=interval)


def identity():
    """Return public fixture identity and a private MAC that must not leak."""

    return {
        "model": "SPEM-002CEBEU50",
        "firmware": "2.1.1",
        "mac": "private-do-not-serialize",
        "uptime": 1000,
        "time_skew_sec": -0.2,
    }


class HardwarePreflightTests(unittest.TestCase):
    """Exercise only mocked meter RPCs."""

    def test_single_phase_read_only_probe(self):
        """Safe status returns samples but omits device MAC and credentials."""

        client = Mock()
        client.device_health.return_value = identity()
        client.read.return_value = (184.9, 50.0, 37.3)
        report = PROBE.run_probe(client, args())
        self.assertEqual(report["status"], "pass")
        self.assertEqual(len(report["samples"]), 2)
        self.assertEqual(report["samples"][0]["L1"]["voltage_v"], 184.9)
        self.assertNotIn("mac", str(report).lower())
        self.assertFalse(report["physical_family_independently_verified"])
        self.assertEqual(report["production_writes"], 0)
        self.assertEqual(report["telegram_writes"], 0)

    def test_complete_pro3em_three_phase_probe(self):
        """Only a three-phase complete atomic report can pass."""

        client = Mock()
        client.device_health.return_value = identity()
        client.read_phases.return_value = {
            "L1": (220.0, 50.0, 6.3),
            "L2": (222.0, 50.0, 6.3),
            "L3": (221.0, 50.0, 6.3),
        }
        report = PROBE.run_probe(client, args("pro3em", 1))
        self.assertEqual(set(report["samples"][0]), {"L1", "L2", "L3"})

    def test_missing_phase_is_failure(self):
        """Do not certify incomplete physical three-phase polling."""

        client = Mock()
        client.device_health.return_value = identity()
        client.read_phases.return_value = {
            "L1": (220.0, 50.0, 6.3),
            "L3": (221.0, 50.0, 6.3),
        }
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            PROBE.run_probe(client, args("pro3em", 1))

    def test_bad_numeric_and_zero_meter_samples_fail(self):
        """An invalid RPC may not be misclassified as working hardware."""

        client = Mock()
        client.device_health.return_value = identity()
        for voltage, frequency in ((900, 50), (230, 5), (0, 50)):
            with self.subTest(voltage=voltage, frequency=frequency):
                client.read.return_value = (voltage, frequency, 4.0)
                with self.assertRaises(RuntimeError):
                    PROBE.run_probe(client, args(samples=1))

    def test_legacy_runtime_without_shared_validator_still_works(self):
        """The probe must work on currently deployed 0.5.13-hw."""

        client = Mock()
        client.device_health.return_value = identity()
        client.read.return_value = (190.0, 50.0, 31.5)
        with patch.object(
            PROBE.app,
            "validated_meter_numbers",
            create=True,
            side_effect=AssertionError("legacy runtime must not need it"),
        ):
            report = PROBE.run_probe(client, args(samples=1))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["samples"][0]["L1"]["voltage_v"], 190.0)

    def test_malformed_rpc_and_latency_fail_locally(self):
        """Reject nonfinite, missing, boolean or invalid latency values."""

        for values in (
            None,
            (None, 50.0, 20.0),
            (True, 50.0, 20.0),
            (230.0, float("nan"), 20.0),
            (230.0, 50.0, float("inf")),
            (230.0, 50.0, -1.0),
            (230.0, 50.0),
        ):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                PROBE.validated_sample(values)

    def test_invalid_sampling_rejected_without_rpc(self):
        """Reject unreasonable counts or invalid delays before reading."""

        for count, interval in ((0, 0), (11, 0), (1, float("inf")), (1, -1)):
            with (
                self.subTest(count=count, interval=interval),
                self.assertRaises(ValueError),
            ):
                PROBE.validate_args(args(samples=count, interval=interval))

    def test_dry_run_does_not_initialize_client_or_call_telegram(self):
        """A dry run is safe without any device configuration."""

        out = io.StringIO()
        with (
            patch.object(
                sys,
                "argv",
                ["preflight", "--model", "pro_em50", "--dry-run", "--json"],
            ),
            patch.object(
                PROBE.app, "load_config", side_effect=AssertionError("network")
            ),
            redirect_stdout(out),
        ):
            self.assertEqual(PROBE.main(), 0)
        self.assertIn('"network_calls": 0', out.getvalue())

    def test_cli_help(self):
        """A one-shot production diagnostic must support -h/--help."""

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["preflight", "--help"]),
            redirect_stdout(out),
            self.assertRaises(SystemExit) as done,
        ):
            PROBE.parser().parse_args()
        self.assertEqual(done.exception.code, 0)
        self.assertIn("--dry-run", out.getvalue())

    def test_unknown_device_identity_does_not_pass(self):
        """Explicit identity is required even when numerical RPC works."""

        client = Mock()
        client.device_health.return_value = {"model": None, "uptime": 12}
        with self.assertRaisesRegex(RuntimeError, "identity"):
            PROBE.run_probe(client, args(samples=1))


if __name__ == "__main__":
    unittest.main()
