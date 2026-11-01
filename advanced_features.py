"""Advanced analytics and export helpers for the Shelly voltage monitor."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
import html
import io
import sqlite3
import struct
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database read-only."""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def phase_stats(path: Path, phase: str, since: float) -> dict[str, float | int | None]:
    """Return summary statistics for one phase since a timestamp."""

    with connect_ro(path) as db:
        row = db.execute(
            "SELECT COUNT(*),MIN(voltage),MAX(voltage),AVG(voltage) "
            "FROM measurements WHERE phase=? AND ts>=?",
            (phase, since),
        ).fetchone()
    return {"count": int(row[0]), "min": row[1], "max": row[2], "avg": row[3]}


def quality_stats(
    path: Path, phase: str, since: float, warning_low: float,
    critical_low: float, warning_high: float
) -> dict[str, float | int]:
    """Estimate voltage-quality percentages from retained samples."""

    with connect_ro(path) as db:
        row = db.execute(
            "SELECT COUNT(*),SUM(voltage<?),SUM(voltage<?),SUM(voltage>?) "
            "FROM measurements WHERE phase=? AND ts>=?",
            (warning_low, critical_low, warning_high, phase, since),
        ).fetchone()
    total = int(row[0] or 0)
    result = {"samples": total, "below_warning": int(row[1] or 0),
              "below_critical": int(row[2] or 0), "above_warning": int(row[3] or 0)}
    denom = total or 1
    result["bad_pct"] = 100.0 * (result["below_warning"] + result["above_warning"]) / denom
    return result


def outage_history(path: Path, phase: str, limit: int = 10) -> list[tuple[float, float | None]]:
    """Return newest outage intervals for one phase."""

    with connect_ro(path) as db:
        rows = db.execute(
            "SELECT started_at,ended_at FROM outages WHERE phase=? "
            "ORDER BY started_at DESC LIMIT ?", (phase, limit)
        ).fetchall()
    return [(float(a), None if b is None else float(b)) for a, b in rows]


def series(path: Path, phase: str, since: float, limit: int = 3000) -> list[tuple[float, float]]:
    """Return downsample-ready voltage points for one phase."""

    with connect_ro(path) as db:
        rows = db.execute(
            "SELECT ts,voltage FROM measurements WHERE phase=? AND ts>=? "
            "ORDER BY ts DESC LIMIT ?", (phase, since, limit)
        ).fetchall()
    return [(float(a), float(b)) for a, b in reversed(rows)]


def export_csv(path: Path, phase: str, since: float, target: Path) -> int:
    """Export phase measurements to CSV and return the row count."""

    with connect_ro(path) as db, target.open("w", newline="") as handle:
        rows = db.execute(
            "SELECT ts,voltage,frequency FROM measurements WHERE phase=? AND ts>=? "
            "ORDER BY ts", (phase, since)
        )
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "phase", "voltage_v", "frequency_hz"])
        count = 0
        for ts, voltage, frequency in rows:
            writer.writerow([f"{float(ts):.3f}", phase, voltage, frequency])
            count += 1
    return count


def png_chunk(kind: bytes, data: bytes) -> bytes:
    """Encode one PNG chunk."""

    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    """Write an RGB byte buffer as a PNG without external dependencies."""

    stride = width * 3
    raw = b"".join(b"\x00" + bytes(pixels[y * stride:(y + 1) * stride]) for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
    data += png_chunk(b"IDAT", zlib.compress(raw, 9)) + png_chunk(b"IEND", b"")
    path.write_bytes(data)


def set_pixel(pixels: bytearray, width: int, height: int, x: int, y: int) -> None:
    """Set one chart pixel when coordinates are in bounds."""

    if not (0 <= x < width and 0 <= y < height):
        return
    offset = (y * width + x) * 3
    pixels[offset:offset + 3] = b"\x20\x20\x20"


def line(pixels: bytearray, width: int, height: int, a: tuple[int, int], b: tuple[int, int]) -> None:
    """Draw one integer line segment with Bresenham stepping."""

    x0, y0 = a; x1, y1 = b
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        set_pixel(pixels, width, height, x0, y0)
        if x0 == x1 and y0 == y1:
            return
        twice = 2 * err
        if twice >= dy: err += dy; x0 += sx
        if twice <= dx: err += dx; y0 += sy


def chart_png(path: Path, phase: str, hours: int, target: Path) -> int:
    """Render a compact PNG voltage chart and return point count."""

    points = series(path, phase, datetime.now().timestamp() - hours * 3600)
    width, height, pad = 1000, 480, 36
    pixels = bytearray(b"\xff" * width * height * 3)
    if len(points) < 2:
        write_png(target, width, height, pixels); return len(points)
    volts = [p[1] for p in points]; lo, hi = min(volts), max(volts)
    span = max(10.0, hi - lo); lo -= 5; hi = lo + span + 10
    coords = []
    for index, (_, voltage) in enumerate(points):
        x = pad + int(index * (width - pad * 2) / max(1, len(points) - 1))
        y = height - pad - int((voltage - lo) * (height - pad * 2) / (hi - lo))
        coords.append((x, y))
    for a, b in zip(coords, coords[1:]): line(pixels, width, height, a, b)
    write_png(target, width, height, pixels)
    return len(points)


def backup_health(directory: Path) -> dict[str, object]:
    """Return metadata for the newest compressed SQLite backup."""

    files = sorted(directory.glob("voltage-*.sqlite3.gz"), key=lambda p: p.stat().st_mtime)
    if not files:
        return {"status": "missing"}
    newest = files[-1]; stat = newest.stat()
    return {"status": "ok", "name": newest.name, "mtime": stat.st_mtime, "size": stat.st_size}


def format_stamp(ts: float, timezone: str) -> str:
    """Format a timestamp in the configured timezone."""

    return datetime.fromtimestamp(ts, ZoneInfo(timezone)).strftime("%d.%m %H:%M")


def dashboard_html(title: str, rows: list[dict[str, object]]) -> str:
    """Render a dependency-free local dashboard."""

    cards = []
    for row in rows:
        name = html.escape(str(row["phase"])); state = html.escape(str(row["state"]))
        voltage = "—" if row.get("voltage") is None else f"{float(row['voltage']):.1f} V"
        cards.append(f"<section><h2>{name}</h2><strong>{voltage}</strong><p>{state}</p></section>")
    body = "".join(cards)
    return (
        "<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=30>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:sans-serif;max-width:900px;margin:30px auto;"
        "background:#111;color:#eee}main{display:grid;grid-template-columns:"
        "repeat(3,1fr);gap:16px}section{padding:20px;background:#222;"
        "border-radius:12px}strong{font-size:2rem}</style>"
        f"<h1>{html.escape(title)}</h1><main>{body}</main>"
    )
