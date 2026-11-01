"""Analytics, export, chart, backup, and dashboard helpers.

The functions in this module do not change monitor state. Database access is
read-only, which makes these helpers safe to reuse from Telegram commands,
HTTP handlers, tests, and future user interfaces.
"""

from __future__ import annotations

import csv
import html
import sqlite3
import struct
import zlib
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CHART_WIDTH = 1000
CHART_HEIGHT = 480
CHART_PADDING = 36
CHART_MIN_SPAN = 10.0
CHART_MARGIN = 5.0
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DARK_PIXEL = b"\x20\x20\x20"
WHITE_PIXEL = b"\xff"

DASHBOARD_STYLE = (
    "body{font-family:sans-serif;max-width:900px;margin:30px auto;"
    "background:#111;color:#eee}"
    "main{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}"
    "section{padding:20px;background:#222;border-radius:12px}"
    "strong{font-size:2rem}"
)


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database in read-only mode."""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def phase_stats(
    path: Path,
    phase: str,
    since: float,
) -> dict[str, float | int | None]:
    """Return count, minimum, maximum, and average for one phase."""

    query = (
        "SELECT COUNT(*),MIN(voltage),MAX(voltage),AVG(voltage) "
        "FROM measurements WHERE phase=? AND ts>=?"
    )
    with closing(connect_ro(path)) as db:
        row = db.execute(query, (phase, since)).fetchone()
    return {
        "count": int(row[0]),
        "min": row[1],
        "max": row[2],
        "avg": row[3],
    }


def quality_stats(
    path: Path,
    phase: str,
    since: float,
    warning_low: float,
    critical_low: float,
    warning_high: float,
) -> dict[str, float | int]:
    """Calculate simple voltage-quality counters for retained samples."""

    query = (
        "SELECT COUNT(*),SUM(voltage<?),SUM(voltage<?),SUM(voltage>?) "
        "FROM measurements WHERE phase=? AND ts>=?"
    )
    params = (warning_low, critical_low, warning_high, phase, since)
    with closing(connect_ro(path)) as db:
        row = db.execute(query, params).fetchone()
    result = _quality_result(row)
    return result


def _quality_result(row: tuple[object, ...]) -> dict[str, float | int]:
    """Convert one SQLite quality row into named values."""

    total = int(row[0] or 0)
    result: dict[str, float | int] = {
        "samples": total,
        "below_warning": int(row[1] or 0),
        "below_critical": int(row[2] or 0),
        "above_warning": int(row[3] or 0),
    }
    bad_samples = result["below_warning"] + result["above_warning"]
    result["bad_pct"] = 100.0 * bad_samples / (total or 1)
    return result


def outage_history(
    path: Path,
    phase: str,
    limit: int = 10,
) -> list[tuple[float, float | None]]:
    """Return the newest outage intervals for one phase."""

    query = (
        "SELECT started_at,ended_at FROM outages WHERE phase=? "
        "ORDER BY started_at DESC LIMIT ?"
    )
    with closing(connect_ro(path)) as db:
        rows = db.execute(query, (phase, limit)).fetchall()
    return [
        (float(start), None if end is None else float(end))
        for start, end in rows
    ]


def series(
    path: Path,
    phase: str,
    since: float,
    limit: int = 3000,
) -> list[tuple[float, float]]:
    """Return chronological voltage points for one phase."""

    query = (
        "SELECT ts,voltage FROM measurements WHERE phase=? AND ts>=? "
        "ORDER BY ts DESC LIMIT ?"
    )
    with closing(connect_ro(path)) as db:
        rows = db.execute(query, (phase, since, limit)).fetchall()
    return [(float(ts), float(voltage)) for ts, voltage in reversed(rows)]


def export_csv(
    path: Path,
    phase: str,
    since: float,
    target: Path,
) -> int:
    """Export phase measurements to CSV and return the row count."""

    query = (
        "SELECT ts,voltage,frequency FROM measurements "
        "WHERE phase=? AND ts>=? ORDER BY ts"
    )
    with closing(connect_ro(path)) as db:
        rows = db.execute(query, (phase, since)).fetchall()
    _write_csv(target, phase, rows)
    return len(rows)


def _write_csv(
    target: Path,
    phase: str,
    rows: list[tuple[object, ...]],
) -> None:
    """Write already-read measurement rows to a CSV file."""

    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "phase", "voltage_v", "frequency_hz"])
        for ts, voltage, frequency in rows:
            writer.writerow([f"{float(ts):.3f}", phase, voltage, frequency])


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk with its size and checksum."""

    body = kind + data
    size = struct.pack(">I", len(data))
    checksum = struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return size + body + checksum


def write_png(
    path: Path,
    width: int,
    height: int,
    pixels: bytearray,
) -> None:
    """Write an RGB byte buffer as a PNG without extra packages."""

    stride = width * 3
    rows = (
        b"\x00" + bytes(pixels[y * stride : (y + 1) * stride])
        for y in range(height)
    )
    raw = b"".join(rows)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = PNG_SIGNATURE + png_chunk(b"IHDR", header)
    data += png_chunk(b"IDAT", zlib.compress(raw, 9))
    data += png_chunk(b"IEND", b"")
    path.write_bytes(data)


def set_pixel(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
) -> None:
    """Set one chart pixel when its coordinates are inside the image."""

    if not (0 <= x < width and 0 <= y < height):
        return
    offset = (y * width + x) * 3
    pixels[offset : offset + 3] = DARK_PIXEL


def line(
    pixels: bytearray,
    width: int,
    height: int,
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    """Draw one straight line between two integer pixel coordinates."""

    x_pos, y_pos = start
    x_end, y_end = end
    x_delta = abs(x_end - x_pos)
    x_step = 1 if x_pos < x_end else -1
    y_delta = -abs(y_end - y_pos)
    y_step = 1 if y_pos < y_end else -1
    error = x_delta + y_delta
    while True:
        set_pixel(pixels, width, height, x_pos, y_pos)
        if x_pos == x_end and y_pos == y_end:
            return
        error, x_pos, y_pos = _line_step(
            error,
            x_delta,
            y_delta,
            x_step,
            y_step,
            x_pos,
            y_pos,
        )


def _line_step(
    error: int,
    x_delta: int,
    y_delta: int,
    x_step: int,
    y_step: int,
    x_pos: int,
    y_pos: int,
) -> tuple[int, int, int]:
    """Calculate one Bresenham line step."""

    twice_error = 2 * error
    if twice_error >= y_delta:
        error += y_delta
        x_pos += x_step
    if twice_error <= x_delta:
        error += x_delta
        y_pos += y_step
    return error, x_pos, y_pos


def chart_png(
    path: Path,
    phase: str,
    hours: int,
    target: Path,
) -> int:
    """Render a compact voltage chart and return the point count."""

    since = datetime.now().timestamp() - hours * 3600
    points = series(path, phase, since)
    pixels = bytearray(WHITE_PIXEL * CHART_WIDTH * CHART_HEIGHT * 3)
    if len(points) >= 2:
        coordinates = _chart_coordinates(points)
        for start, end in zip(
            coordinates,
            coordinates[1:],
            strict=False,
        ):
            line(pixels, CHART_WIDTH, CHART_HEIGHT, start, end)
    write_png(target, CHART_WIDTH, CHART_HEIGHT, pixels)
    return len(points)


def _chart_coordinates(
    points: list[tuple[float, float]],
) -> list[tuple[int, int]]:
    """Convert voltage points into chart pixel coordinates."""

    voltages = [point[1] for point in points]
    low, high = _chart_range(voltages)
    width = CHART_WIDTH - CHART_PADDING * 2
    height = CHART_HEIGHT - CHART_PADDING * 2
    divisor = max(1, len(points) - 1)
    coordinates = []
    for index, (_, voltage) in enumerate(points):
        x_pos = CHART_PADDING + int(index * width / divisor)
        ratio = (voltage - low) / (high - low)
        y_pos = CHART_HEIGHT - CHART_PADDING - int(ratio * height)
        coordinates.append((x_pos, y_pos))
    return coordinates


def _chart_range(voltages: list[float]) -> tuple[float, float]:
    """Return a readable vertical voltage range for a chart."""

    low = min(voltages)
    high = max(voltages)
    span = max(CHART_MIN_SPAN, high - low)
    low -= CHART_MARGIN
    high = low + span + CHART_MARGIN * 2
    return low, high


def backup_health(directory: Path) -> dict[str, object]:
    """Return information about the newest compressed SQLite backup."""

    files = sorted(
        directory.glob("voltage-*.sqlite3.gz"),
        key=lambda path: path.stat().st_mtime,
    )
    if not files:
        return {"status": "missing"}
    newest = files[-1]
    stat = newest.stat()
    return {
        "status": "ok",
        "name": newest.name,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }


def format_stamp(timestamp: float, timezone: str) -> str:
    """Format a timestamp in the configured timezone."""

    value = datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
    return value.strftime("%d.%m %H:%M")


def dashboard_html(
    title: str,
    rows: list[dict[str, object]],
) -> str:
    """Render a small local dashboard without extra web packages."""

    safe_title = html.escape(title)
    cards = "".join(_dashboard_card(row) for row in rows)
    return (
        "<!doctype html><meta charset=utf-8>"
        "<meta http-equiv=refresh content=30>"
        f"<title>{safe_title}</title>"
        f"<style>{DASHBOARD_STYLE}</style>"
        f"<h1>{safe_title}</h1><main>{cards}</main>"
    )


def _dashboard_card(row: dict[str, object]) -> str:
    """Render one escaped dashboard phase card."""

    phase = html.escape(str(row["phase"]))
    state = html.escape(str(row["state"]))
    voltage_value = row.get("voltage")
    voltage = "—" if voltage_value is None else f"{float(voltage_value):.1f} V"
    return (
        f"<section><h2>{phase}</h2><strong>{voltage}</strong>"
        f"<p>{state}</p></section>"
    )
