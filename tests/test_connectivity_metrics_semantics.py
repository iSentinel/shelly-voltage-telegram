"""Prometheus compatibility without claiming actual electricity loss."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_app import APP, config

from telegram_i18n import tr


def find_value(text: str, prefix: str) -> str:
    """Extract a single exact Prometheus series without partial matches."""

    for line in text.splitlines():
        if line.startswith(prefix + " "):
            return line.split(" ", 1)[1]
    raise AssertionError(f"missing metric: {prefix}")


class ConnectivityMetricTests(unittest.TestCase):
    """Retain legacy names while exposing accurate new descriptions."""

    def test_unlabeled_metrics_preserve_legacy_values(self):
        """New series must share exactly the old count and duration."""

        with tempfile.TemporaryDirectory() as directory:
            monitor = APP.Monitor(
                config(database_path=Path(directory) / "monitor.sqlite3")
            )
            monitor.storage.initialize()
            monitor.state.error("unreachable", offline_after=1)
            now = APP.time.time()
            monitor.storage.begin_outage(now - 20)
            text = APP.metrics_text(monitor)
            pairs = (
                ("shelly_connectivity_losses_total", "shelly_outages_total"),
                ("shelly_connectivity_losses_today", "shelly_outages_today"),
                (
                    "shelly_connectivity_loss_duration_today_seconds",
                    "shelly_outage_duration_today_seconds",
                ),
            )
            for current, legacy in pairs:
                with self.subTest(metric=current):
                    self.assertEqual(
                        find_value(text, current),
                        find_value(text, legacy),
                    )
            self.assertEqual(
                find_value(text, "shelly_connectivity_losses_total"), "1"
            )

    def test_metric_help_never_claims_proven_mains_switching(self):
        """HELP should identify the source as connectivity hysteresis."""

        with tempfile.TemporaryDirectory() as directory:
            monitor = APP.Monitor(
                config(database_path=Path(directory) / "health.sqlite3")
            )
            monitor.storage.initialize()
            output = APP.metrics_text(monitor)
            help_lines = [
                line
                for line in output.splitlines()
                if line.startswith("# HELP shelly_")
                and ("outage" in line or "connectivity_loss" in line)
            ]
            self.assertGreaterEqual(len(help_lines), 6)
            self.assertIn("not electricity downtime", output)
            self.assertIn("not verified mains outages", output)
            self.assertNotIn("Confirmed outage duration", output)

    def test_legacy_labeled_phase_metric_kept(self):
        """Preserve existing phase dashboards during gradual migration."""

        monitor = APP.Monitor(config(shelly_meter_type="pro3em"))
        monitor.phase_states["L2"].error("unreachable", offline_after=1)
        text = "\n".join(APP.phase_metrics(monitor, "L2"))
        old = 'shelly_outages_total{phase="L2"}'
        new = 'shelly_connectivity_losses_total{phase="L2"}'
        self.assertEqual(find_value(text, old), find_value(text, new))
        self.assertEqual(find_value(text, new), "1")

    def test_em50_does_not_add_unconfigured_phase_samples(self):
        """Avoid fabricating phase metrics for single-phase hardware."""

        monitor = APP.Monitor(config(shelly_meter_type="pro_em50"))
        text = "\n".join(APP.phase_metrics(monitor, "L2"))
        self.assertIn('shelly_phase_configured{phase="L2"} 0', text)
        self.assertNotIn('shelly_connectivity_losses_total{phase="L2"}', text)

    def test_event_empty_text_never_says_confirmed_mains(self):
        """Empty history labels are neutral in RU, UK and EN."""

        expected = {
            "ru": "Событий мониторинга",
            "uk": "Подій моніторингу",
            "en": "No monitoring events",
        }
        for language, phrase in expected.items():
            with self.subTest(language=language):
                text = tr(language, "events_empty")
                self.assertIn(phrase, text)
                self.assertNotIn("Подтвержд", text)
                self.assertNotIn("Підтвердж", text)
                self.assertNotIn("confirmed", text.lower())

    def test_live_voltage_remains_unknown_in_offline_metrics(self):
        """Renaming counters must not revive stale voltage readings."""

        monitor = APP.Monitor(config())
        monitor.state.restore(APP.time.time() - 120, 234.8, 50)
        monitor.state.error("network", offline_after=1)
        with tempfile.TemporaryDirectory() as folder:
            monitor.storage = APP.Storage(
                Path(folder) / "readonly.sqlite3", "Europe/Kyiv"
            )
            monitor.storage.initialize()
            metrics = APP.metrics_text(monitor)
            self.assertIn("shelly_voltage_volts NaN", metrics)
            self.assertIn('shelly_connectivity{state="offline"} 1', metrics)
            self.assertIn("shelly_connectivity_losses_total 1", metrics)


if __name__ == "__main__":
    unittest.main()
