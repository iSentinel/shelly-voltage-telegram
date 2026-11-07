"""Recovery banner must never describe abnormal voltage as normal."""

from __future__ import annotations

import math
import unittest
from unittest.mock import Mock

from test_app import APP, config


class RecoveredHeaderTruthTests(unittest.TestCase):
    """Check independent configured and measured voltage thresholds."""

    def setUp(self):
        """Construct one disconnected test monitor without Telegram calls."""

        self.monitor = APP.Monitor(config())
        self.monitor.telegram.send = Mock()
        self.monitor.telegram.edit = Mock()
        self.monitor.telegram.pin = Mock()

    def test_recovery_title_uses_actual_voltage_in_all_languages(self):
        """185 V is a critical state, even after low-voltage alert resets."""

        values = (
            (185.1, "🚨"),
            (195.0, "⚠️"),
            (220.0, "✅"),
            (254.0, "⚠️"),
            (256.0, "🚨"),
        )
        for language in ("ru", "uk", "en"):
            self.monitor.language = language
            for voltage, expected_icon in values:
                with self.subTest(lang=language, voltage=voltage):
                    title = APP.voltage_event_title(
                        self.monitor, "recovered", voltage
                    )
                    self.assertTrue(title.startswith(expected_icon), title)
                    if voltage != 220.0:
                        self.assertNotIn("✅", title)

    def test_boundaries_follow_current_admin_config(self):
        """Live setting changes change labels without altering events."""

        self.monitor.critical_low = 175.0
        self.monitor.warning_low = 200.0
        self.monitor.warning_high = 240.0
        self.monitor.critical_high = 260.0
        for voltage, icon in (
            (175.0, "🚨"),
            (175.1, "⚠️"),
            (200.0, "⚠️"),
            (200.1, "✅"),
            (239.9, "✅"),
            (240.0, "⚠️"),
            (260.0, "🚨"),
        ):
            with self.subTest(voltage=voltage):
                title = APP.voltage_event_title(
                    self.monitor, "recovered", voltage
                )
                self.assertTrue(title.startswith(icon), title)

    def test_status_banner_and_voltage_icon_agree_at_185_v(self):
        """The original operator screenshot mismatch cannot recur."""

        now = APP.time.time()
        self.monitor.language = "uk"
        self.monitor.state.success(185.3, 50.0, 1.0, recovery_after=1)
        self.monitor.last_voltage_event = ("recovered", 185.1, 50.0, now, "L1")
        self.monitor.storage.day_stats = Mock(
            return_value={
                "count": 10,
                "min": 118.1,
                "max": 228.4,
                "avg": 205.8,
            }
        )
        text = APP.multiphase_status_text(self.monitor)
        self.assertIn("🚨 Напруга досі критично низька", text)
        self.assertIn("🔴 L: 185.3 В", text)
        self.assertNotIn("✅ Напруга відновилася", text)
        self.monitor.telegram.send.assert_not_called()
        self.monitor.telegram.edit.assert_not_called()
        self.monitor.telegram.pin.assert_not_called()

    def test_nonfinite_recovery_has_unknown_status(self):
        """Corrupt historical metadata must never display green recovery."""

        for voltage in (math.nan, math.inf, -math.inf):
            with self.subTest(value=repr(voltage)):
                title = APP.voltage_event_title(
                    self.monitor, "recovered", voltage
                )
                self.assertTrue(title.startswith("⚠️"))
                self.assertNotIn("✅", title)

    def test_other_event_kinds_keep_previous_localized_titles(self):
        """Low and high transitions retain existing semantics."""

        for kind in (
            "low",
            "warning_low",
            "critical_low",
            "warning_high",
            "critical_high",
        ):
            title = APP.voltage_event_title(self.monitor, kind, 185.1)
            self.assertEqual(title, APP.tr("ru", f"event_{kind}"))
        self.monitor.telegram.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
