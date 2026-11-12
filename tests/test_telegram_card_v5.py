"""Source-only regression tests for smooth Design Polish v5 rendering."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image

from telegram_card_png import HEIGHT, WIDTH, render_card_png
from telegram_card_v5 import (
    PAST_COLORS,
    Painter,
    font,
    visible_periods,
)

KYIV = ZoneInfo("Europe/Kyiv")
NOW = datetime(2026, 11, 12, 16, 45, tzinfo=KYIV).timestamp()


def state(online: bool = True) -> dict:
    """Build an independent meter state and full-day YASNO forecast."""

    local = datetime.fromtimestamp(NOW, KYIV)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "schema": 1,
        "s": "online" if online else "offline",
        "v": 221.4,
        "f": 50.0,
        "t": NOW - 5,
        "b": "X" * 8 + "O" * 9 + "X" * 15 + "O" * 3 + "X" * 9 + "O" * 4,
        "d": local.date().isoformat(),
        "e": (midnight + timedelta(days=1)).timestamp(),
        "u": NOW - 120,
        "g": "10.1",
        "src": "yasno",
        "l": "uk",
    }


class GraphicV5Tests(unittest.TestCase):
    """Ensure v5 remains separate from physical power and legacy v4."""

    def setUp(self):
        """Use disposable files and the explicit v5 renderer option."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.target = self.folder / "card.png"
        env = patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": "v5"})
        env.start()
        self.addCleanup(env.stop)

    def render(self, payload: dict):
        """Render with a fixed clock, without network or Bot API actions."""

        report = render_card_png(payload, self.target, now=NOW)
        with Image.open(self.target) as picture:
            picture.load()
            self.assertEqual(picture.size, (WIDTH, HEIGHT))
            self.assertEqual(picture.mode, "RGB")
        self.assertEqual(report["style"], "v5")
        self.assertLess(self.target.stat().st_size, 500_000)
        return report, self.target.read_bytes()

    def test_online_voltage_is_visible_and_forecast_is_independent(self):
        """A red planned outage does not contradict real online Shelly."""

        payload = state()
        payload["b"] = "X" * 48
        report, _ = self.render(payload)
        self.assertTrue(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])
        self.assertEqual(report["forecast_off_slots"], 48)
        self.assertEqual(report["status"], "online")

    def test_offline_cannot_reveal_historical_voltage(self):
        """Old voltage and frequency fields have zero effect on offline PNG."""

        payload = state(online=False)
        report, first = self.render(payload)
        payload.update(v=180.1, f=42.0, t=NOW - 5000)
        second = self.render(payload)[1]
        self.assertEqual(first, second)
        self.assertFalse(report["live_voltage_visible"])
        self.assertTrue(report["forecast_visible"])

    def test_forecast_does_not_revive_offline_meter(self):
        """Green scheduled slots are not evidence of physical supply."""

        payload = state(online=False)
        payload["b"] = "O" * 48
        report, _ = self.render(payload)
        self.assertEqual(report["status"], "offline")
        self.assertFalse(report["live_voltage_visible"])

    def test_old_measurement_is_stale_not_current(self):
        """Connectivity alone cannot make a stale reading live."""

        payload = state()
        payload["t"] = NOW - 61
        report, _ = self.render(payload)
        self.assertEqual(report["status"], "stale")
        self.assertFalse(report["live_voltage_visible"])

    def test_expired_forecast_has_no_valid_slots(self):
        """A previous day's YASNO cache must be hidden from v5."""

        payload = state()
        payload["u"] = NOW - 7201
        report, _ = self.render(payload)
        self.assertFalse(report["forecast_visible"])
        self.assertTrue(report["live_voltage_visible"])

    def test_double_resolution_type_is_antialiased(self):
        """Inter text has intermediate edge colors after Lanczos reduction."""

        self.render(state())
        with Image.open(self.target) as im:
            palette = set(im.crop((63, 40, 340, 80)).getdata())
        self.assertGreater(len(palette), 20)

    def test_ukrainian_glyphs_are_available(self):
        """The font includes Ukrainian I, Yi, Ye and Ghe characters."""

        root = os.getenv("TELEGRAM_V5_FONT_ROOT", "/usr/share/fonts")
        face = font(root, "medium", 22)
        for glyph in ("І", "Ї", "Є", "Ґ", "і", "ї", "є", "ґ"):
            with self.subTest(glyph=glyph):
                self.assertIsNotNone(face.getmask(glyph).getbbox())

    def test_repeated_output_is_identical(self):
        """Stable state and clock must generate the same PNG bytes."""

        payload = state()
        first = self.render(payload)[1]
        second = self.render(payload)[1]
        self.assertEqual(first, second)

    def test_missing_fonts_fail_closed_before_file_output(self):
        """Never silently fall back to a bitmap on a broken Docker image."""

        with (
            patch.dict(
                os.environ,
                {"TELEGRAM_V5_FONT_ROOT": "/not-installed-v5-fonts"},
            ),
            self.assertRaisesRegex(ValueError, "V5_FONT_MISSING"),
        ):
            render_card_png(state(), self.target, now=NOW)
        self.assertFalse(self.target.exists())

    def test_legacy_bitmap_style_remains_usable(self):
        """Explicitly selecting v4 preserves the existing tested path."""

        with patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": "v4"}):
            report = render_card_png(state(), self.target, now=NOW)
        self.assertNotIn("style", report)
        self.assertTrue(report["live_voltage_visible"])

    def test_unknown_style_does_not_guess(self):
        """Reject accidental renderer typos instead of sending surprises."""

        with (
            patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": "v6"}),
            self.assertRaisesRegex(ValueError, "unsupported graphic style"),
        ):
            render_card_png(state(), self.target, now=NOW)
        self.assertFalse(self.target.exists())

    def test_invalid_voltage_is_never_displayed_live(self):
        """Use shared fail-closed measurement checks for the new design."""

        for voltage in (-1, None, 0.0, 500.0, float("nan"), True):
            with self.subTest(voltage=voltage):
                payload = state()
                payload["v"] = voltage
                report, _ = self.render(payload)
                self.assertFalse(report["live_voltage_visible"])

    def test_every_hour_has_a_visible_timestamp(self):
        """Restore 00:00 through 23:00 labels in a six-by-four grid."""

        original = Painter.text
        with patch.object(
            Painter, "text", autospec=True, side_effect=original
        ) as writer:
            self.render(state())
        stamps = {
            call.args[3]: (call.args[1], call.args[2])
            for call in writer.call_args_list
            if isinstance(call.args[3], str)
        }
        for hour in range(24):
            label = f"{hour:02d}:00"
            with self.subTest(hour=hour):
                self.assertIn(label, stamps)
                x, y = stamps[label]
                self.assertGreaterEqual(x, 64)
                self.assertLess(x, 893)
                self.assertEqual(y, 421 + 40 * (hour // 6))

    def test_half_hour_color_changes_survive_hourly_layout(self):
        """08:00–08:30 light and 08:30–09:00 outage stay distinct."""

        original = Painter.rect
        with patch.object(
            Painter, "rect", autospec=True, side_effect=original
        ) as drawing:
            self.render(state())
        rectangles = {
            (call.args[1], call.args[2]) for call in drawing.call_args_list
        }
        self.assertIn(((344, 453, 409, 488), PAST_COLORS["O"]), rectangles)
        self.assertIn(((409, 453, 474, 488), PAST_COLORS["X"]), rectangles)

    def test_six_exact_periods_return_in_text(self):
        """Show start/end/duration with 24:00 intact below the tiles."""

        original = Painter.text
        with patch.object(
            Painter, "text", autospec=True, side_effect=original
        ) as writer:
            self.render(state())
        text = [call.args[3] for call in writer.call_args_list]
        expected = (
            "00:00 – 04:00 (≈4 год.)",
            "04:00 – 08:30 (≈4.5 год.)",
            "08:30 – 16:00 (≈7.5 год.)",
            "16:00 – 17:30 (≈1.5 год.)",
            "17:30 – 22:00 (≈4.5 год.)",
            "22:00 – 24:00 (≈2 год.)",
        )
        for row in expected:
            with self.subTest(row=row):
                self.assertIn(row, text)
        self.assertEqual(len(visible_periods(state()["b"])), 6)

    def test_overflow_is_disclosed_not_silently_dropped(self):
        """Rapidly alternating schedules show a clear more-rows label."""

        payload = state()
        payload["b"] = "OX" * 24
        original = Painter.text
        with patch.object(
            Painter, "text", autospec=True, side_effect=original
        ) as writer:
            self.render(payload)
        content = [call.args[3] for call in writer.call_args_list]
        self.assertIn("ЩЕ 43 ІНТЕРВАЛІВ", content)
        self.assertEqual(len(visible_periods(payload["b"])), 5)

    def test_period_labels_fit_without_overlapping_status(self):
        """Long Ukrainian headings must remain inside their columns."""

        painter = Painter()
        left = "08:30 – 16:00 (≈7.5 год.)"
        right = "ПЛАНОВЕ ВІДКЛЮЧЕННЯ"
        self.assertLess(92 + painter.measure(left, 17, "medium"), 578)
        self.assertLess(592 + painter.measure(right, 15), 900)

    def test_current_half_hour_keeps_a_visible_outline(self):
        """The current 16:30 slot must have a white border."""

        original = Painter.rect
        with patch.object(
            Painter, "rect", autospec=True, side_effect=original
        ) as drawing:
            self.render(state())
        current = [
            call
            for call in drawing.call_args_list
            if call.kwargs.get("border") == "#F3F7FF"
        ]
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0].args[1], (690, 494, 753, 527))

    def test_font_packages_and_pillow_are_built_into_docker(self):
        """Avoid creating a build that works only with host font files."""

        source = Path(__file__).resolve().parents[1]
        docker = (source / "Dockerfile").read_text()
        requirements = (source / "requirements.txt").read_text()
        self.assertIn("fonts-inter", docker)
        self.assertIn("fonts-noto-core", docker)
        self.assertIn("TELEGRAM_GRAPHIC_STYLE=v5", docker)
        self.assertIn("Pillow>=11.3.0,<11.4", requirements)


if __name__ == "__main__":
    unittest.main()
