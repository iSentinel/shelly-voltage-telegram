#!/usr/bin/env python3
"""Run read-only checks before promoting the current software candidate."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIVE_ENV = Path("/opt/shelly-voltage/.env")
DEFAULT_DB = Path("/var/lib/shelly-voltage/voltage.sqlite3")
DEFAULT_BASELINE = ROOT / "validation/0.5.6-hw-blackout-baseline.json"


def load_recovery_module() -> ModuleType:
    """Load the existing recovery checker without duplicating its logic."""

    path = ROOT / "scripts" / "recovery-acceptance.py"
    spec = importlib.util.spec_from_file_location("recovery_acceptance", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load recovery acceptance module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def read_version(root: Path, name: str) -> str:
    """Read one required version marker."""

    path = root / name
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"cannot read {name}") from exc
    if not value:
        raise ValueError(f"{name} is empty")
    return value


def read_env_value(path: Path, key: str) -> str:
    """Read one non-secret deployment setting from an env file."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("cannot read live deployment env") from exc
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return ""


def version_payload(root: Path) -> dict[str, str]:
    """Return candidate, public-release, and live version markers."""

    return {
        "candidate": read_version(root, "VERSION"),
        "release": read_version(root, "RELEASE_VERSION"),
        "live": read_version(root, "LIVE_VERSION"),
    }


def recovery_args(args: argparse.Namespace) -> argparse.Namespace:
    """Build arguments accepted by the existing recovery checker."""

    return argparse.Namespace(
        db=args.db,
        health_url=args.health_url,
        ready_url=args.ready_url,
        timeout=args.timeout,
        baseline=args.baseline,
        output=None,
    )


def promotion_errors(
    versions: dict[str, str],
    meter_type: str,
    recovery: dict[str, object],
) -> list[str]:
    """Return reasons why promotion must remain blocked."""

    errors = list(recovery.get("errors", []))
    if recovery.get("status") != "pass" and not errors:
        errors.append("recovery acceptance did not pass")
    health = recovery.get("health") or {}
    if meter_type != "pro_em50":
        errors.append("live meter type is not pro_em50")
    if health.get("version") != versions["live"]:
        errors.append("live service version does not match LIVE_VERSION")
    if versions["candidate"] == versions["live"]:
        errors.append("candidate already matches live checkpoint")
    return errors


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    """Run all read-only pre-promotion checks and aggregate the result."""

    recovery_module = load_recovery_module()
    versions = version_payload(args.root)
    meter_type = read_env_value(args.live_env, "SHELLY_METER_TYPE")
    recovery = recovery_module.verify_payload(recovery_args(args))
    errors = promotion_errors(versions, meter_type, recovery)
    return {
        "status": "pass" if not errors else "fail",
        "read_only": True,
        "versions": versions,
        "meter_type": meter_type or "missing",
        "recovery": recovery,
        "errors": errors,
    }


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--root", type=Path, default=ROOT)
    cli.add_argument("--live-env", type=Path, default=DEFAULT_LIVE_ENV)
    cli.add_argument("--db", type=Path, default=DEFAULT_DB)
    cli.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    cli.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    cli.add_argument("--ready-url", default="http://127.0.0.1:8080/ready")
    cli.add_argument("--timeout", type=float, default=5.0)
    cli.add_argument("--json", action="store_true", dest="json_output")
    return cli


def print_result(payload: dict[str, object], json_output: bool) -> None:
    """Print JSON or one concise promotion result."""

    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    state = str(payload["status"]).upper()
    print(f"PROMOTION_CHECK={state}")
    for error in payload.get("errors", []):
        print(f"- {error}")


def main() -> int:
    """Run the checker and return a stable PASS/FAIL exit code."""

    args = parser().parse_args()
    if args.timeout <= 0:
        print("ERROR: --timeout must be > 0", file=sys.stderr)
        return 2
    try:
        payload = build_payload(args)
    except Exception as exc:
        payload = {"status": "fail", "error_type": exc.__class__.__name__}
    print_result(payload, args.json_output)
    return 0 if payload.get("status") == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
