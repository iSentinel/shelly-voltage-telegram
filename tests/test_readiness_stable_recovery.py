"""Readiness gates must not confuse one recovery sample with online service."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from test_app import APP, config


class StableReadinessTests(unittest.TestCase):
    """Verify readiness across a natural sequence of successful polls."""

    def setUp(self):
        """Use an isolated monitor; no device or Telegram network calls."""

        self.monitor = APP.Monitor(
            config(recovery_after_successes=3, stale_after=30)
        )
        self.monitor.telegram.send = Mock()
        self.monitor.telegram.edit = Mock()
        self.monitor.telegram.pin = Mock()

    def test_first_two_successes_do_not_signal_ready(self):
        """A recovered socket alone is not stable recovery confirmation."""

        with patch.object(APP.time, "time", return_value=1000.0):
            self.monitor.state.success(225.0, 50.0, 2.0)
            self.monitor.state.error("unreachable", offline_after=1)
            for i in (1, 2):
                self.monitor.state.success(
                    220.0 + i, 50.0, 2.0, recovery_after=3
                )
                status, payload = APP.readiness(self.monitor)
                self.assertEqual(status, 503)
                self.assertFalse(payload["ready"])
                self.assertEqual(payload["connectivity"], "recovering")
                self.assertEqual(payload["successes"], i)
                self.assertEqual(payload["required_successes"], 3)
                self.assertEqual(
                    payload["reason"], "recovery not yet confirmed"
                )

    def test_ready_only_after_third_fresh_sample(self):
        """The stable-success count has to reach the configured value."""

        with patch.object(APP.time, "time", return_value=2000.0):
            self.monitor.state.success(221.0, 50.0, 2.0)
            self.monitor.state.error("unreachable", offline_after=1)
            for _ in range(3):
                self.monitor.state.success(221.0, 50.0, 2.0, recovery_after=3)
            status, payload = APP.readiness(self.monitor)
            self.assertEqual(status, 200)
            self.assertTrue(payload["ready"])
            self.assertEqual(payload["connectivity"], "online")
        with patch.object(APP.time, "time", return_value=2100.0):
            status, payload = APP.readiness(self.monitor)
            self.assertEqual(status, 503)
            self.assertEqual(payload["reason"], "sample is stale")

    def test_restored_sample_does_not_make_startup_ready(self):
        """A fresh cached value is not an actual successful live poll."""

        with patch.object(APP.time, "time", return_value=3000.0):
            self.monitor.state.restore(2998.0, 225.0, 50.0)
            status, payload = APP.readiness(self.monitor)
            self.assertEqual(status, 503)
            self.assertEqual(
                payload["reason"], "waiting for confirmed online sample"
            )
            self.assertEqual(payload["connectivity"], "starting")

    def test_offline_without_sample_remains_offline(self):
        """Cold-start network failure must retain the existing reason."""

        self.monitor.state.error("no route to host", offline_after=1)
        status, payload = APP.readiness(self.monitor)
        self.assertEqual(status, 503)
        self.assertEqual(payload["reason"], "shelly offline")

    def test_health_live_sample_still_visible_during_recovery(self):
        """Only readiness changes; telemetry retains fresh information."""

        with patch.object(APP.time, "time", return_value=4000.0):
            self.monitor.state.success(222.0, 50.0, 1.0)
            self.monitor.state.error("offline", offline_after=1)
            self.monitor.state.success(223.0, 50.0, 1.0, recovery_after=3)
            health = APP.service_snapshot(self.monitor)
            status, payload = APP.readiness(self.monitor)
            self.assertEqual(health["connectivity"], "recovering")
            self.assertEqual(health["live_voltage"], 223.0)
            self.assertEqual(status, 503)
            self.assertFalse(payload["ready"])
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
