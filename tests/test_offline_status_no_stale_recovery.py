"""Offline Telegram statuses cannot claim ongoing voltage recovery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_app import APP, config


class OfflineRecoveryDisplayTests(unittest.TestCase):
    """Render offline events without fabricated current power quality."""

    def setUp(self):
        """Initialize disconnected test state and disposable database."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.monitor = APP.Monitor(
            config(database_path=Path(tmp.name) / "status.sqlite3")
        )
        self.monitor.storage.initialize()
        self.monitor.telegram.send = Mock()
        self.monitor.telegram.edit = Mock()
        self.monitor.telegram.pin = Mock()
        self.monitor.state.success(185.1, 50.0, 1.0)
        self.timestamp = float(self.monitor.state.snapshot()["last_success"])
        self.monitor.last_voltage_event = (
            "recovered",
            185.1,
            50.0,
            self.timestamp,
            "L1",
        )
        self.monitor.last_recovery_at = self.timestamp
        self.monitor.last_recovery_duration = 60.0

    def test_offline_ignores_historical_recovered_event_in_all_locales(self):
        """A recovery label is invalid while current voltage is unknown."""

        self.monitor.state.error("No route to host", offline_after=1)
        for language in ("ru", "uk", "en"):
            with self.subTest(language=language):
                self.monitor.language = language
                with patch.object(
                    APP.time, "time", return_value=self.timestamp + 60
                ):
                    status = APP.multiphase_status_text(self.monitor)
                self.assertIn(APP.tr(language, "status_offline"), status)
                self.assertNotIn(APP.tr(language, "event_recovered"), status)
                self.assertNotIn(
                    APP.tr(
                        language,
                        "power_recovered",
                        duration=APP.duration_text(60, language),
                    ),
                    status,
                )
                self.assertNotIn(
                    APP.tr(language, "status_frequency", value=50.0), status
                )
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()

    def test_event_returns_only_after_fresh_online_measurement(self):
        """An actual successful poll is required before recovery is shown."""

        self.monitor.state.error("offline", offline_after=1)
        offline = APP.multiphase_status_text(self.monitor)
        self.assertNotIn("✅", offline.splitlines()[0])
        self.monitor.state.success(220.0, 50.0, 1.0, recovery_after=1)
        self.monitor.last_voltage_event = (
            "recovered",
            220.0,
            50.0,
            self.monitor.state.snapshot()["last_success"],
            "L1",
        )
        recovered = APP.multiphase_status_text(self.monitor)
        self.assertTrue(recovered.startswith("✅ Напряжение восстановилось"))

    def test_starting_status_does_not_claim_recent_recovery(self):
        """A stale sample after restart must not be presented as online."""

        self.monitor.state.restore(self.timestamp, 185.1, 50.0)
        with self.monitor.state.lock:
            self.monitor.state.connectivity = "starting"
        status = APP.multiphase_status_text(self.monitor)
        self.assertNotIn("Напряжение восстановилось", status)
        self.assertIn(APP.tr("ru", "status_starting"), status)

    def test_no_new_event_or_database_writes_are_required(self):
        """Rendering a disconnected view must be strictly read-only."""

        self.monitor.state.error("offline", offline_after=1)
        self.monitor.storage.set_meta = Mock(
            side_effect=AssertionError("unexpected database mutation")
        )
        self.monitor.storage.add = Mock(
            side_effect=AssertionError("unexpected sample mutation")
        )
        _ = APP.multiphase_status_text(self.monitor)
        self.monitor.storage.set_meta.assert_not_called()
        self.monitor.storage.add.assert_not_called()
        self.monitor.telegram.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
