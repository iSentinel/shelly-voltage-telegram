"""Verify real local HTTP responses never relabel old power as live."""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from test_app import APP, config


class OfflineHTTPContractTests(unittest.TestCase):
    """Exercise disposable localhost HTTP with no Shelly or Telegram calls."""

    def setUp(self) -> None:
        """Start the production handler with a temporary isolated database."""

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.monitor = APP.Monitor(
            config(
                database_path=Path(self.tmp.name) / "http.sqlite3",
                stale_after=45.0,
                recovery_after_successes=2,
            )
        )
        self.monitor.storage.initialize()
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), APP.health_handler(self.monitor)
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.monitor.state.success(221.9, 50.0, 1.0)
        self.monitor.state.error("unreachable", offline_after=1)

    def get(self, route: str) -> tuple[int, str, dict[str, str]]:
        """Read only the ephemeral fixture server's HTTP response."""

        try:
            response = urllib.request.urlopen(self.base_url + route, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read().decode("utf-8")
            return response.status, content, dict(response.headers)

    def test_offline_status_marks_historical_values_not_live(self) -> None:
        """Current fields must be unknown although last-known data remains."""

        status, body, headers = self.get("/status.json")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["connectivity"], "offline")
        self.assertTrue(payload["sample_is_stale"])
        self.assertEqual(payload["voltage"], 221.9)
        self.assertIsNone(payload["live_voltage"])
        self.assertIsNone(payload["live_frequency"])
        self.assertEqual(headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(payload["phases"]["L1"]["live_voltage"], None)

    def test_offline_ready_metrics_and_dashboard_are_stale_safe(self) -> None:
        """Offline means 503, NaN metrics and no last voltage in dashboard."""

        code, body, _ = self.get("/ready")
        self.assertEqual(code, 503)
        self.assertEqual(json.loads(body)["ready"], False)
        code, metrics, _ = self.get("/metrics")
        self.assertEqual(code, 200)
        self.assertIn("shelly_voltage_volts NaN", metrics)
        self.assertIn("shelly_frequency_hz NaN", metrics)
        code, html, _ = self.get("/dashboard")
        self.assertEqual(code, 200)
        self.assertIn("offline", html.lower())
        self.assertNotIn("221.9", html)

    def test_recovery_requires_two_real_valid_samples(self) -> None:
        """First fresh reading is recovering; second confirms online."""

        self.monitor.state.success(225.0, 50.0, 1.0, recovery_after=2)
        code, body, _ = self.get("/status.json")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["live_voltage"], 225.0)
        self.assertEqual(self.get("/ready")[0], 503)
        self.monitor.state.success(225.5, 50.0, 1.0, recovery_after=2)
        code, body, _ = self.get("/ready")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ready"])


if __name__ == "__main__":
    unittest.main()
