"""Networkless acceptance for measured-power Telegram PNG cards."""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram_card_png import (
    GREEN,
    HEIGHT,
    RED,
    WIDTH,
    render_card_png,
)

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 10, 2, 10, tzinfo=KYIV).timestamp()


def fixture(online: bool = True) -> dict:
    """Build a deterministic state with independently scheduled outages."""

    day = datetime.fromtimestamp(NOW, KYIV).date().isoformat()
    midnight = datetime.fromtimestamp(NOW, KYIV).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return {
        "schema": 1,
        "l": "uk",
        "s": "online" if online else "offline",
        "v": 223.0,
        "f": 50.0,
        "t": NOW - 10,
        "b": "O" * 17 + "X" * 15 + "?" * 16,
        "d": day,
        "u": NOW - 120,
        "e": (midnight + timedelta(days=1)).timestamp(),
        "g": "10.1",
        "src": "yasno",
    }


def read_png(path: Path) -> tuple[int, int, bytes]:
    """Extract unfiltered RGB pixels without Pillow or native packages."""

    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    compressed = bytearray()
    width = height = 0
    while offset < len(raw):
        length = struct.unpack(">I", raw[offset : offset + 4])[0]
        tag = raw[offset + 4 : offset + 8]
        payload = raw[offset + 8 : offset + 8 + length]
        if tag == b"IHDR":
            width, height = struct.unpack(">II", payload[:8])
        if tag == b"IDAT":
            compressed.extend(payload)
        offset += 12 + length
    data = zlib.decompress(compressed)
    stride = width * 3 + 1
    assert all(data[i] == 0 for i in range(0, len(data), stride))
    return (
        width,
        height,
        b"".join(
            data[i + 1 : i + stride] for i in range(0, len(data), stride)
        ),
    )


class TelegramGraphicWidgetTests(unittest.TestCase):
    """Never confuse schedule forecasts with measured electricity."""

    def setUp(self):
        """Create one disposable render directory for the test."""

        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def render(self, state: dict) -> tuple[dict, bytes]:
        """Render exactly once and return the report plus PNG bytes."""

        target = self.root / "status.png"
        report = render_card_png(state, target, now=NOW)
        self.assertLess(target.stat().st_size, 500_000)
        return report, target.read_bytes()

    def test_valid_online_card_shows_measurement_and_forecast(self):
        """Real voltage and the predicted outage are independently visible."""

        result, _ = self.render(fixture())
        self.assertTrue(result["live_voltage_visible"])
        self.assertTrue(result["forecast_visible"])
        self.assertEqual(result["status"], "online")
        self.assertEqual(result["forecast_off_slots"], 15)
        width, height, rgb = read_png(self.root / "status.png")
        self.assertEqual((width, height), (WIDTH, HEIGHT))
        self.assertEqual(
            rgb[
                (430 * width + 60 + 17 * 17 + 8) * 3 : (
                    430 * width + 60 + 17 * 17 + 8
                )
                * 3
                + 3
            ],
            RED,
        )
        self.assertEqual(
            rgb[(430 * width + 68) * 3 : (430 * width + 68) * 3 + 3], GREEN
        )

    def test_offline_ignores_hidden_historical_voltage(self):
        """A planned power-on period cannot make an offline reading live."""

        old = fixture(online=False)
        a, data1 = self.render(old)
        old.pop("v")
        old.pop("t")
        old.pop("f")
        b, data2 = self.render(old)
        self.assertEqual(data1, data2)
        self.assertEqual(a["status"], "offline")
        self.assertFalse(a["live_voltage_visible"])
        self.assertTrue(b["forecast_visible"])

    def test_stale_online_sample_never_draws_live_voltage(self):
        """The measurement expires after 60 seconds even if still online."""

        old = fixture()
        old["t"] = NOW - 61
        status, _ = self.render(old)
        self.assertEqual(status["status"], "stale")
        self.assertFalse(status["live_voltage_visible"])

    def test_boolean_nan_and_out_of_range_are_never_live(self):
        """Invalid sensor readings must fail closed."""

        for voltage in (True, float("nan"), -1, 600, None):
            with self.subTest(voltage=voltage):
                reading = fixture()
                reading["v"] = voltage
                status, _ = self.render(reading)
                self.assertFalse(status["live_voltage_visible"])

    def test_expired_yasno_never_paints_forecast(self):
        """Old data stays hidden independently from live Shelly telemetry."""

        for key, value in (
            ("u", NOW - 7201),
            ("d", "2026-11-09"),
            ("e", NOW - 1),
            ("src", "community"),
            ("g", "unauthorized-group"),
        ):
            with self.subTest(key=key):
                state = fixture()
                state[key] = value
                report, _ = self.render(state)
                self.assertFalse(report["forecast_visible"])
                self.assertTrue(report["live_voltage_visible"])

    def test_forecast_x_does_not_mark_real_online_shelly_offline(self):
        """A red forecast cannot override an independently fresh meter."""

        state = fixture()
        state["b"] = "X" * 48
        report, _ = self.render(state)
        self.assertTrue(report["live_voltage_visible"])
        self.assertEqual(report["status"], "online")
        self.assertEqual(report["forecast_off_slots"], 48)

    def test_forecast_o_does_not_restore_offline_shelly(self):
        """A green forecast does not prove physical power is available."""

        state = fixture(online=False)
        state["b"] = "O" * 48
        report, _ = self.render(state)
        self.assertEqual(report["status"], "offline")
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])

    def test_repeated_render_is_identical(self):
        """Pixel payload is deterministic for same explicit clock and state."""

        state = fixture()
        _, one = self.render(state)
        _, two = self.render(state)
        self.assertEqual(one, two)

    def test_invalid_schema_and_clock_are_rejected(self):
        """Unsupported states cannot generate fake-looking status cards."""

        with self.assertRaises(ValueError):
            render_card_png({}, self.root / "invalid.png", NOW)
        with self.assertRaises(ValueError):
            render_card_png(fixture(), self.root / "invalid.png", float("nan"))


if __name__ == "__main__":
    unittest.main()
