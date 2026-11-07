"""Read cached outage-data-ua forecasts separately from Shelly."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
REGIONS = frozenset({"kyiv", "kyiv-region", "dnipro", "odesa"})
MAX_BYTES = 512 * 1024
MAX_AGE = timedelta(hours=2)
STATES = {
    "yes": ("on", "on"),
    "no": ("off", "off"),
    "first": ("off", "on"),
    "second": ("on", "off"),
    "maybe": ("possible", "possible"),
    "mfirst": ("possible", "on"),
    "msecond": ("on", "possible"),
}


@dataclass(frozen=True, slots=True)
class CommunityForecast:
    """Outage forecast from a community mirror, not measured mains state."""

    region: str
    group: str
    day: str
    source_updated: datetime
    definite: tuple[tuple[str, str], ...]
    possible: tuple[tuple[str, str], ...]


def check_timestamp(raw: object, now: datetime) -> datetime:
    """Reject missing, naive, stale and implausibly future updates."""

    if not isinstance(raw, str) or now.tzinfo is None:
        raise ValueError("invalid update timestamp")
    try:
        updated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid update timestamp") from exc
    if updated.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    age = now.astimezone(timezone.utc) - updated.astimezone(timezone.utc)
    if age > MAX_AGE or age < -timedelta(minutes=2):
        raise ValueError("outage-data-ua snapshot is stale")
    return updated


def current_groups(fact: object, now: datetime) -> dict:
    """Locate today's exact Kyiv-midnight data without using old entries."""

    if not isinstance(fact, dict) or not isinstance(fact.get("data"), dict):
        raise ValueError("missing fact data")
    day = now.astimezone(KYIV).date()
    found = []
    for stamp, groups in fact["data"].items():
        try:
            local = datetime.fromtimestamp(int(stamp), KYIV)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            raise ValueError("invalid day key") from exc
        if local.hour or local.minute or local.second:
            raise ValueError("day key is not local midnight")
        if local.date() == day:
            found.append(groups)
    if len(found) != 1 or not isinstance(found[0], dict):
        raise ValueError("today's forecast unavailable")
    return found[0]


def slots_for(hours: object) -> tuple[str, ...]:
    """Decode all 24 hourly flags into 48 explicit half-hour states."""

    if not isinstance(hours, dict):
        raise ValueError("group is not present")
    if set(hours) != {str(hour) for hour in range(1, 25)}:
        raise ValueError("incomplete hourly forecast")
    slots = []
    for hour in range(1, 25):
        code = hours[str(hour)]
        if not isinstance(code, str) or code not in STATES:
            raise ValueError("unknown forecast state")
        slots.extend(STATES[code])
    return tuple(slots)


def windows(slots: tuple[str, ...], state: str) -> tuple[tuple[str, str], ...]:
    """Merge adjacent half-hours of one kind into sorted time windows."""

    result = []
    start = None
    for index, code in enumerate((*slots, "end")):
        if code == state and start is None:
            start = index
        elif code != state and start is not None:
            result.append((clock(start), clock(index)))
            start = None
    return tuple(result)


def clock(slot: int) -> str:
    """Format the local time at a half-hour boundary."""

    return f"{slot // 2:02d}:{slot % 2 * 30:02d}"


def parse_snapshot(
    source: object, region: str, group: str, now: datetime | None = None
) -> CommunityForecast:
    """Fail closed when community data cannot be independently interpreted."""

    current = now or datetime.now(timezone.utc)
    if region not in REGIONS or not re.fullmatch(r"\d{1,2}\.\d", group):
        raise ValueError("invalid region or group")
    if not isinstance(source, dict) or source.get("regionId") != region:
        raise ValueError("region mismatch")
    status = source.get("lastUpdateStatus")
    if not isinstance(status, dict) or status.get("ok") is not True:
        raise ValueError("upstream parser failed")
    if status.get("status") != "parsed" or status.get("code") != 200:
        raise ValueError("upstream parser status not accepted")
    meta = source.get("meta")
    if not isinstance(meta, dict) or meta.get("schemaVersion") != "1.0.0":
        raise ValueError("unsupported snapshot schema")
    updated = check_timestamp(source.get("lastUpdated"), current)
    hours = current_groups(source.get("fact"), current).get(f"GPV{group}")
    decoded = slots_for(hours)
    return CommunityForecast(
        region,
        group,
        current.astimezone(KYIV).date().isoformat(),
        updated,
        windows(decoded, "off"),
        windows(decoded, "possible"),
    )


def read_snapshot(path: str | Path) -> object:
    """Read a size-limited regular JSON file without following symlinks."""

    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise ValueError("snapshot must be a regular file")
    with file.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("snapshot is too large")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid snapshot JSON") from exc


def format_forecast(forecast: CommunityForecast, language: str = "uk") -> str:
    """Render the forecast with conspicuous independent-source disclaimer."""

    labels = {
        "uk": ("Громадський прогноз", "Відключення", "Можливо"),
        "ru": ("Общественный прогноз", "Отключения", "Возможно"),
        "en": ("Community forecast", "Scheduled", "Possible"),
    }
    if language not in labels:
        raise ValueError("invalid language")
    title, definite, possible = labels[language]
    lines = [f"{title} · {forecast.day} · GPV{forecast.group}"]
    lines.extend(f"🪫 {definite}: {a}–{b}" for a, b in forecast.definite)
    lines.extend(f"⚠ {possible}: {a}–{b}" for a, b in forecast.possible)
    lines.append("Source: Baskerville42/outage-data-ua (unofficial mirror)")
    lines.append(
        "Forecast only. Actual mains state requires fresh Shelly data."
    )
    return "\n".join(lines)


def main() -> int:
    """Preview cached schedules without network or production changes."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Downloaded local JSON")
    parser.add_argument("--region", required=True, choices=sorted(REGIONS))
    parser.add_argument("--group", required=True, help="Example: 10.1")
    parser.add_argument("--language", choices=("uk", "ru", "en"), default="uk")
    args = parser.parse_args()
    try:
        data = read_snapshot(args.file)
        preview = parse_snapshot(data, args.region, args.group)
        print(format_forecast(preview, args.language))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"FAIL: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
