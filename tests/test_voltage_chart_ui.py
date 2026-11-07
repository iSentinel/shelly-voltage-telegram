"""Deterministic PNG chart and coverage tests without Telegram requests."""

from __future__ import annotations

import math
import sqlite3
import struct
import tempfile
import time
import unittest
import zlib
from pathlib import Path

from test_app import APP

from advanced_features import chart_png, series


def read_png_pixels(path: Path) -> tuple[int, int, bytes]:
    """Decode the project's no-filter RGB PNG without other libraries."""

    content = path.read_bytes()
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("not a PNG")
    pos, payload = 8, bytearray()
    while pos < len(content):
        length = struct.unpack_from(">I", content, pos)[0]
        tag = content[pos + 4 : pos + 8]
        if tag == b"IDAT":
            payload.extend(content[pos + 8 : pos + 8 + length])
        pos += 12 + length
    width, height = struct.unpack_from(">II", content, 16)
    raw = zlib.decompress(payload)
    pixels = b"".join(
        raw[index + 1 : index + 1 + width * 3]
        for index in range(0, len(raw), width * 3 + 1)
    )
    assert len(pixels) == width * height * 3
    return width, height, pixels


def pixel_at(pixels: bytes, width: int, x: int, y: int) -> bytes:
    """Read a single RGB pixel from an existing PNG buffer."""

    offset = (y * width + x) * 3
    return pixels[offset : offset + 3]


class VoltageChartUiTests(unittest.TestCase):
    """Check original-range coverage, gap safety, and visual axis markers."""

    def setUp(self):
        """Create a disposable database that cannot affect live Shelly."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "samples.sqlite3"
        self.store = APP.Storage(self.db, "Europe/Kyiv")
        self.store.initialize()
        self.now = time.time()

    def test_all_24h_measured_data_is_represented_without_latest_only_cut(
        self,
    ):
        """Preserve early extreme values when more than 3000 samples exist."""

        start = self.now - 24 * 3600
        for index in range(3601):
            voltage = 119.0 if index == 7 else 228.0
            if index == 24:
                voltage = 260.0
            self.store.add(start + index * 23.99, voltage, 50.0)
        points = series(self.db, "L1", start, until=self.now)
        self.assertLessEqual(len(points), 3000)
        self.assertGreater(points[0][0], start - 1)
        self.assertLess(points[0][0], start + 30)
        self.assertAlmostEqual(min(v for _, v in points), 119.0)
        self.assertAlmostEqual(max(v for _, v in points), 260.0)
        self.assertGreater(points[-1][0], self.now - 60)

    def test_chart_has_grid_labels_and_high_contrast_voltage_line(self):
        """Rendered PNG exposes visible time/voltage grid and danger curve."""

        for i in range(50):
            value = 122.0 if i > 44 else 222.0
            self.store.add(self.now - (49 - i) * 20, value, 50.0)
        filename = self.root / "graph.png"
        count = chart_png(self.db, "L1", 24, filename, warning_low=180.0)
        width, height, pixels = read_png_pixels(filename)
        self.assertGreater(count, 10)
        self.assertLessEqual(count, 50)
        self.assertEqual((width, height), (1000, 560))
        self.assertIn(b"\x25\x63\xeb", pixels)
        self.assertIn(b"\xdc\x26\x26", pixels)
        self.assertIn(b"\xf9\x73\x16", pixels)
        self.assertIn(b"\x33\x47\x56", pixels)
        self.assertNotEqual(pixel_at(pixels, width, 12, 250), b"\xff" * 3)

    def test_long_connection_gap_is_not_drawn_as_voltage_transition(self):
        """A missing three-hour history segment must remain disconnected."""

        begin = self.now - 4 * 3600
        self.store.add(begin + 60, 225.0, 50.0)
        self.store.add(begin + 3 * 3600, 125.0, 50.0)
        target = self.root / "gap.png"
        chart_png(self.db, "L1", 4, target, warning_low=180.0)
        width, _, pixels = read_png_pixels(target)
        ink = {b"\x25\x63\xeb", b"\xdc\x26\x26"}
        center = [pixel_at(pixels, width, 520, y) for y in range(108, 485)]
        self.assertTrue(all(point not in ink for point in center))

    def test_empty_graph_has_no_invented_voltage_values(self):
        """The PNG must render a no-data state when SQLite has no readings."""

        dest = self.root / "empty.png"
        count = chart_png(self.db, "L1", 24, dest)
        width, height, pixels = read_png_pixels(dest)
        self.assertEqual(count, 0)
        self.assertEqual((width, height), (1000, 560))
        self.assertNotIn(b"\x25\x63\xeb", pixels)
        self.assertIn(b"\x94\xa3\xb8", pixels)

    def test_invalid_nonfinite_values_are_excluded_from_chart(self):
        """Old SQLite corruption must not create NaN chart bounds."""

        with sqlite3.connect(self.db) as db:
            db.execute(
                "insert into measurements (ts,voltage,frequency,phase) "
                "values (?,?,?,?)",
                (self.now - 100, float("inf"), 50.0, "L1"),
            )
        self.store.add(self.now - 50, 214.0, 50.0)
        points = series(self.db, "L1", self.now - 3600, until=self.now)
        self.assertEqual(len(points), 1)
        self.assertTrue(all(math.isfinite(v) for _, v in points))
        self.assertEqual(
            chart_png(self.db, "L1", 1, self.root / "safe.png"), 1
        )


if __name__ == "__main__":
    unittest.main()
