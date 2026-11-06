#!/usr/bin/env python3
"""Compare a manual forecast with recent Shelly voltage, without writes."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_dtek_forecast import (  # noqa: E402
    KYIV,
    MANUAL_SOURCE,
    ManualForecast,
    local_stamp,
    validate_periods,
)

ALLOWED_SOURCES = {MANUAL_SOURCE, "user-supplied forecast, unverified"}


def parse_args() -> argparse.Namespace:
    """Require files and allow deterministic read-only checks."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forecast", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--at", help="Offset-aware ISO-8601 observation time")
    parser.add_argument("--max-age-sec", type=int, default=45)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def load_manual(path: Path) -> ManualForecast:
    """Reject untrusted or accidentally provider-verified forecast data."""

    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("manual forecast file is unavailable or oversized")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("manual forecast root must be an object")
    if data.get("source") not in ALLOWED_SOURCES:
        raise ValueError("forecast source is not the manual source")
    if data.get("source_verified", data.get("provider_verified")) is not False:
        raise ValueError("manual forecast cannot claim provider verification")
    day = date.fromisoformat(data["date_kyiv"])
    group = str(data["group"])
    if not re.fullmatch(r"\d{1,2}\.\d", group):
        raise ValueError("invalid group")
    periods = tuple(tuple(pair) for pair in data["user_reported_windows"])
    validate_periods(day, periods)
    return ManualForecast(day.isoformat(), group, periods)


def current_window(
    forecast: ManualForecast,
    observed: datetime,
) -> tuple[str, str] | None:
    """Select a predicted window without declaring an actual outage."""

    day = date.fromisoformat(forecast.day)
    if observed.astimezone(KYIV).date() != day:
        return None
    for begin, end in forecast.periods:
        if (
            local_stamp(day, begin)
            <= observed.timestamp()
            < local_stamp(day, end)
        ):
            return begin, end
    return None


def read_latest(path: Path) -> tuple[float, float, float, int]:
    """Read the latest true measurement and open connectivity-gap count."""

    if not path.is_file() or path.is_symlink():
        raise ValueError("SQLite database is missing or is a symlink")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
        row = db.execute(
            "select ts, voltage, frequency from measurements "
            "order by ts desc limit 1"
        ).fetchone()
        open_gaps = db.execute(
            "select count(*) from outages where ended_at is null"
        ).fetchone()[0]
    if row is None:
        raise ValueError("no real Shelly measurements available")
    return float(row[0]), float(row[1]), float(row[2]), open_gaps


def compare(
    forecast: ManualForecast,
    reading: tuple,
    observed: datetime,
    max_age_sec: int = 45,
) -> dict:
    """Keep planned and measured evidence independently classified."""

    if not 10 <= max_age_sec <= 300:
        raise ValueError("invalid sample age limit")
    ts, voltage, frequency, open_gaps = reading
    age = observed.timestamp() - ts
    fresh = (
        math.isfinite(age)
        and 0 <= age <= max_age_sec
        and math.isfinite(voltage)
        and 0 < voltage < 500
        and math.isfinite(frequency)
        and 40 <= frequency <= 70
        and open_gaps == 0
    )
    window = current_window(forecast, observed)
    return {
        "status": "pass",
        "observed_at_kyiv": observed.astimezone(KYIV).isoformat(),
        "group": forecast.group,
        "source": MANUAL_SOURCE,
        "source_verified": False,
        "predicted_window": list(window) if window else None,
        "actual_mains_outage_verified": False,
        "measurement": "recent_valid_voltage" if fresh else "unknown_or_stale",
        "voltage_v": round(voltage, 1) if fresh else None,
        "frequency_hz": round(frequency, 2) if fresh else None,
        "sample_age_sec": round(age, 1) if fresh else None,
        "connectivity_gaps_open": open_gaps,
        "device_controls_or_telegram_writes": False,
    }


def main() -> int:
    """Produce a one-shot PASS/FAIL report with no device or network calls."""

    args = parse_args()
    try:
        now = (
            datetime.fromisoformat(args.at)
            if args.at
            else datetime.now(ZoneInfo("Europe/Kyiv"))
        )
        if now.tzinfo is None:
            raise ValueError("--at must include a timezone offset")
        forecast = load_manual(args.forecast)
        result = compare(forecast, read_latest(args.db), now, args.max_age_sec)
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
    ) as exc:
        result = {"status": "fail", "reason": type(exc).__name__}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['status'].upper()}: manual forecast comparison")
        if result["status"] == "pass":
            print("Predicted window:", result["predicted_window"])
            print("Measured voltage:", result["voltage_v"])
        else:
            print("Reason:", result["reason"])
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
