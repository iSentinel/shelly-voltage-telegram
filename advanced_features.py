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


def bucket_extremes(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Keep first/last/min/max points within a time bucket."""

    if not points:
        return []
    smallest = min(points, key=lambda row: row[1])
    biggest = max(points, key=lambda row: row[1])
    return sorted(set((points[0], smallest, biggest, points[-1])))


def series(
    path: Path,
    phase: str,
    since: float,
    limit: int = 3000,
    until: float | None = None,
) -> list[tuple[float, float]]:
    """Keep the entire time range while retaining extreme readings."""

    if limit < 4:
        raise ValueError("series limit must be at least four")
    end = datetime.now().timestamp() if until is None else until
    if end <= since:
        return []
    buckets = max(1, limit // 4)
    window = (end - since) / buckets
    result: list[tuple[float, float]] = []
    current = -1
    group: list[tuple[float, float]] = []
    with closing(connect_ro(path)) as db:
        rows = db.execute(
            "SELECT ts,voltage FROM measurements WHERE phase=? "
            "AND ts>=? AND ts<=? ORDER BY ts",
            (phase, since, end),
        )
        for ts, voltage in rows:
            ts, voltage = float(ts), float(voltage)
            if not (math.isfinite(ts) and math.isfinite(voltage)):
                continue
            bucket = min(buckets - 1, int((ts - since) / window))
            if bucket != current and group:
                result.extend(bucket_extremes(group))
                group = []
            current = bucket
            group.append((ts, voltage))
    result.extend(bucket_extremes(group))
    return result


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


# Tiny bitmap font keeps Telegram PNG rendering dependency-free.
CHART_FONT = {
    " ": ("00000",) * 7,
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    ":": ("00000", "00100", "00100", "00000", "00100", "00100", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00001", "00010", "00100", "01000", "10000", "10000"),
}


def chart_pixel(
    image: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    color: bytes,
) -> None:
    """Write one RGB pixel inside the PNG canvas."""

    if 0 <= x < width and 0 <= y < height:
        index = (y * width + x) * 3
        image[index : index + 3] = color


def chart_text(
    image: bytearray,
    width: int,
    height: int,
    text: str,
    x: int,
    y: int,
    color: bytes,
    scale: int = 2,
) -> None:
    """Draw simple pixel labels readable without system fonts."""

    for char in text.upper():
        glyph = CHART_FONT.get(char, CHART_FONT[" "])
        for row, pattern in enumerate(glyph):
            for col, bit in enumerate(pattern):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            chart_pixel(
                                image,
                                width,
                                height,
                                x + (col * scale) + dx,
                                y + (row * scale) + dy,
                                color,
                            )
        x += 6 * scale


def chart_line(
    image: bytearray,
    width: int,
    height: int,
    start: tuple[int, int],
    end: tuple[int, int],
    color: bytes,
    thickness: int = 1,
) -> None:
    """Rasterize a segment with integer Bresenham stepping."""

    x, y = start
    end_x, end_y = end
    delta_x, delta_y = abs(end_x - x), -abs(end_y - y)
    step_x, step_y = (1 if x < end_x else -1), (1 if y < end_y else -1)
    error = delta_x + delta_y
    while True:
        for offset in range(thickness):
            chart_pixel(image, width, height, x, y + offset, color)
        if x == end_x and y == end_y:
            return
        twice = 2 * error
        if twice >= delta_y:
            error += delta_y
            x += step_x
        if twice <= delta_x:
            error += delta_x
            y += step_y


def chart_scale(voltages: list[float]) -> tuple[float, float, float]:
    """Choose rounded grid ticks that include all measured extremes."""

    low, high = min(voltages), max(voltages)
    for tick in (5.0, 10.0, 20.0, 25.0, 50.0, 100.0, 200.0):
        bottom = math.floor((low - 3) / tick) * tick
        top = math.ceil((high + 3) / tick) * tick
        if (top - bottom) / tick <= 7:
            return bottom, top, tick
    return math.floor(low / 200) * 200, math.ceil(high / 200) * 200, 200


def chart_grid(
    pixels: bytearray,
    width: int,
    height: int,
    limits: tuple[float, float, float],
    bounds: tuple[int, int, int, int],
) -> None:
    """Draw horizontal voltage guide lines and voltage-axis labels."""

    low, high, step = limits
    left, top, right, bottom = bounds
    grid, ink = b"\xdb\xe4\xef", b"\x33\x47\x56"
    for tick in range(int(round((high - low) / step)) + 1):
        value = low + tick * step
        y = round(bottom - (value - low) / (high - low) * (bottom - top))
        for x in range(left, right + 1):
            if x % 8 < 5:
                chart_pixel(pixels, width, height, x, y, grid)
        chart_text(pixels, width, height, f"{value:g}V", 6, y - 10, ink, 3)


def chart_times(
    pixels: bytearray,
    width: int,
    height: int,
    bounds: tuple[int, int, int, int],
    since: float,
    until: float,
    timezone: str,
) -> None:
    """Label ticks by real local timestamps, not by point index."""

    left, _, right, bottom = bounds
    ink, gray = b"\x33\x47\x56", b"\x94\xa3\xb8"
    for index in range(5):
        frac = index / 4
        x = round(left + frac * (right - left))
        moment = since + frac * (until - since)
        label = datetime.fromtimestamp(moment, ZoneInfo(timezone)).strftime(
            "%H:%M"
        )
        for y in range(bottom, bottom + 7):
            chart_pixel(pixels, width, height, x, y, gray)
        chart_text(pixels, width, height, label, x - 44, bottom + 17, ink, 3)
        if index in (0, 4):
            date = datetime.fromtimestamp(moment, ZoneInfo(timezone)).strftime(
                "%d.%m"
            )
            chart_text(
                pixels, width, height, date, x - 28, bottom + 43, ink, 2
            )


def chart_header(
    pixels: bytearray,
    width: int,
    height: int,
    phase: str,
    hours: int,
    voltages: list[float],
    warning_low: float | None,
) -> None:
    """Print measured range and optional low-voltage warning marker."""

    dark, red = b"\x1e\x40\x67", b"\xc0\x27\x37"
    chart_text(
        pixels, width, height, f"{phase} / {hours}H VOLTAGE", 84, 18, dark, 3
    )
    if voltages:
        info = f"MIN {min(voltages):.1f}V  MAX {max(voltages):.1f}V"
        chart_text(pixels, width, height, info, 84, 56, dark, 2)
        chart_text(
            pixels,
            width,
            height,
            f"LAST {voltages[-1]:.1f}V",
            480,
            56,
            dark,
            2,
        )
    if warning_low is not None:
        chart_text(
            pixels,
            width,
            height,
            f"LOW {warning_low:g}V",
            width - 195,
            56,
            red,
            2,
        )


def chart_last_sample_note(
    pixels: bytearray,
    width: int,
    height: int,
    points: list[tuple[float, float]],
    now: float,
    timezone: str,
) -> None:
    """Show measurement time; never label an old chart value as live."""

    if not points:
        return
    timestamp = points[-1][0]
    observed = datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
    age = now - timestamp
    status = "HISTORICAL" if age > 60 else "RECENT"
    note = f"LAST AT {observed:%d.%m %H:%M} / {status}"
    chart_text(pixels, width, height, note, 480, 82, b"\x33\x47\x56", 2)


def chart_data(
    pixels: bytearray,
    width: int,
    height: int,
    points: list[tuple[float, float]],
    bounds: tuple[int, int, int, int],
    times: tuple[float, float],
    limits: tuple[float, float, float],
    warning_low: float | None,
) -> None:
    """Plot actual times and leave large gaps disconnected."""

    left, top, right, bottom = bounds
    since, until = times
    low, high, _ = limits
    ink, danger = b"\x25\x63\xeb", b"\xdc\x26\x26"
    previous = None
    gap_limit = max(90.0, 2 * (until - since) / (right - left))
    for ts, voltage in points:
        x = round(left + (ts - since) / (until - since) * (right - left))
        y = round(bottom - (voltage - low) / (high - low) * (bottom - top))
        current = (x, y)
        abnormal = warning_low is not None and voltage < warning_low
        color = danger if abnormal else ink
        if previous is not None and ts - previous[0] <= gap_limit:
            chart_line(pixels, width, height, previous[1], current, color, 2)
        else:
            chart_line(pixels, width, height, current, current, color, 3)
        previous = (ts, current)


def chart_warning_line(
    pixels: bytearray,
    width: int,
    height: int,
    bounds: tuple[int, int, int, int],
    limits: tuple[float, float, float],
    warning_low: float | None,
) -> None:
    """Show configured low warning only when within the displayed scale."""

    if warning_low is None:
        return
    low, high, _ = limits
    if not low < warning_low < high:
        return
    left, top, right, bottom = bounds
    y = round(bottom - (warning_low - low) / (high - low) * (bottom - top))
    for x in range(left, right):
        if x % 13 < 7:
            chart_pixel(pixels, width, height, x, y, b"\xf9\x73\x16")


def chart_png(
    path: Path,
    phase: str,
    hours: int,
    target: Path,
    warning_low: float | None = None,
    timezone: str = "Europe/Kyiv",
) -> int:
    """Render voltage/time axis and preserve the full requested period."""

    now = datetime.now().timestamp()
    since = now - hours * 3600
    points = series(path, phase, since, until=now)
    width, height = 1000, 560
    pixels = bytearray(b"\xf8\xfa\xfc" * width * height)
    bounds = (84, 108, width - 42, height - 76)
    values = [value for _, value in points]
    limits = chart_scale(values) if values else (0.0, 250.0, 50.0)
    chart_header(pixels, width, height, phase, hours, values, warning_low)
    chart_last_sample_note(pixels, width, height, points, now, timezone)
    chart_grid(pixels, width, height, limits, bounds)
    chart_times(pixels, width, height, bounds, since, now, timezone)
    chart_warning_line(pixels, width, height, bounds, limits, warning_low)
    if points:
        chart_data(
            pixels,
            width,
            height,
            points,
            bounds,
            (since, now),
            limits,
            warning_low,
        )
    else:
        chart_text(
            pixels, width, height, "NO DATA", 400, 270, b"\x94\xa3\xb8", 3
        )
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
