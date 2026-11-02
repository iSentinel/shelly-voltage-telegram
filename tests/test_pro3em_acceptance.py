import argparse
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "pro3em-acceptance.py"
SPEC = importlib.util.spec_from_file_location("pro3em_acceptance", SCRIPT)
ACCEPT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ACCEPT
assert SPEC.loader is not None
SPEC.loader.exec_module(ACCEPT)


class FakeClient:
    """Return scripted Pro 3EM readings or failures."""

    def __init__(self, results):
        self.results = iter(results)

    def read_phases(self):
        """Return the next scripted result."""
        value = next(self.results)
        if isinstance(value, Exception):
            raise value
        return value


def args(**overrides):
    """Build acceptance arguments for unit tests."""
    values = {
        "samples": 1,
        "interval": 0.0,
        "timeout": 1.0,
        "min_voltage": 50.0,
        "max_voltage": 300.0,
        "min_frequency": 40.0,
        "max_frequency": 70.0,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def good_readings():
    """Return one valid atomic three-phase response."""
    return {
        "L1": (221.1, 50.0, 12.5),
        "L2": (220.8, 50.0, 12.5),
        "L3": (222.0, 50.0, 12.5),
    }


class Pro3EMAcceptanceTests(unittest.TestCase):
    def test_valid_three_phase_sample_passes(self):
        payload = ACCEPT.run_acceptance(FakeClient([good_readings()]), args())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(
            set(payload["samples"][0]["phases"]), set(ACCEPT.PHASES)
        )
        self.assertTrue(payload["read_only"])

    def test_missing_phase_fails(self):
        readings = good_readings()
        del readings["L3"]
        payload = ACCEPT.run_acceptance(FakeClient([readings]), args())
        self.assertEqual(payload["status"], "fail")
        self.assertIn("missing phases: L3", payload["samples"][0]["errors"])

    def test_zero_voltage_fails(self):
        readings = good_readings()
        readings["L2"] = (0.0, 50.0, 12.5)
        payload = ACCEPT.run_acceptance(FakeClient([readings]), args())
        self.assertEqual(payload["status"], "fail")
        self.assertIn(
            "L2 voltage out of range: 0.0", payload["samples"][0]["errors"]
        )

    def test_rpc_failure_is_concise(self):
        client = FakeClient([TimeoutError("unreachable")])
        payload = ACCEPT.run_acceptance(client, args())
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["samples"][0]["error_type"], "TimeoutError")

    def test_non_atomic_latency_fails(self):
        readings = good_readings()
        readings["L3"] = (222.0, 50.0, 13.0)
        payload = ACCEPT.run_acceptance(FakeClient([readings]), args())
        self.assertEqual(payload["status"], "fail")
        self.assertIn(
            "phases did not come from one atomic RPC cycle",
            payload["samples"][0]["errors"],
        )

    def test_sleep_occurs_only_between_samples(self):
        client = FakeClient([good_readings(), good_readings()])
        with patch.object(ACCEPT.time, "sleep") as sleep:
            payload = ACCEPT.run_acceptance(
                client, args(samples=2, interval=0.1)
            )
        self.assertEqual(payload["status"], "pass")
        sleep.assert_called_once_with(0.1)


if __name__ == "__main__":
    unittest.main()
