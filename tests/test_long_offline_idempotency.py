"""Protect connectivity incident history across thousands of failed polls."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class LongOfflineIdempotencyTests(unittest.TestCase):
    """Use an isolated SQLite database and mocked Telegram and Shelly."""

    def failed_polls(self, monitor, count: int, error: str) -> None:
        """Perform many failures without network or noisy test logs."""

        monitor.shelly.read = Mock(side_effect=OSError(error))
        with (
            patch.object(APP.logger, "warning"),
            patch.object(APP.logger, "debug"),
        ):
            for _ in range(count):
                self.assertFalse(monitor.poll_once())

    def assert_single_open_outage(self, monitor) -> None:
        """Ensure disconnected polls did not duplicate the incident."""

        self.assertEqual(monitor.storage.outage_total("L1"), 1)
        self.assertIsNotNone(monitor.storage.open_outage_started("L1"))
        self.assertEqual(monitor.storage.latest("L1")[1], 230.0)

    def finish_recovery(self, monitor) -> None:
        """Require exactly three successive fresh polls before online."""

        monitor.rotate_status_after_recovery = Mock()
        monitor.shelly.read = Mock(return_value=(224.0, 50.0, 1.0))
        for state in ("recovering", "recovering", "online"):
            self.assertTrue(monitor.poll_once())
            self.assertEqual(monitor.state.snapshot()["connectivity"], state)
        self.assertEqual(APP.readiness(monitor)[0], 200)
        self.assertEqual(monitor.storage.outage_total("L1"), 1)
        self.assertIsNone(monitor.storage.open_outage_started("L1"))
        monitor.rotate_status_after_recovery.assert_called_once()
        monitor.telegram.send.assert_not_called()
        self.assertEqual(len(monitor.storage.recent(30, "L1")), 4)

    def test_thousands_of_failures_and_restart_are_idempotent(self):
        """One incident survives an offline restart without alert spam."""

        with tempfile.TemporaryDirectory() as directory:
            cfg = config(
                database_path=Path(directory) / "offline.sqlite3",
                offline_after_failures=3,
                recovery_after_successes=3,
                availability_alerts_enabled=True,
                telegram_token="fixture-token",
                telegram_chat_id="100",
                telegram_status_chat_id="200",
            )
            original = APP.Monitor(cfg)
            original.storage.initialize()
            original.storage.add(APP.time.time() - 5, 230.0, 50.0)
            original.state.restore(APP.time.time() - 5, 230.0, 50.0)
            original.telegram.send = Mock(return_value=1)
            self.failed_polls(original, 2800, "offline")
            self.assert_single_open_outage(original)
            original.telegram.send.assert_called_once()

            restarted = APP.Monitor(cfg)
            restarted.storage.initialize()
            restarted.restore_phase("L1")
            restarted.telegram.send = Mock(return_value=2)
            self.failed_polls(restarted, 250, "still offline")
            self.assert_single_open_outage(restarted)
            restarted.telegram.send.assert_not_called()
            self.assertEqual(APP.readiness(restarted)[0], 503)
            self.finish_recovery(restarted)


if __name__ == "__main__":
    unittest.main()
