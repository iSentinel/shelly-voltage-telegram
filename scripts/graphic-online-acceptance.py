#!/usr/bin/env python3
"""Verify a fresh online Shelly Telegram card without network calls."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import struct
import sys
import tempfile
import time
from pathlib import Path


def shared_checks():
    """Reuse the offline CLI's defensive bounded local file operations."""

    path = Path(__file__).with_name("graphic-offline-acceptance.py")
    spec = importlib.util.spec_from_file_location(
        "graphic_acceptance_shared", path
    )
    if spec is None or spec.loader is None:
        raise ValueError("SHARED_CHECKS_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments() -> argparse.Namespace:
    """Read one local online health snapshot and an output destination."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-file", type=Path, required=True)
    parser.add_argument("--forecast-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", default="10.1")
    parser.add_argument("--now", type=float)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def local_state(args: argparse.Namespace, clock: float, checks) -> dict:
    """Require real fresh voltage and optionally fresh YASNO schedule data."""

    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from telegram_widget_data import widget_public_data

    health = checks.read_fixture(args.health_file)
    if health.get("connectivity") != "online":
        raise ValueError("NOT_ONLINE")
    options = {"group": args.group, "now": clock, "language": "uk"}
    if args.forecast_file:
        forecast = checks.read_fixture(args.forecast_file)
        if set(forecast) != {"bars", "day", "updated", "source"}:
            raise ValueError("FORECAST_INVALID")
        options.update(
            bars=forecast["bars"],
            day=forecast["day"],
            forecast_updated=forecast["updated"],
            forecast_source=forecast["source"],
        )
    try:
        state = json.loads(widget_public_data(health, **options))
    except (TypeError, ValueError):
        raise ValueError("STATE_INVALID") from None
    if state.get("s") != "online":
        raise ValueError("NO_FRESH_MEASUREMENT")
    if any(field not in state for field in ("v", "t")):
        raise ValueError("NO_FRESH_MEASUREMENT")
    return state


def check_png(path: Path, result: dict) -> None:
    """Validate PNG geometry and true online telemetry rendering."""

    image = path.read_bytes()
    if (
        not image.startswith(b"\x89PNG\r\n\x1a\n")
        or len(image) < 1024
        or len(image) > 2_000_000
        or struct.unpack(">II", image[16:24]) != (960, 560)
        or result.get("status") != "online"
        or result.get("live_voltage_visible") is not True
    ):
        raise ValueError("PNG_INVALID")


def accept(args: argparse.Namespace, checks) -> dict:
    """Render from a local snapshot and publish without overwriting files."""

    clock = time.time() if args.now is None else args.now
    if not math.isfinite(clock) or clock <= 0:
        raise ValueError("CLOCK_INVALID")
    state = local_state(args, clock, checks)
    if checks.linked_path(args.output) or not args.output.parent.is_dir():
        raise ValueError("OUTPUT_UNSAFE")
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from telegram_card_png import render_card_png

    with tempfile.TemporaryDirectory(
        prefix=".online-acceptance-", dir=args.output.parent
    ) as folder:
        image = Path(folder) / "card.png"
        report = render_card_png(state, image, now=clock)
        image.chmod(0o600)
        check_png(image, report)
        action = checks.deliver_png(image, args.output)
    return {
        "status": "PASS",
        "live_voltage_visible": True,
        "voltage_v": state["v"],
        "frequency_hz": state.get("f"),
        "forecast_visible": report["forecast_visible"],
        "image_size": "960x560",
        "output_action": action,
        "telegram_sent": False,
    }


def main() -> int:
    """Return 0 for verified online cards; 2 for a safe refusal."""

    args = arguments()
    try:
        result = accept(args, shared_checks())
    except Exception as exc:
        reason = str(exc)
        if reason not in {
            "NOT_ONLINE",
            "FORECAST_INVALID",
            "STATE_INVALID",
            "NO_FRESH_MEASUREMENT",
            "PNG_INVALID",
            "CLOCK_INVALID",
            "OUTPUT_UNSAFE",
            "OUTPUT_EXISTS_DIFFERENT",
            "INPUT_UNSAFE",
            "INPUT_TOO_LARGE",
            "INPUT_INVALID",
        }:
            reason = "ACCEPTANCE_UNAVAILABLE"
        result = {"status": "FAIL", "reason": reason, "telegram_sent": False}
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("ONLINE ACCEPTANCE: " + result["status"])
        if result["status"] == "FAIL":
            print("Reason: " + result["reason"])
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
