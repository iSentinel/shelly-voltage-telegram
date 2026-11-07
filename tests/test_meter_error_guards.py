"""Single-phase Shelly RPC errors must not be accepted as voltage."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from test_app import APP, config


class MeterErrorGuardTests(unittest.TestCase):
    """Exercise all supported single-phase RPC model selectors."""

    def client(self, model: str):
        """Build a disposable client without contacting a real meter."""

        cfg = config(shelly_meter_type=model)
        if model == "pro_em50":
            return APP.ShellyClient(cfg)
        return APP.RpcMeterClient(cfg, APP.METER_RPC[model])

    def read(self, model: str, payload: dict):
        """Read only a mocked RPC response for the chosen model."""

        client = self.client(model)
        if model == "pro_em50":
            response = Mock()
            response.json.return_value = payload
            with patch.object(client.session, "get", return_value=response):
                return client.read()
        with patch.object(client, "rpc", return_value=payload):
            return client.read()

    def test_all_single_phase_models_reject_reported_meter_failure(self):
        """A present voltage does not override an RPC meter fault."""

        for model in APP.METER_RPC:
            with self.subTest(model=model):
                payload = {
                    "voltage": 230.0,
                    "freq": 50.0,
                    "errors": ["power_meter_failure"],
                }
                with self.assertRaisesRegex(RuntimeError, "meter errors"):
                    self.read(model, payload)

    def test_unknown_error_text_is_not_reflected(self):
        """Never print arbitrary device-provided error strings."""

        secret = "arbitrary-remote-sensitive-string"
        for model in ("pro_em50", "plus_1pm", "pm_mini_gen3"):
            with self.subTest(model=model):
                payload = {
                    "voltage": 230.0,
                    "freq": 50.0,
                    "errors": [secret],
                }
                with self.assertRaises(RuntimeError) as caught:
                    self.read(model, payload)
                self.assertIn("unknown_error", str(caught.exception))
                self.assertNotIn(secret, str(caught.exception))

    def test_malformed_errors_field_is_not_a_healthy_reading(self):
        """Invalid error structures must fail closed, not become telemetry."""

        for model in ("pro_em50", "1pm_gen4", "plus_pm_mini"):
            with self.subTest(model=model):
                payload = {"voltage": 230.0, "freq": 50.0, "errors": None}
                with self.assertRaisesRegex(RuntimeError, "invalid_format"):
                    self.read(model, payload)

    def test_empty_errors_preserve_legitimate_sampling(self):
        """Healthy device status remains readable for each RPC family."""

        for model in APP.METER_RPC:
            with self.subTest(model=model):
                payload = {"voltage": 230.0, "freq": 50.0, "errors": []}
                voltage, frequency, latency = self.read(model, payload)
                self.assertEqual((voltage, frequency), (230.0, 50.0))
                self.assertGreaterEqual(latency, 0.0)

    def test_missing_frequency_is_not_fabricated(self):
        """Older device replies lacking frequency must not invent 50 Hz."""

        for model in ("plus_1pm", "pm_mini_gen3"):
            with self.subTest(model=model):
                payload = {"voltage": 230.0, "errors": []}
                with self.assertRaises(RuntimeError):
                    self.read(model, payload)


if __name__ == "__main__":
    unittest.main()
