"""Compare Shelly outage timing with published DTEK windows, not causes."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
HALF_HOUR = re.compile(r"^(?:[01][0-9]|2[0-3]):(?:00|30)$")


@dataclass(frozen=True)
class TimeCorrelation:
    """A timeline comparison with no assertion of outage cause."""

    status: str
    matching_window: tuple[str, str] | None = None
    overlap_seconds: int = 0
    cause_verified: bool = False


def clock_stamp(day: date, clock: str) -> float:
    """Convert DTEK local half-hours, rejecting DST-ambiguous times."""

    if clock == "24:00":
        day += timedelta(days=1)
        clock = "00:00"
    elif not HALF_HOUR.fullmatch(clock):
        raise ValueError("invalid DTEK clock value")
    hour, minute = map(int, clock.split(":"))
    local = datetime.combine(day, time(hour, minute), tzinfo=KYIV)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise ValueError("DTEK time is ambiguous on DST transition")
    return local.timestamp()


def calendar_windows(
    day: date,
    periods: list[tuple[str, str]],
) -> list[tuple[float, float, tuple[str, str]]]:
    """Check interval order, overlap and valid day bounds."""

    if len(periods) > 48:
        raise ValueError("too many DTEK intervals")
    found = []
    previous_end = None
    for start, end in periods:
        begin_stamp = clock_stamp(day, start)
        end_stamp = clock_stamp(day, end)
        if end_stamp <= begin_stamp:
            raise ValueError("invalid DTEK interval order")
        if previous_end is not None and begin_stamp < previous_end:
            raise ValueError("overlapping DTEK intervals")
        previous_end = end_stamp
        found.append((begin_stamp, end_stamp, (start, end)))
    return found


def valid_event(
    started: float,
    ended: float | None,
    observed_until: float | None,
) -> tuple[float, float]:
    """Validate real event times and bound an open observation."""

    begin = float(started)
    stop = observed_until if ended is None else ended
    if stop is None:
        raise ValueError("open event needs an observation boundary")
    finish = float(stop)
    if not all(math.isfinite(value) for value in (begin, finish)):
        raise ValueError("nonfinite event timestamp")
    if finish < begin:
        raise ValueError("event ends before it starts")
    return begin, finish


def correlate_outage(
    started: float,
    ended: float | None,
    schedule_day: str,
    periods: list[tuple[str, str]] | None,
    observed_until: float | None = None,
) -> TimeCorrelation:
    """Match timing only, with unknown for missing or invalid evidence."""

    unknown = TimeCorrelation("unknown")
    if periods is None:
        return unknown
    try:
        day = date.fromisoformat(schedule_day)
        begin, finish = valid_event(started, ended, observed_until)
        if datetime.fromtimestamp(begin, KYIV).date() != day:
            return unknown
        windows = calendar_windows(day, periods)
    except (ValueError, TypeError, OverflowError, OSError):
        return unknown
    best = (0, None)
    for low, high, window in windows:
        overlap = max(0, min(finish, high) - max(begin, low))
        if low <= begin < high:
            return TimeCorrelation("start_within_window", window, int(overlap))
        if overlap > best[0]:
            best = (overlap, window)
    if best[1] is not None:
        return TimeCorrelation("partial_overlap", best[1], int(best[0]))
    return TimeCorrelation("outside_published_windows")
