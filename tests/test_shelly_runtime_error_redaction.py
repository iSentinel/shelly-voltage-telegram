"""Never leak a Shelly endpoint or its credentials in operational errors."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class ShellyErrorRedactionTests(unittest.TestCase):
    """Exercise disposable state and mocked network without real Telegram."""

    def setUp(self):
        """Create a temporary monitor disconnected from real services."""

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.monitor = APP.Monitor(
            config(
                database_path=Path(temp.name) / "safe.sqlite3",
                offline_after_failures=1,
                telegram_token="",
                telegram_chat_id="",
            )
        )
        self.monitor.storage.initialize()
        self.secret = "http://user:private-password@192.0.2.10/rpc"

    def test_http_health_never_exposes_request_endpoint(self):
        """A failed request cannot put raw URL or password in /health."""

        error = APP.requests.exceptions.ConnectionError(
            f"HTTPConnectionPool(host=192.0.2.10): {self.secret}"
        )
        self.monitor.shelly.read = Mock(side_effect=error)
        with (
            patch.object(APP.logger, "warning") as warn,
            patch.object(APP.logger, "debug") as debug,
        ):
            self.assertFalse(self.monitor.poll_once())
        snap = APP.service_snapshot(self.monitor)
        serialized = json.dumps(snap, ensure_ascii=False)
        self.assertEqual(snap["last_error"], "connection_error")
        self.assertEqual(
            snap["phases"]["L1"]["last_error"], "connection_error"
        )
        self.assertEqual(snap["connectivity"], "offline")
        self.assertIsNone(snap["live_voltage"])
        for fragment in ("192.0.2.10", "private-password", "user:"):
            self.assertNotIn(fragment, serialized)
            self.assertNotIn(fragment, str(warn.call_args_list))
            self.assertNotIn(fragment, str(debug.call_args_list))
        self.assertEqual(self.monitor.storage.outage_total("L1"), 1)
        self.assertIsNone(self.monitor.storage.latest("L1"))

    def test_timeout_type_is_kept_without_secret_text(self):
        """Diagnose timeouts by type, not by freeform exception message."""

        error = APP.requests.exceptions.ConnectTimeout(self.secret)
        self.monitor.shelly.read = Mock(side_effect=error)
        with patch.object(APP.logger, "warning") as warning:
            self.assertFalse(self.monitor.poll_once())
        self.assertEqual(
            self.monitor.state.snapshot()["last_error"], "timeout"
        )
        self.assertNotIn(self.secret, str(warning.call_args_list))

    def test_unknown_exception_text_never_reaches_state_or_logs(self):
        """A remote error message is untrusted even if the class is safe."""

        self.monitor.shelly.read = Mock(side_effect=RuntimeError(self.secret))
        with patch.object(APP.logger, "warning") as warning:
            self.assertFalse(self.monitor.poll_once())
        self.assertEqual(
            self.monitor.state.snapshot()["last_error"], "read_error"
        )
        self.assertNotIn("private-password", str(warning.call_args_list))

    def test_secondary_phases_do_not_expose_bad_rpc_content(self):
        """Every configured Pro3EM phase reports only classified failures."""

        self.monitor = APP.Monitor(
            config(
                database_path=self.monitor.cfg.database_path,
                shelly_meter_type="pro3em",
                offline_after_failures=1,
                telegram_token="",
                telegram_chat_id="",
            )
        )
        self.monitor.storage.initialize()
        self.monitor.shelly.read_phases = Mock(
            side_effect=APP.requests.exceptions.ConnectionError(self.secret)
        )
        with (
            patch.object(APP.logger, "warning") as warning,
            patch.object(APP.logger, "debug") as debug,
        ):
            self.assertFalse(self.monitor.poll_pro3em_once())
        for phase in ("L1", "L2", "L3"):
            with self.subTest(phase=phase):
                state = self.monitor.phase_states[phase].snapshot()
                self.assertEqual(state["last_error"], "read_error")
                self.assertEqual(state["connectivity"], "offline")
        snap = json.dumps(APP.service_snapshot(self.monitor))
        self.assertNotIn(self.secret, snap)
        self.assertNotIn(self.secret, str(warning.call_args_list))
        self.assertNotIn(self.secret, str(debug.call_args_list))

    def test_repeated_failures_keep_one_incident_without_spam(self):
        """Redaction does not change outage or repeated warning behavior."""

        self.monitor.shelly.read = Mock(
            side_effect=ConnectionError(self.secret)
        )
        with (
            patch.object(APP.logger, "warning") as warning,
            patch.object(APP.logger, "debug"),
        ):
            for _ in range(50):
                self.assertFalse(self.monitor.poll_once())
        self.assertEqual(self.monitor.storage.outage_total("L1"), 1)
        self.assertEqual(
            self.monitor.state.snapshot()["consecutive_failures"], 50
        )
        self.assertEqual(warning.call_count, 1)
        self.assertFalse(self.monitor.telegram.enabled)


if __name__ == "__main__":
    unittest.main()
