"""No physically impossible RPC payload may certify restored connectivity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_app import APP, config


class PhysicalMeterRangeTests(unittest.TestCase):
    """Check read-only model RPCs and disposable monitor integration."""

    def test_all_models_reject_impossible_voltage_and_frequency(self):
        """No RPC wrapper may accept impossible nonzero-power readings."""

        for model, method in APP.METER_RPC.items():
            for voltage, frequency in (
                (900.0, 50.0),
                (500.0, 50.0),
                (225.0, 5.0),
                (225.0, 0.0),
                (225.0, 70.1),
                (0.0, 5.0),
            ):
                with self.subTest(model=model, v=voltage, f=frequency):
                    client = APP.RpcMeterClient(
                        config(shelly_meter_type=model), method
                    )
                    client.rpc = Mock(
                        return_value={
                            "voltage": voltage,
                            "freq": frequency,
                        }
                    )
                    with self.assertRaisesRegex(RuntimeError, "out-of-range"):
                        client.read()

    def test_legacy_em50_rejects_impossible_values(self):
        """EM-50 direct HTTP reading uses the same numeric restrictions."""

        for voltage, frequency in ((900.0, 50.0), (230.0, 5.0), (230.0, 71.0)):
            with self.subTest(voltage=voltage, frequency=frequency):
                client = APP.ShellyClient(config(shelly_meter_type="pro_em50"))
                response = Mock()
                response.json.return_value = {
                    "voltage": voltage,
                    "freq": frequency,
                }
                client.session.get = Mock(return_value=response)
                with self.assertRaisesRegex(RuntimeError, "out-of-range"):
                    client.read()

    def test_actual_zero_and_real_low_high_voltage_are_preserved(self):
        """Do not fabricate or suppress actual zero or severe grid events."""

        for voltage, frequency in (
            (0.0, 50.0),
            (0.0, 0.0),
            (118.1, 50.0),
            (262.9, 50.0),
            (499.0, 70.0),
            (230.0, 40.0),
        ):
            with self.subTest(voltage=voltage, frequency=frequency):
                self.assertEqual(
                    APP.validated_meter_numbers(voltage, frequency),
                    (voltage, frequency),
                )

    def test_bad_phase_rejects_entire_atomic_pro3em_rpc(self):
        """A corrupted L2 value prevents L1/L2/L3 partial acceptance."""

        for field, bad in (("b_voltage", 900.0), ("b_freq", 5.0)):
            with self.subTest(field=field):
                client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
                payload = {
                    "a_voltage": 230.0,
                    "a_freq": 50.0,
                    "b_voltage": 231.0,
                    "b_freq": 50.0,
                    "c_voltage": 232.0,
                    "c_freq": 50.0,
                }
                payload[field] = bad
                client.rpc = Mock(return_value=payload)
                with self.assertRaisesRegex(RuntimeError, "out-of-range"):
                    client.read_phases()

    def test_invalid_rpc_never_stored_or_classified_as_online(self):
        """Reject bad meter data before any SQLite or status mutation."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = APP.Monitor(
                config(
                    database_path=Path(folder) / "samples.sqlite3",
                    shelly_meter_type="em_gen3",
                    offline_after_failures=1,
                    telegram_token="",
                    telegram_chat_id="",
                )
            )
            monitor.storage.initialize()
            monitor.shelly.rpc = Mock(
                return_value={
                    "voltage": 900.0,
                    "freq": 5.0,
                }
            )
            self.assertFalse(monitor.poll_once())
            self.assertIsNone(monitor.storage.latest("L1"))
            self.assertEqual(
                monitor.state.snapshot()["connectivity"], "offline"
            )
            self.assertEqual(monitor.storage.outage_total("L1"), 1)
            self.assertIsNotNone(monitor.storage.open_outage_started("L1"))

    def test_bad_recovery_rpc_cannot_close_existing_outage(self):
        """A malformed retry must preserve an open connectivity incident."""

        with tempfile.TemporaryDirectory() as folder:
            monitor = APP.Monitor(
                config(
                    database_path=Path(folder) / "samples.sqlite3",
                    shelly_meter_type="em_gen3",
                    offline_after_failures=1,
                    recovery_after_successes=2,
                    telegram_token="",
                    telegram_chat_id="",
                )
            )
            monitor.storage.initialize()
            monitor.shelly.rpc = Mock(
                return_value={"voltage": 220.0, "freq": 50.0}
            )
            self.assertTrue(monitor.poll_once())
            monitor.shelly.rpc = Mock(side_effect=OSError("offline"))
            self.assertFalse(monitor.poll_once())
            self.assertIsNotNone(monitor.storage.open_outage_started("L1"))
            monitor.shelly.rpc = Mock(
                return_value={
                    "voltage": 900.0,
                    "freq": 5.0,
                }
            )
            self.assertFalse(monitor.poll_once())
            self.assertEqual(
                monitor.state.snapshot()["connectivity"], "offline"
            )
            self.assertIsNotNone(monitor.storage.open_outage_started("L1"))
            self.assertEqual(monitor.storage.outage_total("L1"), 1)


if __name__ == "__main__":
    unittest.main()
