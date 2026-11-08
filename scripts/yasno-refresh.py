"""Safely refresh a YASNO QA cache once; no Telegram or Shelly writes."""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yasno_cache import (  # noqa: E402
    assert_safe_path,
    fetch_to_cache,
    read_verified_cache,
)
from yasno_schedule import parse_schedule  # noqa: E402

DEFAULT_INTERVAL_S = 900
MIN_INTERVAL_S = 300
MAX_INTERVAL_S = 7200


def cache_health(
    path: str, group: str, now: datetime
) -> tuple[bool, float, str]:
    """Validate today's sealed schedule and return its retrieval age."""

    try:
        payload, captured = read_verified_cache(path)
        result = parse_schedule(payload, group, now, captured)
        age = (now - captured).total_seconds()
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return False, -1.0, type(exc).__name__
    return True, age, f"{result.day} group={result.group}"


@contextmanager
def exclusive_lock(path: str):
    """Reject unsafe lockfiles and prevent concurrent refresh attempts."""

    lock = Path(path + ".lock")
    assert_safe_path(lock, writing=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(lock, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("refresh lock is not an ordinary file")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("refresh lock permissions or owner invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another YASNO refresh is running") from exc
        yield
    finally:
        os.close(descriptor)


def refresh_once(path: str, group: str, interval_s: int) -> str:
    """Fetch only when due; keep a previously valid cache on errors."""

    if not MIN_INTERVAL_S <= interval_s <= MAX_INTERVAL_S:
        raise ValueError("refresh interval must be between 300 and 7200s")
    assert_safe_path(Path(path), writing=True)
    with exclusive_lock(path):
        fresh, age, detail = cache_health(
            path, group, datetime.now(timezone.utc)
        )
        if fresh and age < interval_s:
            return f"SKIPPED cache fresh age={age:.0f}s {detail}"
        day = fetch_to_cache(path, group)
        return f"UPDATED day={day.day} group={day.group} source=YASNO"


def options() -> argparse.Namespace:
    """Expose explicit inspect, dry-run and write-once operations."""

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true", help="Read-only check")
    mode.add_argument(
        "--dry-run", action="store_true", help="No writes or HTTP"
    )
    mode.add_argument("--once", action="store_true", help="Fetch at most once")
    parser.add_argument("--cache", required=True, help="Protected JSON path")
    parser.add_argument("--group", default="10.1", help="YASNO queue group")
    parser.add_argument(
        "--min-interval-seconds", type=int, default=DEFAULT_INTERVAL_S
    )
    return parser.parse_args()


def main() -> int:
    """Report a stable PASS/FAIL summary with no credentials in output."""

    args = options()
    try:
        assert_safe_path(Path(args.cache), writing=True)
        if not MIN_INTERVAL_S <= args.min_interval_seconds <= MAX_INTERVAL_S:
            raise ValueError("interval outside allowed 300–7200 seconds")
        fresh, age, detail = cache_health(
            args.cache, args.group, datetime.now(timezone.utc)
        )
        if args.status:
            print(f"{'PASS' if fresh else 'FAIL'} STATUS {detail}")
            return 0 if fresh else 1
        if args.dry_run:
            decision = (
                "SKIP"
                if fresh and age < args.min_interval_seconds
                else "FETCH_DUE"
            )
            print(f"PASS DRY_RUN {decision} {detail} no_network=True")
            return 0
        result = refresh_once(
            args.cache, args.group, args.min_interval_seconds
        )
        print(f"PASS {result}")
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"FAIL REFRESH {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
