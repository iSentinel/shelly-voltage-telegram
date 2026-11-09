"""Verify invalid RPC data cannot forge connection recovery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_app import APP, config


class RecoverySequenceTests(unittest.TestCase):
    """All fixtures and measurement data are disconnected from production."""

    def setUp(self):
        """Create a temporary monitor with no Telegram credentials."""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.monitor = APP.Monitor(
            config(
                database_path=Path(directory.name) / "sample.sqlite3",
                shelly_meter_type="em_gen3",
                offline_after_failures=1,
                recovery_after_successes=3,
                telegram_token="",
                telegram_chat_id="",
            )
        )
        self.monitor.storage.initialize()
        self.assertFalse(self.monitor.telegram.enabled)

    def poll(self, voltage: float, frequency: float) -> bool:
        """Return a scripted read-only RPC response from an in-memory mock."""

        self.monitor.shelly.rpc = Mock(
            return_value={
                "voltage": voltage,
                "freq": frequency,
            }
        )
        return self.monitor.poll_once()

    def expect(self, state: str, code: int) -> None:
        """Check both local state and HTTP readiness classification."""

        self.assertEqual(self.monitor.state.snapshot()["connectivity"], state)
        ready_code, ready = APP.readiness(self.monitor)
        self.assertEqual(ready_code, code)
        self.assertEqual(ready["ready"], code == 200)

    def test_bad_interleaved_rpc_resets_success_counter(self):
        """Require three consecutive valid polls to close the same outage."""

        self.assertFalse(self.poll(900.0, 50.0))
        self.expect("offline", 503)
        self.assertIsNone(self.monitor.storage.latest("L1"))
        self.assertEqual(self.monitor.storage.outage_total("L1"), 1)

        for voltage in (210.0, 211.0):
            self.assertTrue(self.poll(voltage, 50.0))
            self.expect("recovering", 503)
        self.assertFalse(self.poll(225.0, 5.0))
        self.expect("offline", 503)
        self.assertEqual(self.monitor.storage.outage_total("L1"), 1)

        for voltage in (212.0, 213.0):
            self.assertTrue(self.poll(voltage, 50.0))
            self.expect("recovering", 503)
        self.assertTrue(self.poll(214.0, 50.0))
        self.expect("online", 200)
        self.assertIsNone(self.monitor.storage.open_outage_started("L1"))
        self.assertEqual(len(self.monitor.storage.recent(20, "L1")), 5)

    def test_bad_pro3em_phase_does_not_write_partial_cycle(self):
        """L2 impossible voltage rejects all three phase measurements."""

        with tempfile.TemporaryDirectory() as folder:
            meter = APP.Monitor(
                config(
                    database_path=Path(folder) / "triphase.sqlite3",
                    shelly_meter_type="pro3em",
                    offline_after_failures=1,
                    telegram_token="",
                    telegram_chat_id="",
                )
            )
            meter.storage.initialize()
            meter.shelly.rpc = Mock(
                return_value={
                    "a_voltage": 210.0,
                    "a_freq": 50.0,
                    "b_voltage": 900.0,
                    "b_freq": 50.0,
                    "c_voltage": 212.0,
                    "c_freq": 50.0,
                }
            )
            self.assertFalse(meter.poll_pro3em_once())
            for phase in ("L1", "L2", "L3"):
                with self.subTest(phase=phase):
                    self.assertIsNone(meter.storage.latest(phase))
                    self.assertEqual(
                        meter.phase_states[phase].snapshot()["connectivity"],
                        "offline",
                    )
                    self.assertIsNotNone(
                        meter.storage.open_outage_started(phase)
                    )


if __name__ == "__main__":
    unittest.main()
