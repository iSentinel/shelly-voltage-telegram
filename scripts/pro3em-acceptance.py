#!/usr/bin/env python3
"""Run read-only physical acceptance checks for Shelly Pro 3EM/3CT63."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class PhaseReader(Protocol):
    """Protocol implemented by the existing Pro3EM client."""

    def read_phases(self) -> dict[str, tuple[float, float, float]]:
        """Read one atomic L1/L2/L3 cycle."""
        ...


PHASES = ("L1", "L2", "L3")


@dataclass(frozen=True, slots=True)
class AcceptanceConfig:
    """Connection settings needed by the existing Pro3EM client."""

    shelly_host: str
    shelly_user: str
    shelly_password: str
    shelly_timeout: float
    em1_id: int = 0


def read_secret(path: str) -> str:
    """Read an optional password file without exposing its value."""

    if not path:
        return os.getenv("SHELLY_PASSWORD", "").strip()
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read password file: {path}") from exc


def build_config(args: argparse.Namespace) -> AcceptanceConfig:
    """Build the minimal connection config used by Pro3EMClient."""

    host = (args.host or os.getenv("SHELLY_HOST", "")).strip()
    if not host:
        raise ValueError("Shelly host is required")
    password_file = args.password_file or os.getenv("SHELLY_PASSWORD_FILE", "")
    return AcceptanceConfig(
        shelly_host=host,
        shelly_user=args.username,
        shelly_password=read_secret(password_file),
        shelly_timeout=args.timeout,
    )


def validate_args(args: argparse.Namespace) -> None:
    """Reject invalid acceptance thresholds before any network request."""

    if args.samples < 1:
        raise ValueError("--samples must be >= 1")
    if args.interval < 0 or args.timeout <= 0:
        raise ValueError("--interval must be >= 0 and --timeout must be > 0")
    bounds = (
        args.min_voltage,
        args.max_voltage,
        args.min_frequency,
        args.max_frequency,
    )
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("acceptance ranges must be finite")
    if args.min_voltage <= 0 or args.min_voltage >= args.max_voltage:
        raise ValueError("voltage range is invalid")
    if args.min_frequency <= 0 or args.min_frequency >= args.max_frequency:
        raise ValueError("frequency range is invalid")


def finite_number(value: object) -> float | None:
    """Convert numeric RPC data without permitting NaN or infinity."""

    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def readable_number(value: object, decimals: int) -> str:
    """Format a safe number without repeating arbitrary RPC input."""

    number = finite_number(value)
    return f"{number:.{decimals}f}" if number is not None else "invalid"


def validate_readings(
    readings: dict[str, tuple[float, float, float]], args: argparse.Namespace
) -> list[str]:
    """Require one finite, consistent sample for exactly three phases."""

    errors = []
    missing = [phase for phase in PHASES if phase not in readings]
    extra = sorted(phase for phase in readings if phase not in PHASES)
    if missing:
        errors.append("missing phases: " + ",".join(missing))
    if extra:
        errors.append("unexpected phases: " + ",".join(extra))
    latencies = set()
    for phase in PHASES:
        if phase not in readings:
            continue
        voltage, frequency, rpc_ms = readings[phase]
        numeric_voltage = finite_number(voltage)
        numeric_frequency = finite_number(frequency)
        numeric_latency = finite_number(rpc_ms)
        if numeric_voltage is None or not (
            args.min_voltage <= numeric_voltage <= args.max_voltage
        ):
            errors.append(
                f"{phase} voltage out of range: {readable_number(voltage, 1)}"
            )
        if numeric_frequency is None or not (
            args.min_frequency <= numeric_frequency <= args.max_frequency
        ):
            errors.append(
                f"{phase} frequency out of range: "
                f"{readable_number(frequency, 2)}"
            )
        if numeric_latency is None or numeric_latency < 0:
            errors.append(f"{phase} RPC latency is invalid")
        else:
            latencies.add(round(numeric_latency, 6))
    if len(latencies) > 1:
        errors.append("phases did not come from one atomic RPC cycle")
    return errors


def sample_payload(
    client: PhaseReader, args: argparse.Namespace, number: int
) -> dict[str, object]:
    """Read and validate one Pro 3EM atomic sample cycle."""

    captured_at = time.time()
    readings = client.read_phases()
    errors = validate_readings(readings, args)
    phases = {
        phase: {
            "voltage": finite_number(readings[phase][0]),
            "frequency": finite_number(readings[phase][1]),
        }
        for phase in PHASES
        if phase in readings
    }
    valid_latencies = [
        finite_number(readings[phase][2])
        for phase in PHASES
        if phase in readings
    ]
    rpc_ms = max((ms for ms in valid_latencies if ms is not None), default=0.0)
    return {
        "sample": number,
        "captured_at": captured_at,
        "rpc_ms": round(rpc_ms, 1),
        "phases": phases,
        "errors": errors,
        "status": "pass" if not errors else "fail",
    }


def run_acceptance(
    client: PhaseReader, args: argparse.Namespace
) -> dict[str, object]:
    """Run all requested read-only samples and aggregate PASS/FAIL."""

    samples = []
    for number in range(1, args.samples + 1):
        try:
            row = sample_payload(client, args, number)
        except Exception as exc:
            row = {
                "sample": number,
                "status": "fail",
                "error_type": exc.__class__.__name__,
            }
        samples.append(row)
        if number < args.samples and args.interval:
            time.sleep(args.interval)
    passed = all(row["status"] == "pass" for row in samples)
    return {
        "status": "pass" if passed else "fail",
        "meter_type": "pro3em",
        "rpc": "EM.GetStatus?id=0",
        "read_only": True,
        "samples": samples,
    }


def create_client(cfg: AcceptanceConfig) -> PhaseReader:
    """Load the existing runtime client only for a real device check."""

    from app import Pro3EMClient

    return Pro3EMClient(cfg)  # type: ignore[arg-type]


def print_text(payload: dict[str, object]) -> None:
    """Print a concise human-readable acceptance report."""

    for row in payload["samples"]:
        state = str(row["status"]).upper()
        if state == "PASS":
            phases = row["phases"]
            values = " ".join(
                f"{phase}={phases[phase]['voltage']:.1f}V/"
                f"{phases[phase]['frequency']:.2f}Hz"
                for phase in PHASES
            )
            print(f"sample {row['sample']}: PASS {values}")
        else:
            details = row.get("errors") or [row.get("error_type", "error")]
            print(f"sample {row['sample']}: FAIL {'; '.join(details)}")
    print(f"PRO3EM_ACCEPTANCE={str(payload['status']).upper()}")


def parser() -> argparse.ArgumentParser:
    """Build the acceptance CLI parser."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--host", help="Shelly host; defaults to SHELLY_HOST")
    cli.add_argument(
        "--username", default=os.getenv("SHELLY_USERNAME", "admin")
    )
    cli.add_argument("--password-file", help="Digest password file")
    cli.add_argument("--samples", type=int, default=3)
    cli.add_argument("--interval", type=float, default=1.0)
    cli.add_argument("--timeout", type=float, default=5.0)
    cli.add_argument("--min-voltage", type=float, default=50.0)
    cli.add_argument("--max-voltage", type=float, default=300.0)
    cli.add_argument("--min-frequency", type=float, default=40.0)
    cli.add_argument("--max-frequency", type=float, default=70.0)
    cli.add_argument("--json", action="store_true", dest="json_output")
    return cli


def main() -> int:
    """Run the CLI and return a stable PASS/FAIL exit code."""

    args = parser().parse_args()
    try:
        validate_args(args)
        cfg = build_config(args)
        from loguru import logger

        logger.remove()
        logger.add(sys.stderr, level="WARNING")
        payload = run_acceptance(create_client(cfg), args)
    except Exception as exc:
        payload = {"status": "fail", "error_type": exc.__class__.__name__}
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if "samples" in payload:
            print_text(payload)
        else:
            print(f"PRO3EM_ACCEPTANCE=FAIL {payload['error_type']}")
    return 0 if payload["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
