"""Advanced analytics and export helpers for the Shelly voltage monitor."""

from __future__ import annotations

import csv
import html
import math
import sqlite3
import struct
import zlib
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database read-only."""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=10)


def phase_stats(
    path: Path, phase: str, since: float, until: float | None = None
) -> dict[str, float | int | None]:
    """Return observed phase samples within a bounded time interval."""

    with closing(connect_ro(path)) as db:
        row = db.execute(
            "SELECT COUNT(*),MIN(voltage),MAX(voltage),AVG(voltage) "
            "FROM measurements WHERE phase=? AND ts>=? "
            "AND (? IS NULL OR ts<=?)",
            (phase, since, until, until),
        ).fetchone()
    return {"count": int(row[0]), "min": row[1], "max": row[2], "avg": row[3]}


def quality_stats(
    path: Path,
    phase: str,
    since: float,
    warning_low: float,
    critical_low: float,
    warning_high: float,
) -> dict[str, float | int]:
    """Estimate voltage-quality percentages from retained samples."""

    with closing(connect_ro(path)) as db:
        row = db.execute(
            "SELECT COUNT(*),SUM(voltage<?),SUM(voltage<?),SUM(voltage>?) "
            "FROM measurements WHERE phase=? AND ts>=?",
            (warning_low, critical_low, warning_high, phase, since),
        ).fetchone()
    total = int(row[0] or 0)
    result = {
        "samples": total,
        "below_warning": int(row[1] or 0),
        "below_critical": int(row[2] or 0),
        "above_warning": int(row[3] or 0),
    }
    denom = total or 1
    result["bad_pct"] = (
        100.0 * (result["below_warning"] + result["above_warning"]) / denom
    )
    return result


def weekly_time_categories(
    path: Path, phase: str, since: float, until: float, low: float, high: float
) -> dict[str, float]:
    """Count observed threshold time without filling long sample gaps."""

    observed = below = above = 0.0
    previous: tuple[float, float] | None = None
    with closing(connect_ro(path)) as db:
        rows = db.execute(
            "SELECT ts,voltage FROM measurements "
            "WHERE phase=? AND ts>=? AND ts<=? ORDER BY ts",
            (phase, since, until),
        )
        for stamp, voltage in rows:
            try:
                point = (float(stamp), float(voltage))
            except (TypeError, ValueError, OverflowError):
                previous = None
                continue
            if not all(math.isfinite(v) for v in point):
                previous = None
                continue
            if previous:
                gap = point[0] - previous[0]
                if 0 < gap <= 30:
                    observed += gap
                    below += gap if previous[1] <= low else 0.0
                    above += gap if previous[1] >= high else 0.0
            previous = point
    return {
        "observed": observed,
        "unknown": max(0.0, until - since - observed),
        "below": below,
        "above": above,
    }


def weekly_quality_stats(
    path: Path, phase: str, since: float, until: float, low: float, high: float
) -> dict[str, float | int | None]:
    """Summarize one phase and exclude gaps from time estimates."""

    with closing(connect_ro(path)) as db:
        row = db.execute(
            "SELECT COUNT(*),MIN(voltage),MAX(voltage),AVG(voltage) "
            "FROM measurements "
            "WHERE phase=? AND ts>=? AND ts<=?",
            (phase, since, until),
        ).fetchone()
    coverage = weekly_time_categories(path, phase, since, until, low, high)
    return {
        "count": int(row[0]),
        "min": row[1],
        "max": row[2],
        "avg": row[3],
        **coverage,
    }


def weekly_connection_stats(
    path: Path, phase: str, since: float, until: float
) -> dict[str, float | int]:
    """Count overlapping lost-connection periods in a chosen week."""

    with closing(connect_ro(path)) as db:
        rows = db.execute(
            "SELECT started_at,ended_at FROM outages "
            "WHERE phase=? AND started_at<? "
            "AND (ended_at IS NULL OR ended_at>?)",
            (phase, until, since),
        ).fetchall()
    total = 0.0
    count = 0
    for start, end in rows:
        lower = max(since, float(start))
        upper = until if end is None else min(until, float(end))
        if upper > lower:
            total += upper - lower
            count += 1
    return {"count": count, "duration": total}


def outage_history(
    path: Path, phase: str, limit: int = 10
) -> list[tuple[float, float | None]]:
    """Return newest outage intervals for one phase."""

    with closing(connect_ro(path)) as db:
        rows = db.execute(
            "SELECT started_at,ended_at FROM outages WHERE phase=? "
            "ORDER BY started_at DESC LIMIT ?",
            (phase, limit),
        ).fetchall()
    return [(float(a), None if b is None else float(b)) for a, b in rows]


def series(
    path: Path, phase: str, since: float, limit: int = 3000
) -> list[tuple[float, float]]:
    """Return downsample-ready voltage points for one phase."""

    with closing(connect_ro(path)) as db:
        rows = db.execute(
            "SELECT ts,voltage FROM measurements WHERE phase=? AND ts>=? "
            "ORDER BY ts DESC LIMIT ?",
            (phase, since, limit),
        ).fetchall()
    return [(float(a), float(b)) for a, b in reversed(rows)]


def export_csv(path: Path, phase: str, since: float, target: Path) -> int:
    """Export phase measurements to CSV and return the row count."""

    with (
        closing(connect_ro(path)) as db,
        target.open("w", newline="") as handle,
    ):
        rows = db.execute(
            "SELECT ts,voltage,frequency FROM measurements "
            "WHERE phase=? AND ts>=? "
            "ORDER BY ts",
            (phase, since),
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
    return (
        struct.pack(">I", len(data))
        + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int, pixels: bytearray) -> None:
    """Write an RGB byte buffer as a PNG without external dependencies."""

    stride = width * 3
    raw = b"".join(
        b"\x00" + bytes(pixels[y * stride : (y + 1) * stride])
        for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
    data += png_chunk(b"IDAT", zlib.compress(raw, 9)) + png_chunk(b"IEND", b"")
    path.write_bytes(data)


def set_pixel(
    pixels: bytearray, width: int, height: int, x: int, y: int
) -> None:
    """Set one chart pixel when coordinates are in bounds."""

    if not (0 <= x < width and 0 <= y < height):
        return
    offset = (y * width + x) * 3
    pixels[offset : offset + 3] = b"\x20\x20\x20"


def line(
    pixels: bytearray,
    width: int,
    height: int,
    a: tuple[int, int],
    b: tuple[int, int],
) -> None:
    """Draw one integer line segment with Bresenham stepping."""

    x0, y0 = a
    x1, y1 = b
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        set_pixel(pixels, width, height, x0, y0)
        if x0 == x1 and y0 == y1:
            return
        twice = 2 * err
        if twice >= dy:
            err += dy
            x0 += sx
        if twice <= dx:
            err += dx
            y0 += sy


def chart_png(path: Path, phase: str, hours: int, target: Path) -> int:
    """Render a compact PNG voltage chart and return point count."""

    points = series(path, phase, datetime.now().timestamp() - hours * 3600)
    width, height, pad = 1000, 480, 36
    pixels = bytearray(b"\xff" * width * height * 3)
    if len(points) < 2:
        write_png(target, width, height, pixels)
        return len(points)
    volts = [p[1] for p in points]
    lo, hi = min(volts), max(volts)
    span = max(10.0, hi - lo)
    lo -= 5
    hi = lo + span + 10
    coords = []
    for index, (_, voltage) in enumerate(points):
        x = pad + int(index * (width - pad * 2) / max(1, len(points) - 1))
        y = height - pad - int((voltage - lo) * (height - pad * 2) / (hi - lo))
        coords.append((x, y))
    for a, b in zip(coords, coords[1:]):
        line(pixels, width, height, a, b)
    write_png(target, width, height, pixels)
    return len(points)


def backup_health(directory: Path) -> dict[str, object]:
    """Return metadata for the newest compressed SQLite backup."""

    files = sorted(
        directory.glob("voltage-*.sqlite3.gz"), key=lambda p: p.stat().st_mtime
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


def format_stamp(ts: float, timezone: str) -> str:
    """Format a timestamp in the configured timezone."""

    return datetime.fromtimestamp(ts, ZoneInfo(timezone)).strftime(
        "%d.%m %H:%M"
    )


def dashboard_sample_age(row: dict[str, object]) -> str:
    """Describe the last sample without treating it as current voltage."""

    age = row.get("sample_age_seconds")
    if age is None:
        return "No valid measurement yet"
    seconds = max(0, int(float(age)))
    if seconds < 60:
        unit = "second" if seconds == 1 else "seconds"
        return f"Last valid sample: {seconds} {unit} ago"
    minutes = seconds // 60
    unit = "minute" if minutes == 1 else "minutes"
    return f"Last valid sample: {minutes} {unit} ago"


DASHBOARD_CSS = """
:root{color-scheme:dark;font-family:system-ui,sans-serif}
*{box-sizing:border-box}
body{margin:0;background:#0b1220;color:#f0f5fc;line-height:1.5}
.shell{max-width:1100px;margin:auto;padding:clamp(18px,4vw,46px)}
.eyebrow{color:#8fb9db;text-transform:uppercase;letter-spacing:.13em}
h1{font-size:clamp(1.7rem,4vw,2.5rem);margin:.3em 0}
.intro{color:#a9b7c9;margin-bottom:28px}
main{display:grid;gap:18px}
main{grid-template-columns:repeat(auto-fit,minmax(235px,1fr))}
.phase-card{padding:24px;border:1px solid #2a3b50;border-radius:18px}
.phase-card{background:#172337;box-shadow:0 12px 28px #0002}
.phase-head{display:flex;align-items:center;gap:12px}
.phase-head{justify-content:space-between}
h2{font-size:1.2rem;margin:0}
.status{font-size:.78rem;padding:5px 10px;border-radius:20px;}
.status{white-space:nowrap}
.status-online{background:#163b30;color:#a4f4c5}
.status-recovering{background:#263a54;color:#c7e1ff}
.status-offline{background:#4a3019;color:#ffce8c}
.status-stale{background:#394252;color:#e1e8f2}
.status-unknown,.status-unconfigured{background:#303847;color:#c5d0dd}
.reading{font-size:clamp(2.4rem,7vw,3.6rem);font-weight:750;margin:24px 0 0}
.unit{font-size:1rem;color:#adbcd0;margin-left:8px}
.frequency{color:#d5e4f4;margin:8px 0}
.sample-age{color:#a9b7c9;font-size:.85rem;margin:18px 0 0}
footer{margin-top:30px;color:#a9b7c9;font-size:.86rem}
@media(max-width:480px){.phase-card{padding:18px}.phase-head{flex-wrap:wrap}}
"""


def dashboard_card(row: dict[str, object]) -> str:
    """Render one accessible phase card with escaped data."""

    phase = html.escape(str(row["phase"]), quote=True)
    state = str(row.get("state", "unknown")).lower()
    labels = {
        "online": "Online",
        "recovering": "Recovering",
        "offline": "Shelly offline",
        "stale": "Reading stale",
        "not configured": "Not configured",
    }
    label = labels.get(state, "Unknown")
    style = state.replace(" ", "-") if state in labels else "unknown"
    value = row.get("voltage")
    voltage = "—" if value is None else f"{float(value):.1f}"
    frequency = row.get("frequency") if value is not None else None
    frequency_text = "—" if frequency is None else f"{float(frequency):.2f} Hz"
    age = html.escape(dashboard_sample_age(row), quote=True)
    return (
        f'<article class="phase-card"><header class="phase-head">'
        f'<h2>{phase}</h2><span class="status status-{style}">{label}</span>'
        f'</header><div class="reading">{voltage}<span class="unit">V</span>'
        f'</div><p class="frequency">Frequency: {frequency_text}</p>'
        f'<p class="sample-age">{age}</p></article>'
    )


def dashboard_html(title: str, rows: list[dict[str, object]]) -> str:
    """Render responsive read-only HTML without external dependencies."""

    heading = html.escape(title, quote=True)
    cards = "".join(dashboard_card(row) for row in rows)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="30">'
        f"<title>{heading}</title><style>{DASHBOARD_CSS}</style></head>"
        f'<body><div class="shell"><header><p class="eyebrow">'
        f'Local read-only monitoring</p><h1>{heading}</h1><p class="intro">'
        "Only fresh measurements are displayed as live voltage."
        f"</p></header><main>{cards}</main><footer>"
        "Shelly offline means connectivity is unavailable, "
        "not confirmed mains power loss. Refreshes every 30 seconds."
        "</footer></div></body></html>"
    )
