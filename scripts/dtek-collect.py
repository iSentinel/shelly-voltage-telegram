#!/usr/bin/env python3
"""Collect public DTEK outage schedules as isolated, validated JSON cache."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dtek_schedule import (  # noqa: E402
    CACHE_MAX_BYTES,
    REGIONS,
    extract_schedule,
    outage_periods,
    published_groups,
)


def parse_args() -> argparse.Namespace:
    """Parse explicit one-shot collector options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=sorted(REGIONS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture-html", type=Path)
    parser.add_argument("--timeout-sec", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def browser_html(region: str, timeout_sec: int) -> str:
    """Read a public regional page in a short-lived Chromium browser."""

    from playwright.sync_api import sync_playwright

    url = REGIONS[region]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="load", timeout=timeout_sec * 1000)
            actual = urlparse(page.url)
            expected = urlparse(url)
            if actual.hostname != expected.hostname:
                raise ValueError("DTEK page redirected to another host")
            return page.content()
        finally:
            browser.close()


def build_payload(region: str, html: str, now: datetime) -> dict:
    """Validate today's published schedule before writing a cache."""

    fact = extract_schedule(html)
    groups = published_groups(fact, now)
    count = 0
    for name, hours in groups.items():
        if name.startswith("GPV") and isinstance(hours, dict):
            try:
                outage_periods(hours)
                count += 1
            except ValueError:
                continue
    if not count:
        raise ValueError("DTEK has no complete published groups today")
    payload = {
        "version": 1, "region": region,
        "fetched_at": now.astimezone(timezone.utc).isoformat(),
        "fact": fact,
    }
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > CACHE_MAX_BYTES:
        raise ValueError("DTEK snapshot exceeds cache size limit")
    return payload


def write_cache(path: Path, payload: dict) -> None:
    """Replace cache atomically after complete serialization."""

    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".dtek-", delete=False,
        ) as file:
            name = file.name
            os.fchmod(file.fileno(), 0o600)
            json.dump(payload, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def main() -> int:
    """Collect once, fail closed, and avoid touching live Shelly state."""

    args = parse_args()
    if args.timeout_sec < 1 or args.timeout_sec > 60:
        raise SystemExit("--timeout-sec must be between 1 and 60")
    try:
        html = (
            args.fixture_html.read_text(encoding="utf-8")
            if args.fixture_html
            else browser_html(args.region, args.timeout_sec)
        )
        payload = build_payload(
            args.region, html, datetime.now(timezone.utc)
        )
        if not args.dry_run:
            write_cache(args.output, payload)
        print("PASS: DTEK snapshot validated; dry_run=", args.dry_run)
        return 0
    except Exception as exc:  # Never publish a partial cache on failure.
        print("FAIL: DTEK schedule unavailable", type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
