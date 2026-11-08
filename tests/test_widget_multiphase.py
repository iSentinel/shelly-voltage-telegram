"""Three-phase Telegram widget tests without any Shelly or Telegram I/O."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from test_app import APP, config

from telegram_widget_data import widget_public_data
from telegram_widget_fallback import widget_fallback_text

NOW = datetime(2026, 11, 8, 2, 0, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()


def online(voltage: float, stamp: float = NOW - 3) -> dict:
    """Create an isolated, recently measured phase snapshot."""

    return {
        "connectivity": "online",
        "last_success": stamp,
        "voltage": voltage,
        "frequency": 50.0,
    }


def three_phases() -> dict[str, dict]:
    """Model two secondary phases with independent connection state."""

    return {
        "L2": online(224.2),
        "L3": {
            "connectivity": "offline",
            "last_success": NOW - 600,
            "voltage": 232.9,
            "frequency": 50.0,
        },
    }


class MultiphaseWidgetTests(unittest.TestCase):
    """Never display cached phase telemetry as current measurements."""

    def test_each_phase_fails_closed_independently(self):
        """Fresh L2 must not make an offline L3 appear live."""

        state = widget_public_data(
            online(223.1), phases=three_phases(), now=NOW, language="ru"
        )
        data = json.loads(state)
        self.assertEqual(data["v"], 223.1)
        self.assertEqual(data["p"]["L2"]["v"], 224.2)
        self.assertEqual(data["p"]["L3"], {"s": "offline"})
        text = widget_fallback_text(state, now=NOW)
        self.assertIn("L2: 224.2 В", text)
        self.assertIn("L3: —", text)
        self.assertNotIn("232.9", text)

    def test_l1_offline_does_not_hide_good_secondary_phases(self):
        """Loss of L1 cannot turn valid L2 and L3 into fake zeros."""

        disconnected = {
            "connectivity": "offline",
            "last_success": NOW - 200,
            "voltage": 218.7,
        }
        phases = {"L2": online(226.7), "L3": online(224.9)}
        state = widget_public_data(
            disconnected, phases=phases, now=NOW, language="en"
        )
        text = widget_fallback_text(state, now=NOW)
        self.assertIn("Disconnected", text)
        self.assertNotIn("218.7", text)
        self.assertIn("L2: 226.7 V", text)
        self.assertIn("L3: 224.9 V", text)

    def test_secondary_phase_ages_out(self):
        """An L2 sample older than 60 seconds must be hidden."""

        phases = three_phases()
        phases["L2"]["last_success"] = NOW - 61
        data = json.loads(
            widget_public_data(online(223.1), phases=phases, now=NOW)
        )
        self.assertEqual(data["p"]["L2"]["s"], "stale")
        self.assertNotIn("v", data["p"]["L2"])
        self.assertIn("L2: —", widget_fallback_text(json.dumps(data), now=NOW))

    def test_incomplete_or_wrong_phase_names_fail(self):
        """Reject ambiguous phase configurations before serializing data."""

        for invalid in (
            {},
            {"L2": online(222.0)},
            {"L2": online(220.0), "L4": online(221.0)},
            {"L2": online(220.0), "L3": None},
        ):
            with (
                self.subTest(invalid=str(list(invalid))),
                self.assertRaises(ValueError),
            ):
                widget_public_data(online(223.1), phases=invalid, now=NOW)

    def test_payload_stays_within_telegram_limit(self):
        """All three voltage channels plus a whole day must fit."""

        state = widget_public_data(
            online(223.1),
            phases=three_phases(),
            now=NOW,
            group="10.1",
            bars="OX?O" * 12,
            day="2026-11-08",
            forecast_updated=NOW - 60,
        )
        self.assertLessEqual(len(state.encode("utf-8")), 1024)

    def test_one_phase_preview_has_no_extra_phase_tiles(self):
        """EM-50 users must see only the single measured phase."""

        monitor = APP.Monitor(
            config(shelly_meter_type="pro_em50", telegram_language="uk")
        )
        monitor.state.success(225.0, 50.0, 5.0, 1)
        text = APP.private_widget_preview_text(monitor)
        self.assertIn("Напруга: 225.0 В", text)
        self.assertNotIn("L2:", text)
        self.assertNotIn("L3:", text)

    def test_pro3em_private_preview_respects_independent_state(self):
        """Preview must reflect the three configured states individually."""

        monitor = APP.Monitor(
            config(shelly_meter_type="pro3em", telegram_language="ru")
        )
        monitor.state.success(224.4, 50.0, 5.0, 1)
        monitor.phase_states["L2"].success(221.8, 50.0, 5.0, 1)
        monitor.phase_states["L3"].error("disconnected", 1)
        text = APP.private_widget_preview_text(monitor)
        self.assertIn("Напряжение: 224.4 В", text)
        self.assertIn("L2: 221.8 В", text)
        self.assertIn("L3: —", text)

    def test_payload_ignores_sensitive_snapshot_metadata(self):
        """Only permitted numeric fields reach Telegram's public_data."""

        phase = online(221.4)
        phase["private_token"] = "never-publish-me"
        state = widget_public_data(
            online(221.8),
            phases={"L2": phase, "L3": online(220.9)},
            now=NOW,
        )
        self.assertNotIn("private_token", state)
        self.assertNotIn("never-publish-me", state)


if __name__ == "__main__":
    unittest.main()
