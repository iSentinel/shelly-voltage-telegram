#!/usr/bin/env python3
"""Safely verify Shelly meter RPCs without changing the device or database."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app  # noqa: E402


def parser() -> argparse.ArgumentParser:
    """Describe a bounded, non-interactive, read-only hardware check."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--model", required=True, choices=tuple(app.METER_MODELS))
    cli.add_argument("--samples", type=int, default=3)
    cli.add_argument("--interval", type=float, default=0.2)
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--json", action="store_true")
    return cli


def validate_args(args: argparse.Namespace) -> None:
    """Reject invalid sampling limits before opening a network connection."""

    if not 1 <= args.samples <= 10:
        raise ValueError("samples must be between 1 and 10")
    if not math.isfinite(args.interval) or not 0 <= args.interval <= 5:
        raise ValueError("interval must be a finite number from 0 to 5")


def meter_client(cfg: app.Config, model: str) -> app.ShellyClient:
    """Select the same read-only adapter used by the live monitor."""

    if model == "pro3em":
        return app.Pro3EMClient(cfg)
    if model == "pro_em50":
        return app.ShellyClient(cfg)
    return app.RpcMeterClient(cfg, app.METER_RPC[model])


def validated_sample(raw: object) -> tuple[float, float, float]:
    """Check numeric RPC values without requiring a newer deployed app."""

    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise RuntimeError("invalid meter sample")
    if any(isinstance(value, bool) for value in raw):
        raise RuntimeError("invalid meter sample")
    try:
        voltage, frequency, latency = (float(value) for value in raw)
    except (ValueError, TypeError, OverflowError):
        raise RuntimeError("invalid meter sample") from None
    if not all(
        math.isfinite(value) for value in (voltage, frequency, latency)
    ):
        raise RuntimeError("invalid meter sample")
    if not (0 < voltage < 500 and 40 <= frequency <= 70 and latency >= 0):
        raise RuntimeError("meter sample out of range")
    return voltage, frequency, latency


def phase_sample(client: app.ShellyClient, model: str) -> dict:
    """Read one atomic RPC sample and validate every required phase."""

    if model == "pro3em":
        measured = client.read_phases()
        if set(measured) != {"L1", "L2", "L3"}:
            raise RuntimeError("three-phase sample is incomplete")
    else:
        measured = {"L1": client.read()}
    result = {}
    for phase, values in measured.items():
        voltage, frequency, latency = validated_sample(values)
        result[phase] = {
            "voltage_v": round(voltage, 1),
            "frequency_hz": round(frequency, 2),
            "rpc_ms": round(latency, 1),
        }
    return result


def validate_device_identity(info: object) -> dict:
    """Report non-secret device identity, uptime and clock health only."""

    if not isinstance(info, dict) or not info.get("model"):
        raise RuntimeError("device identity missing")
    uptime = info.get("uptime")
    skew = info.get("time_skew_sec")
    if not isinstance(uptime, (int, float)) or not math.isfinite(uptime):
        raise RuntimeError("device uptime invalid")
    if skew is not None and not math.isfinite(float(skew)):
        raise RuntimeError("device clock skew invalid")
    return {
        "reported_model": str(info["model"])[:64],
        "firmware": str(info.get("firmware") or "unknown")[:64],
        "uptime_seconds": round(float(uptime), 1),
        "clock_skew_seconds": round(float(skew), 2)
        if skew is not None
        else None,
    }


def run_probe(client: app.ShellyClient, args: argparse.Namespace) -> dict:
    """Collect a bounded number of real read-only samples."""

    identity = validate_device_identity(client.device_health())
    readings = []
    for index in range(args.samples):
        if index:
            time.sleep(args.interval)
        readings.append(phase_sample(client, args.model))
    return {
        "status": "pass",
        "read_only": True,
        "model_adapter": args.model,
        "identity": identity,
        "samples": readings,
        "physical_family_independently_verified": False,
        "production_writes": 0,
        "telegram_writes": 0,
    }


def main() -> int:
    """Execute a read-only probe and print a sanitized PASS/FAIL result."""

    args = parser().parse_args()
    try:
        validate_args(args)
        if args.dry_run:
            result = {
                "status": "dry_run",
                "model_adapter": args.model,
                "samples_requested": args.samples,
                "network_calls": 0,
            }
        else:
            cfg = app.load_config()
            result = run_probe(meter_client(cfg, args.model), args)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
        result = {"status": "fail", "reason": type(error).__name__}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    else:
        print(
            f"{result['status'].upper()}: Shelly hardware read-only preflight"
        )
    return 2 if result["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
