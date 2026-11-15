"""Offline three-phase Shelly parser regression from actual app.py source."""

from __future__ import annotations

import ast
import math
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[1] / "app.py"


class ShellyStub:
    """Capture a single invented RPC payload without opening a connection."""

    def __init__(self, payload: object) -> None:
        """Keep an entirely synthetic response and RPC call list."""
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, method: str, params: dict) -> object:
        """Return invented data and capture exactly which RPC was called."""
        self.calls.append((method, params))
        return self.payload


def named_node(name: str) -> ast.AST:
    """Extract one real source function or class, never execute whole app."""
    body = ast.parse(SOURCE.read_text()).body
    matches = [item for item in body if getattr(item, "name", "") == name]
    if len(matches) != 1:
        raise RuntimeError("QA_EXPECTED_ONE_APP_SOURCE_NODE")
    return matches[0]


def real_parser() -> type:
    """Compile only actual Pro3EM read logic plus numeric/error guards."""
    wanted = ("validated_meter_numbers", "em_status_errors", "Pro3EMClient")
    module = ast.fix_missing_locations(
        ast.Module(
            body=[named_node(name) for name in wanted],
            type_ignores=[],
        )
    )
    env = {
        "ShellyClient": ShellyStub,
        "math": math,
        "time": time,
        "EM_ERROR_FIELDS": (
            "errors",
            "a_errors",
            "b_errors",
            "c_errors",
            "n_errors",
            "total_errors",
        ),
        "KNOWN_EM_ERRORS": frozenset(
            {
                "power_meter_failure",
                "phase_sequence",
                "ct_type_not_set",
                "out_of_range:active_power",
                "out_of_range:apparent_power",
                "out_of_range:voltage",
                "out_of_range:current",
            }
        ),
    }
    exec(compile(module, str(SOURCE), "exec"), env)
    return env["Pro3EMClient"]


def complete_payload() -> dict[str, object]:
    """Generate all three valid invented voltage and frequency samples."""
    return {
        "a_voltage": 224.2,
        "a_freq": 50.0,
        "b_voltage": 231.4,
        "b_freq": 49.9,
        "c_voltage": 219.1,
        "c_freq": 50.0,
        "errors": [],
        "total_errors": [],
    }


def real_poll() -> object:
    """Extract only the actual Monitor.poll_pro3em_once method."""
    monitor = named_node("Monitor")
    node = next(
        item
        for item in monitor.body
        if getattr(item, "name", "") == "poll_pro3em_once"
    )
    code = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    scope = {"time": time}
    exec(compile(code, str(SOURCE), "exec"), scope)
    return scope["poll_pro3em_once"]


def real_hardware_probe(readings: dict) -> dict:
    """Run actual source hardware snapshot using only invented phase data."""
    node = named_node("read_hardware_snapshot")
    module = ast.fix_missing_locations(
        ast.Module(body=[node], type_ignores=[])
    )
    client = SimpleNamespace(read_phases=lambda: readings)
    scope = {
        "Config": object,
        "Pro3EMClient": lambda cfg: client,
        "ShellyClient": lambda cfg: client,
        "RpcMeterClient": lambda cfg, method: client,
    }
    exec(compile(module, str(SOURCE), "exec"), scope)
    return scope["read_hardware_snapshot"](
        SimpleNamespace(shelly_meter_type="pro3em")
    )


class Pro3EMParserTests(unittest.TestCase):
    """Use only known synthetic RPC answers and no external hardware."""

    def setUp(self) -> None:
        """Rebuild parser from actual source for each independent test."""
        self.parser = real_parser()

    def test_three_phases_single_rpc(self) -> None:
        """All three samples come from exactly one EM.GetStatus request."""
        client = self.parser(complete_payload())
        reading = client.read_phases()
        self.assertEqual(client.calls, [("EM.GetStatus", {"id": 0})])
        self.assertEqual(set(reading), {"L1", "L2", "L3"})
        self.assertEqual(reading["L1"][:2], (224.2, 50.0))
        self.assertEqual(reading["L2"][:2], (231.4, 49.9))
        self.assertEqual(reading["L3"][:2], (219.1, 50.0))
        self.assertTrue(all(row[2] >= 0 for row in reading.values()))

    def test_missing_L2_is_absent_not_zero(self) -> None:
        """An absent phase must not be reported as fictitious zero volts."""
        payload = complete_payload()
        del payload["b_voltage"]
        reading = self.parser(payload).read_phases()
        self.assertEqual(set(reading), {"L1", "L3"})
        self.assertNotIn("L2", reading)

    def test_missing_L3_frequency_is_absent(self) -> None:
        """An unknown phase frequency cannot be manufactured."""
        payload = complete_payload()
        payload["c_freq"] = None
        self.assertNotIn("L3", self.parser(payload).read_phases())

    def test_actual_zero_zero_remains_measured_sample(self) -> None:
        """Only explicitly received zero/zero may be returned as a reading."""
        payload = complete_payload()
        payload.update({"a_voltage": 0, "a_freq": 0})
        self.assertEqual(
            self.parser(payload).read_phases()["L1"][:2], (0.0, 0.0)
        )

    def test_boolean_voltage_rejected(self) -> None:
        """True is not a physically valid metering number."""
        payload = complete_payload()
        payload["a_voltage"] = True
        with self.assertRaisesRegex(RuntimeError, "numeric"):
            self.parser(payload).read_phases()

    def test_nonfinite_value_rejected(self) -> None:
        """NaN or infinity may not enter persisted phase history."""
        payload = complete_payload()
        payload["b_voltage"] = float("nan")
        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            self.parser(payload).read_phases()

    def test_negative_voltage_rejected(self) -> None:
        """Reject malformed negative readings without storing data."""
        payload = complete_payload()
        payload["c_voltage"] = -2
        with self.assertRaisesRegex(RuntimeError, "negative"):
            self.parser(payload).read_phases()

    def test_out_of_range_voltage_rejected(self) -> None:
        """Reject a meter voltage above the accepted input range."""
        payload = complete_payload()
        payload["a_voltage"] = 550
        with self.assertRaisesRegex(RuntimeError, "out-of-range"):
            self.parser(payload).read_phases()

    def test_total_errors_array_blocks_all_phases(self) -> None:
        """Pro3EM's total_errors must not be ignored by the parser."""
        payload = complete_payload()
        payload["total_errors"] = ["phase_sequence"]
        with self.assertRaisesRegex(RuntimeError, "total_errors"):
            self.parser(payload).read_phases()

    def test_phase_fault_blocks_all_phases(self) -> None:
        """An explicit ct_type_not_set device fault blocks ingestion."""
        payload = complete_payload()
        payload["b_errors"] = ["ct_type_not_set"]
        with self.assertRaisesRegex(RuntimeError, "b_errors"):
            self.parser(payload).read_phases()

    def test_unknown_error_code_redacted(self) -> None:
        """Untrusted error payload text must never enter Telegram/logs."""
        payload = complete_payload()
        payload["errors"] = ["SENSITIVE_CLIENT_BLOB"]
        with self.assertRaisesRegex(RuntimeError, "unknown_error") as err:
            self.parser(payload).read_phases()
        self.assertNotIn("SENSITIVE_CLIENT_BLOB", str(err.exception))

    def test_bad_error_shape_fails_closed(self) -> None:
        """Malformed total_errors is not evidence of a healthy meter."""
        payload = complete_payload()
        payload["total_errors"] = {"unexpected": "shape"}
        with self.assertRaisesRegex(RuntimeError, "invalid_format"):
            self.parser(payload).read_phases()

    def test_rpc_non_dict_fails_closed(self) -> None:
        """Refuse malformed RPC without inventing readings."""
        with self.assertRaisesRegex(RuntimeError, "invalid status"):
            self.parser([1, 2, 3]).read_phases()

    def test_all_phases_missing_returns_no_samples(self) -> None:
        """An empty but valid envelope returns no fabricated phases."""
        self.assertEqual(self.parser({"errors": []}).read_phases(), {})


class Pro3EMPollTests(unittest.TestCase):
    """Check the actual polling method records missing phases as missing."""

    def test_missing_L2_only_records_L1_L3(self) -> None:
        """A partial poll may never write a fake 0V sample for L2."""
        recorded: list[str] = []
        missing: list[str] = []
        source = SimpleNamespace(
            shelly=SimpleNamespace(
                read_phases=lambda: {
                    "L1": (230.0, 50.0, 1.5),
                    "L3": (225.0, 50.0, 1.5),
                }
            ),
            accept_phase_sample=lambda phase, *_: recorded.append(phase),
            record_phase_missing=lambda phase, _: missing.append(phase),
        )
        self.assertTrue(real_poll()(source))
        self.assertEqual(recorded, ["L1", "L3"])
        self.assertEqual(missing, ["L2"])

    def test_rpc_exception_marks_all_missing_without_writes(self) -> None:
        """A meter failure must not synthesize successful phase samples."""
        recorded: list[str] = []
        missing: list[str] = []

        def broken() -> dict:
            """Simulate a failed local EM.GetStatus operation."""
            raise RuntimeError("synthetic local meter offline")

        source = SimpleNamespace(
            shelly=SimpleNamespace(read_phases=broken),
            accept_phase_sample=lambda *args: recorded.append(args[0]),
            record_phase_missing=lambda phase, _: missing.append(phase),
        )
        self.assertFalse(real_poll()(source))
        self.assertEqual(recorded, [])
        self.assertEqual(missing, ["L1", "L2", "L3"])


class Pro3EMProbeTests(unittest.TestCase):
    """Probe must require three complete phases before hardware acceptance."""

    def test_probe_returns_three_distinct_phase_values(self) -> None:
        """The onboarding snapshot preserves L1, L2 and L3 separately."""
        readings = {
            "L1": (224.2, 50.0, 1.0),
            "L2": (231.4, 49.9, 1.1),
            "L3": (219.1, 50.0, 1.2),
        }
        result = real_hardware_probe(readings)
        self.assertEqual(result["meter_type"], "pro3em")
        self.assertEqual(result["phases"]["L2"]["voltage"], 231.4)
        self.assertEqual(len(result["phases"]), 3)
        self.assertEqual(result["rpc_ms"], 1.2)

    def test_probe_rejects_incomplete_hardware_status(self) -> None:
        """A missing phase blocks three-phase readiness."""
        readings = {
            "L1": (224.2, 50.0, 1.0),
            "L3": (219.1, 50.0, 1.2),
        }
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            real_hardware_probe(readings)


if __name__ == "__main__":
    unittest.main()
