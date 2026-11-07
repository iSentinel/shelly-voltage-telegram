"""Reject malformed Shelly RPC numbers before status or SQLite writes."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_app import APP, config


class RpcMeasurementSafetyTests(unittest.TestCase):
    """Verify every Shelly model family rejects invalid numeric payloads."""

    def single_phase_client(self):
        """Create a generic single-phase Gen3 model with mocked RPC."""

        return APP.RpcMeterClient(
            config(shelly_meter_type="em_gen3"), "EM1.GetStatus"
        )

    def pro3em_client(self):
        """Create a three-phase client without physical hardware access."""

        return APP.Pro3EMClient(config(shelly_meter_type="pro3em"))

    def test_all_single_phase_nonfinite_numbers_rejected(self):
        """NaN and infinities cannot be accepted as measured voltage."""

        for bad in (
            float("nan"),
            float("inf"),
            float("-inf"),
            "NaN",
            "Infinity",
            "-Infinity",
        ):
            with self.subTest(value=str(bad)):
                client = self.single_phase_client()
                client.rpc = Mock(return_value={"voltage": bad, "freq": 50})
                with self.assertRaisesRegex(RuntimeError, "non-finite"):
                    client.read()

    def test_single_phase_frequency_must_be_finite(self):
        """Frequency cannot be an infinite or NaN numeric sample."""

        for bad in (float("nan"), float("inf"), "NaN", "inf"):
            with self.subTest(value=str(bad)):
                client = self.single_phase_client()
                client.rpc = Mock(return_value={"voltage": 220, "freq": bad})
                with self.assertRaisesRegex(RuntimeError, "non-finite"):
                    client.read()

    def test_boolean_and_invalid_text_must_not_be_voltage(self):
        """Python bool and arbitrary strings are not meter numbers."""

        for bad in (True, False, "private-invalid-data", {}):
            with self.subTest(value=str(bad)):
                client = self.single_phase_client()
                client.rpc = Mock(return_value={"voltage": bad, "freq": 50})
                with self.assertRaisesRegex(RuntimeError, "invalid"):
                    client.read()

    def test_negative_voltage_or_frequency_is_rejected(self):
        """Measured magnitude and frequency cannot be negative."""

        for voltage, frequency in ((-5, 50), (220, -50)):
            with self.subTest(voltage=voltage, frequency=frequency):
                client = self.single_phase_client()
                client.rpc = Mock(
                    return_value={"voltage": voltage, "freq": frequency}
                )
                with self.assertRaisesRegex(RuntimeError, "negative"):
                    client.read()

    def test_all_supported_single_phase_clients_share_validation(self):
        """Single-phase device wrappers use the same numeric guard."""

        for model, method in APP.METER_RPC.items():
            with self.subTest(model=model):
                client = APP.RpcMeterClient(
                    config(shelly_meter_type=model), method
                )
                client.rpc = Mock(return_value={"voltage": "NaN", "freq": 50})
                with self.assertRaisesRegex(RuntimeError, "non-finite"):
                    client.read()

    def test_three_phase_nan_prevents_partial_phase_samples(self):
        """One corrupted phase invalidates the complete atomic RPC."""

        for prefix in ("a", "b", "c"):
            with self.subTest(phase=prefix):
                client = self.pro3em_client()
                payload = {
                    "a_voltage": 220,
                    "a_freq": 50,
                    "b_voltage": 221,
                    "b_freq": 50,
                    "c_voltage": 222,
                    "c_freq": 50,
                }
                payload[f"{prefix}_voltage"] = float("nan")
                client.rpc = Mock(return_value=payload)
                with self.assertRaisesRegex(RuntimeError, "non-finite"):
                    client.read_phases()

    def test_three_phase_invalid_frequency_prevents_partial_cycle(self):
        """No L1/L2/L3 phase should be returned on invalid frequency."""

        client = self.pro3em_client()
        client.rpc = Mock(
            return_value={
                "a_voltage": 220,
                "a_freq": 50,
                "b_voltage": 221,
                "b_freq": float("inf"),
                "c_voltage": 222,
                "c_freq": 50,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            client.read_phases()

    def test_numeric_strings_remain_compatible(self):
        """The valid numeric RPC string format remains supported."""

        client = self.single_phase_client()
        client.rpc = Mock(return_value={"voltage": "230.5", "freq": "50.0"})
        self.assertEqual(client.read()[:2], (230.5, 50.0))

    def test_zero_voltage_is_not_invented_or_converted(self):
        """A measured zero stays zero rather than becoming a fake value."""

        client = self.single_phase_client()
        client.rpc = Mock(return_value={"voltage": 0.0, "freq": 50.0})
        self.assertEqual(client.read()[:2], (0.0, 50.0))

    def test_physical_em50_rpc_rejects_nonfinite_values(self):
        """The legacy EM-50 HTTP client must share the numeric guard."""

        for raw in (float("nan"), "Infinity", float("-inf")):
            with self.subTest(bad=str(raw)):
                client = APP.ShellyClient(config(shelly_meter_type="pro_em50"))
                response = Mock()
                response.json.return_value = {"voltage": raw, "freq": 50.0}
                client.session.get = Mock(return_value=response)
                with self.assertRaisesRegex(RuntimeError, "non-finite"):
                    client.read()

    def test_pro3em_invalid_rpc_never_persists_nan_rows(self):
        """A malformed phase cannot leak a NULL voltage into SQLite."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = APP.Monitor(
                config(
                    database_path=Path(folder) / "samples.sqlite3",
                    shelly_meter_type="pro3em",
                )
            )
            monitor.storage.initialize()
            monitor.shelly.rpc = Mock(
                return_value={
                    "a_voltage": 230,
                    "a_freq": 50,
                    "b_voltage": float("nan"),
                    "b_freq": 50,
                    "c_voltage": 232,
                    "c_freq": 50,
                }
            )
            self.assertFalse(monitor.poll_pro3em_once())
            self.assertIsNone(monitor.storage.latest("L1"))
            self.assertIsNone(monitor.storage.latest("L2"))
            self.assertIsNone(monitor.storage.latest("L3"))


if __name__ == "__main__":
    unittest.main()
