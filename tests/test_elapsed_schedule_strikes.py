"""Verify elapsed YASNO hours are crossed out without striking live time."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops

from telegram_card_png import (
    render_card_png,
    strike_past_diagonal,
    strike_text,
)
from telegram_card_v5 import Painter

KYIV = ZoneInfo("Europe/Kyiv")
BARS = "X" * 8 + "O" * 9 + "X" * 15 + "O" * 3 + "X" * 9 + "O" * 4


def schedule_state(now: float) -> dict:
    """Provide a single-day, fresh synthetic meter and YASNO forecast."""

    local = datetime.fromtimestamp(now, KYIV)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "schema": 1,
        "l": "uk",
        "s": "online",
        "v": 223.1,
        "f": 50.0,
        "t": now - 3,
        "b": BARS,
        "d": local.date().isoformat(),
        "u": now - 90,
        "e": (midnight + timedelta(days=1)).timestamp(),
        "g": "10.1",
        "src": "yasno",
    }


class ElapsedScheduleStrikeTests(unittest.TestCase):
    """Test both image styles without a Telegram call or live database."""

    def setUp(self) -> None:
        """Create disposable PNG output using the bundled rendering fonts."""

        temp = tempfile.TemporaryDirectory(prefix="shelly-strike-qa-")
        self.addCleanup(temp.cleanup)
        self.target = Path(temp.name) / "card.png"

    def make_card(self, style: str, hour: int, minute: int) -> None:
        """Render a dated fixture with a fresh schedule and fixed Kyiv time."""

        when = datetime(2026, 11, 18, hour, minute, tzinfo=KYIV)
        now = when.timestamp()
        with patch.dict(os.environ, {"TELEGRAM_GRAPHIC_STYLE": style}):
            report = render_card_png(schedule_state(now), self.target, now)
        self.assertTrue(report["forecast_visible"])
        self.assertTrue(self.target.is_file())

    def v5_strikes(self, hour: int, minute: int) -> tuple[int, int]:
        """Capture completed v5 hour and forecast row strokes."""

        original = Painter.strike
        with patch.object(
            Painter, "strike", autospec=True, side_effect=original
        ) as stroked:
            self.make_card("v5", hour, minute)
        positions = [call.args[2] for call in stroked.call_args_list]
        return sum(y < 580 for y in positions), sum(
            y >= 608 for y in positions
        )

    def v4_strikes(self, hour: int, minute: int) -> tuple[int, int]:
        """Capture fallback bitmap clock strikes, not tile diagonals."""

        with patch(
            "telegram_card_png.strike_text", wraps=strike_text
        ) as stroked:
            self.make_card("v4", hour, minute)
        placements = [call.args[3] for call in stroked.call_args_list]
        return sum(y < 580 for y in placements), sum(
            y >= 588 for y in placements
        )

    def test_1645_strikes_only_completed_hours_and_windows(self) -> None:
        """16:00–17:30 remains active while three earlier rows are crossed."""

        for name, capture in (
            ("v5", self.v5_strikes),
            ("v4", self.v4_strikes),
        ):
            with self.subTest(style=name):
                self.assertEqual(capture(16, 45), (16, 3))

    def test_ending_boundary_changes_strikes_at_0400(self) -> None:
        """Mark a 00:00–04:00 interval only once its end has arrived."""

        for name, capture in (
            ("v5", self.v5_strikes),
            ("v4", self.v4_strikes),
        ):
            with self.subTest(style=name):
                self.assertEqual(capture(3, 59), (3, 0))
                self.assertEqual(capture(4, 0), (4, 1))

    def test_today_midnight_and_last_hour_remain_active(self) -> None:
        """Midnight crosses nothing; the 23:00 hour is not fully complete."""

        for name, capture in (
            ("v5", self.v5_strikes),
            ("v4", self.v4_strikes),
        ):
            with self.subTest(style=name):
                self.assertEqual(capture(0, 0), (0, 0))
                row_count = 5 if name == "v5" else 4
                self.assertEqual(capture(23, 59), (23, row_count))

    def diagonal_count(self, style: str, hour: int, minute: int) -> int:
        """Count diagonals without changing the real rendering behavior."""

        if style == "v5":
            original = Painter.diagonal_past_slot
            stroke = patch.object(
                Painter,
                "diagonal_past_slot",
                autospec=True,
                side_effect=original,
            )
        else:
            stroke = patch(
                "telegram_card_png.strike_past_diagonal",
                wraps=strike_past_diagonal,
            )
        with stroke as drawn:
            self.make_card(style, hour, minute)
        return drawn.call_count

    def test_diagonal_per_completed_half_hour(self) -> None:
        """A slot is crossed only after its own half-hour finishes."""

        cases = (
            (0, 0, 0),
            (3, 29, 6),
            (3, 30, 7),
            (3, 59, 7),
            (4, 0, 8),
            (14, 10, 28),
            (14, 30, 29),
            (23, 59, 47),
        )
        for style in ("v5", "v4"):
            for hour, minute, expected in cases:
                with self.subTest(
                    style=style, clock=f"{hour:02d}:{minute:02d}"
                ):
                    self.assertEqual(
                        self.diagonal_count(style, hour, minute), expected
                    )

    def test_diagonals_touch_only_finished_slot_pixels(self) -> None:
        """Verify actual PNG diagonal pixels do not alter active tiles."""

        geometry = {
            "v5": (484, 413, 65, 35),
            "v4": (480, 383, 66, 40),
        }
        for style, (x, y, width, height) in geometry.items():
            with self.subTest(style=style):
                if style == "v5":
                    disabled = patch.object(
                        Painter, "diagonal_past_slot", return_value=None
                    )
                else:
                    disabled = patch(
                        "telegram_card_png.strike_past_diagonal",
                        return_value=None,
                    )
                with disabled:
                    self.make_card(style, 3, 30)
                with Image.open(self.target) as image:
                    baseline = image.copy()
                self.make_card(style, 3, 30)
                with Image.open(self.target) as image:
                    delta = ImageChops.difference(baseline, image)
                self.assertIsNotNone(
                    delta.crop((x, y, x + width, y + height)).getbbox()
                )
                self.assertIsNone(
                    delta.crop(
                        (x + width, y, x + 2 * width, y + height)
                    ).getbbox()
                )
                self.assertIsNone(delta.crop((0, 0, 960, 333)).getbbox())

    def strike_pixel_difference(self, style: str):
        """Return only pixels affected by crossing elapsed labels."""

        if style == "v5":
            disabled = patch.object(Painter, "strike", return_value=None)
        else:
            disabled = patch(
                "telegram_card_png.strike_text", return_value=None
            )
        with disabled:
            self.make_card(style, 16, 45)
        with Image.open(self.target) as image:
            baseline = image.copy()
        self.make_card(style, 16, 45)
        with Image.open(self.target) as image:
            actual = image.copy()
        return ImageChops.difference(baseline, actual)

    def test_strikes_change_only_elapsed_image_regions(self) -> None:
        """Compare real PNG pixels, not only renderer method calls."""

        areas = {
            "v5": {
                "past_hour": (64, 413, 194, 448),
                "current_hour": (624, 493, 754, 528),
                "future_hour": (64, 533, 194, 568),
                "past_row": (92, 608, 375, 632),
                "current_row": (92, 680, 375, 704),
            },
            "v4": {
                "past_hour": (60, 383, 192, 423),
                "current_hour": (620, 473, 752, 513),
                "future_hour": (60, 518, 192, 558),
                "past_row": (104, 588, 575, 618),
                "current_row": (104, 678, 575, 708),
            },
        }
        for style, regions in areas.items():
            with self.subTest(style=style):
                delta = self.strike_pixel_difference(style)
                self.assertIsNotNone(delta.getbbox())
                for part in ("past_hour", "past_row"):
                    with self.subTest(style=style, region=part):
                        self.assertIsNotNone(
                            delta.crop(regions[part]).getbbox()
                        )
                for part in ("current_hour", "future_hour", "current_row"):
                    with self.subTest(style=style, region=part):
                        self.assertIsNone(delta.crop(regions[part]).getbbox())
                self.assertIsNone(delta.crop((0, 0, 960, 333)).getbbox())


if __name__ == "__main__":
    unittest.main()
