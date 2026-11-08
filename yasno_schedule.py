"""Validate direct YASNO Kyiv/DTEK outage forecasts independently of Shelly."""

from __future__ import annotations

import argparse
import json
import re
import stat
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
YASNO_URL = (
    "https://app.yasno.ua/api/blackout-service/public/shutdowns/"
    "regions/25/dsos/902/planned-outages"
)
MAX_BYTES = 1_048_576
MAX_CAPTURE_AGE_S = 7200
MAX_PUBLICATION_AGE_S = 36 * 3600
SLOT_KINDS = {"NotPlanned": "O", "Definite": "X", "Possible": "?"}


@dataclass(frozen=True)
class YasnoDay:
    """Provenance-labelled *forecast*, not measured electricity."""

    group: str
    day: str
    bars: str
    published_at: float
    retrieved_at: float
    source: str = "yasno-kyiv-dtek"
    status: str = "ScheduleApplies"


def parse_timestamp(value: object) -> datetime:
    """Only accept an ISO timestamp with an explicit time-zone offset."""

    if not isinstance(value, str):
        raise ValueError("source timestamp missing")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("source timestamp invalid") from exc
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("source timestamp lacks timezone")
    return date


def schedule_slots(slots: object) -> str:
    """Require 48 half-hour slots with no gap, overlap or unknown types."""

    if not isinstance(slots, list) or not slots:
        raise ValueError("schedule slots missing")
    start = 0
    segments = []
    for slot in slots:
        if not isinstance(slot, dict):
            raise ValueError("schedule slot must be an object")
        lo, hi, kind = (slot.get(x) for x in ("start", "end", "type"))
        if type(lo) is not int or type(hi) is not int:
            raise ValueError("schedule minutes must be integers")
        if lo != start or not (lo < hi <= 1440):
            raise ValueError("schedule has a gap, overlap, or bad end")
        if lo % 30 or hi % 30 or kind not in SLOT_KINDS:
            raise ValueError("schedule slot type or alignment unknown")
        segments.append(SLOT_KINDS[kind] * ((hi - lo) // 30))
        start = hi
    bars = "".join(segments)
    if start != 1440 or len(bars) != 48:
        raise ValueError("schedule does not cover the full day")
    return bars


def validate_day(record: object, now: datetime) -> str:
    """Check Kyiv calendar day and a published applicable schedule."""

    if not isinstance(record, dict):
        raise ValueError("schedule day missing")
    if record.get("status") != "ScheduleApplies":
        raise ValueError("schedule not published or not applicable")
    timestamp = parse_timestamp(record.get("date"))
    local = timestamp.astimezone(KYIV)
    if local.time().replace(tzinfo=None).isoformat() != "00:00:00":
        raise ValueError("schedule date is not Kyiv midnight")
    if local.date() != now.astimezone(KYIV).date():
        raise ValueError("schedule date differs from today")
    return schedule_slots(record.get("slots"))


def parse_schedule(
    data: object, group: str, now: datetime, retrieved_at: datetime
) -> YasnoDay:
    """Fail closed on invalid YASNO response and stale fetch metadata."""

    if now.tzinfo is None or retrieved_at.tzinfo is None:
        raise ValueError("timezone-aware times required")
    if not re.fullmatch(r"\d{1,2}\.\d", group):
        raise ValueError("unsupported outage group")
    age = (now - retrieved_at).total_seconds()
    if not 0 <= age <= MAX_CAPTURE_AGE_S:
        raise ValueError("YASNO capture is stale or in the future")
    if not isinstance(data, dict) or group not in data:
        raise ValueError("YASNO group missing")
    record = data[group]
    if not isinstance(record, dict):
        raise ValueError("YASNO group structure invalid")
    published = parse_timestamp(record.get("updatedOn"))
    age = (now - published).total_seconds()
    if not 0 <= age <= MAX_PUBLICATION_AGE_S:
        raise ValueError("YASNO publication timestamp stale or invalid")
    bars = validate_day(record.get("today"), now)
    return YasnoDay(
        group,
        now.astimezone(KYIV).date().isoformat(),
        bars,
        published.timestamp(),
        retrieved_at.timestamp(),
    )


def read_cached(path: str) -> tuple[dict, datetime]:
    """Read a bounded regular JSON file, treating mtime as cache age."""

    file = Path(path)
    info = file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
        raise ValueError("YASNO cache is not a bounded regular file")
    raw = file.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("YASNO cache is too large")
    data = json.loads(raw)
    return data, datetime.fromtimestamp(info.st_mtime, timezone.utc)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Forbid redirection away from the pinned public provider URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Never follow a redirect to another origin."""

        return None


def fetch_live() -> tuple[dict, datetime]:
    """Fetch a size-limited response from the pinned public HTTPS API."""

    request = urllib.request.Request(
        YASNO_URL, headers={"Accept": "application/json"}
    )
    with urllib.request.build_opener(NoRedirect()).open(
        request, timeout=15
    ) as response:
        if response.geturl() != YASNO_URL or response.status != 200:
            raise ValueError("YASNO returned a different endpoint")
        content_type = response.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0] != "application/json":
            raise ValueError("YASNO did not return JSON")
        raw = response.read(MAX_BYTES + 1)
        received_at = datetime.now(timezone.utc)
    if len(raw) > MAX_BYTES:
        raise ValueError("YASNO response too large")
    return json.loads(raw), received_at


def clock_for_slot(index: int) -> str:
    """Format one 30-minute index as an exact 24-hour clock time."""

    return f"{index // 2:02d}:{30 * (index % 2):02d}"


def outage_windows(bars: str) -> list[str]:
    """Return definite outage windows for display, never mains events."""

    windows = []
    start = None
    for index in range(49):
        off = index < 48 and bars[index] == "X"
        if off and start is None:
            start = index
        elif not off and start is not None:
            windows.append(f"{clock_for_slot(start)}–{clock_for_slot(index)}")
            start = None
    return windows


def main() -> None:
    """Print a bounded QA forecast; do not change production or Telegram."""

    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--fetch", action="store_true")
    source.add_argument("--file", help="Previously captured YASNO JSON")
    parser.add_argument("--group", default="10.1")
    parser.add_argument("--json", action="store_true", help="JSON summary")
    args = parser.parse_args()
    data, captured = fetch_live() if args.fetch else read_cached(args.file)
    result = parse_schedule(
        data, args.group, datetime.now(timezone.utc), captured
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False))
    else:
        print("YASNO · Kyiv DTEK · forecast (not measured mains)")
        print(f"Group {result.group}, day {result.day}")
        print(
            "Definite planned windows:",
            ", ".join(outage_windows(result.bars)) or "none published",
        )


if __name__ == "__main__":
    main()
