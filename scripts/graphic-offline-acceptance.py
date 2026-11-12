#!/usr/bin/env python3
"""Produce an offline PNG acceptance result from local, read-only fixtures."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import struct
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_INPUT = 65536
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


class AcceptanceError(Exception):
    """Safe reason code for a rejected local acceptance run."""


def arguments() -> argparse.Namespace:
    """Require a local health snapshot and explicit output destination."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-file", type=Path, required=True)
    forecast = parser.add_mutually_exclusive_group()
    forecast.add_argument("--forecast-file", type=Path)
    forecast.add_argument("--verified-yasno-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", default="10.1")
    parser.add_argument("--now", type=float)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def linked_path(path: Path) -> bool:
    """Reject any symlink in the path, including parents."""

    return any(item.is_symlink() for item in (path, *path.parents))


def read_fixture(path: Path) -> dict:
    """Read bounded JSON without following symlinks or hardlinks."""

    if linked_path(path):
        raise AcceptanceError("INPUT_UNSAFE")
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AcceptanceError("INPUT_UNSAFE")
        if info.st_size > MAX_INPUT:
            raise AcceptanceError("INPUT_TOO_LARGE")
        with path.open("rb") as stream:
            raw = stream.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise AcceptanceError("INPUT_TOO_LARGE")
        content = json.loads(raw)
    except (OSError, UnicodeError, ValueError) as exc:
        raise AcceptanceError("INPUT_INVALID") from exc
    if not isinstance(content, dict):
        raise AcceptanceError("INPUT_INVALID")
    return content


def verified_forecast(cache: Path, clock: float, group: str) -> dict:
    """Use only a verified, fresh YASNO cache and its retrieval timestamp."""

    from yasno_cache import read_verified_cache
    from yasno_schedule import parse_schedule

    try:
        payload, captured = read_verified_cache(str(cache))
        day = parse_schedule(
            payload,
            group,
            datetime.fromtimestamp(clock, timezone.utc),
            captured,
        )
    except (ValueError, OSError, KeyError, TypeError):
        raise AcceptanceError("FORECAST_INVALID") from None
    return {
        "bars": day.bars,
        "day": day.day,
        "forecast_updated": day.retrieved_at,
        "forecast_source": "yasno",
    }


def widget_state(args: argparse.Namespace, clock: float) -> dict:
    """Encode an offline-only widget with optional verified YASNO forecast."""

    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from telegram_widget_data import widget_public_data

    health = read_fixture(args.health_file)
    if health.get("connectivity") != "offline":
        raise AcceptanceError("NOT_OFFLINE")
    options = {"group": args.group, "now": clock, "language": "uk"}
    if args.verified_yasno_cache:
        options.update(
            verified_forecast(args.verified_yasno_cache, clock, args.group)
        )
    elif args.forecast_file:
        forecast = read_fixture(args.forecast_file)
        if set(forecast) != {"bars", "day", "updated", "source"}:
            raise AcceptanceError("FORECAST_INVALID")
        options.update(
            bars=forecast["bars"],
            day=forecast["day"],
            forecast_updated=forecast["updated"],
            forecast_source=forecast["source"],
        )
    try:
        state = json.loads(widget_public_data(health, **options))
    except (ValueError, TypeError) as exc:
        raise AcceptanceError("STATE_INVALID") from exc
    if state.get("s") != "offline" or any(
        name in state for name in ("v", "f", "t")
    ):
        raise AcceptanceError("LIVE_DATA_LEAK")
    return state


def verify_png(path: Path, result: dict) -> None:
    """Verify dimensions and absence of live voltage after rendering."""

    image = path.read_bytes()
    if (
        not image.startswith(PNG_HEADER)
        or len(image) < 1024
        or len(image) > 2_000_000
        or struct.unpack(">II", image[16:24]) != (960, 800)
        or result.get("status") != "offline"
        or result.get("live_voltage_visible") is not False
    ):
        raise AcceptanceError("PNG_INVALID")


def file_digest(path: Path) -> bytes:
    """Hash image data in bounded chunks rather than reading all at once."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(65536), b""):
            digest.update(piece)
    return digest.digest()


def deliver_png(source: Path, target: Path) -> str:
    """Publish atomically, accepting a byte-identical existing image."""

    if linked_path(target) or not target.parent.is_dir():
        raise AcceptanceError("OUTPUT_UNSAFE")
    if target.exists():
        info = target.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 2_000_000
        ):
            raise AcceptanceError("OUTPUT_UNSAFE")
        if file_digest(source) != file_digest(target):
            raise AcceptanceError("OUTPUT_EXISTS_DIFFERENT")
        return "reused"
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError as exc:
        raise AcceptanceError("OUTPUT_EXISTS_DIFFERENT") from exc
    return "created"


def accept(args: argparse.Namespace) -> dict:
    """Render once locally and leave all existing data unchanged."""

    clock = time.time() if args.now is None else args.now
    if not math.isfinite(clock) or clock <= 0:
        raise AcceptanceError("CLOCK_INVALID")
    state = widget_state(args, clock)
    if linked_path(args.output) or not args.output.parent.is_dir():
        raise AcceptanceError("OUTPUT_UNSAFE")
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from telegram_card_png import render_card_png

    with tempfile.TemporaryDirectory(
        prefix=".offline-acceptance-", dir=args.output.parent
    ) as folder:
        image = Path(folder) / "card.png"
        result = render_card_png(state, image, now=clock)
        image.chmod(0o600)
        verify_png(image, result)
        action = deliver_png(image, args.output)
    return {
        "status": "PASS",
        "live_voltage_visible": False,
        "forecast_visible": result["forecast_visible"],
        "image_size": "960x800",
        "output_action": action,
        "telegram_sent": False,
    }


def main() -> int:
    """Return 0 on acceptance and 2 on a safe, path-free refusal."""

    args = arguments()
    try:
        result = accept(args)
    except (AcceptanceError, OSError, ValueError, TypeError) as exc:
        reason = (
            exc.args[0] if isinstance(exc, AcceptanceError) else "IO_ERROR"
        )
        result = {"status": "FAIL", "reason": reason, "telegram_sent": False}
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("OFFLINE ACCEPTANCE: " + result["status"])
        if result["status"] == "FAIL":
            print("Reason: " + result["reason"])
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
