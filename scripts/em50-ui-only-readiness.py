"""Read-only design-only diagnostics; never authorize an EM-50 release."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path("/opt/shelly-voltage")
REQUIRED_CHECKS = (
    "source_candidate",
    "live_health",
    "sqlite_readonly",
    "latest_backup_sha256",
    "predeploy_backup_parity",
)


def load_script(name: str, filename: str):
    """Load a tested release gate without copying its security logic."""

    source = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, source)
    if spec is None or spec.loader is None:
        raise ValueError("readiness dependency unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolved_service(candidate: Path, runtime: Path) -> dict:
    """Resolve prospective Compose in memory; never log its secrets."""

    source = candidate / "compose.yaml"
    overlay = candidate / "deploy/compose.ui-only.example.yml"
    if not all(
        file.is_file() and not file.is_symlink() for file in (source, overlay)
    ):
        raise ValueError("candidate Compose or UI-only overlay is missing")
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(runtime),
        "-f",
        str(source),
        "-f",
        str(overlay),
        "config",
        "--format",
        "json",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=20
    )
    if result.returncode != 0 or len(result.stdout) > 1024 * 1024:
        raise ValueError("effective candidate Compose could not be resolved")
    service = json.loads(result.stdout)["services"].get("shelly-voltage")
    if not isinstance(service, dict):
        raise ValueError("expected Shelly service not present")
    return service


def effective_configuration(candidate: Path, runtime: Path) -> str:
    """Require explicit YASNO-off without an effective reader mount."""

    service = resolved_service(candidate, runtime)
    environment = service.get("environment")
    if not isinstance(environment, dict):
        raise ValueError("effective service environment is not a mapping")
    if environment.get("TELEGRAM_YASNO_STATUS_ENABLED") != "0":
        raise ValueError("YASNO must be explicitly disabled")
    volumes = service.get("volumes", [])
    if not isinstance(volumes, list):
        raise ValueError("candidate mounts cannot be verified")
    for volume in volumes:
        if not isinstance(volume, dict) or not isinstance(
            volume.get("target"), str
        ):
            raise ValueError("candidate mount is not a resolved mapping")
        if volume["target"].startswith("/run/yasno"):
            raise ValueError("UI-only mode cannot mount YASNO cache")
    return "PASS"


def release_arguments(args: argparse.Namespace) -> SimpleNamespace:
    """Keep source and runtime inputs aligned with strict full preflight."""

    return SimpleNamespace(
        root=str(args.candidate_root),
        health_url="http://127.0.0.1:8080/health",
        ready_url="http://127.0.0.1:8080/ready",
        db=str(args.db),
        backup_dir=str(args.backup_dir),
        cache="/var/lib/shelly-yasno-reader/yasno.json",
        group="10.1",
        recipient_gid=10001,
        max_age_hours=26,
        acceptance_evidence=args.acceptance_evidence,
    )


def compose_status(args: argparse.Namespace) -> str:
    """Turn Compose failures into a safe boolean-style outcome."""

    try:
        return effective_configuration(args.candidate_root, args.runtime_root)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.TimeoutExpired,
    ):
        return "FAIL"


def real_recovery_status(args: argparse.Namespace) -> str:
    """Require real fifteen-minute telemetry without changing SQLite."""

    recovery = load_script(
        "ui_only_recovery_gate", "em50-posthoc-recovery-audit.py"
    )
    try:
        data = recovery.inspect_database(
            args.db, args.incident_id, time.time()
        )
        return data.get("status", "blocked")
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return "blocked"


def normal_voltage_status(args: argparse.Namespace) -> str:
    """Require the recent fifteen-minute band to be entirely normal."""

    stability = load_script(
        "ui_only_normal_window", "em50-recovery-stability.py"
    )
    acceptance = load_script("ui_only_normal_bounds", "recovery-acceptance.py")
    request = SimpleNamespace(
        db=str(args.db),
        health_url="http://127.0.0.1:8080/health",
        ready_url="http://127.0.0.1:8080/ready",
        max_gap_seconds=30,
        min_samples=60,
    )
    try:
        result = stability.inspect(request, time.time())
        bounds = acceptance.configured_normal_bounds(args.db)
        if bounds is None or result.get("outage_id") != args.incident_id:
            return "FAIL"
        low = float(result["voltage_min"])
        high = float(result["voltage_max"])
        if not all(math.isfinite(v) for v in (low, high)):
            return "FAIL"
        return (
            "PASS"
            if result.get("status") == "PASS"
            and bounds[0] < low <= high < bounds[1]
            else "FAIL"
        )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        sqlite3.Error,
        OverflowError,
    ):
        return "FAIL"


def diagnostic_result(
    original: dict, compose: str, recovered: str, normal: str
) -> dict:
    """Keep all release manual gates despite optional YASNO diagnostics."""

    required = all(
        original["checks"][name]["status"] == "PASS"
        for name in REQUIRED_CHECKS
    )
    eligible = (
        required
        and compose == "PASS"
        and recovered == "pass"
        and normal == "PASS"
    )
    return {
        "scope": "design_only_diagnostic_not_a_release_gate",
        "status": "MANUAL_REVIEW_ONLY" if eligible else "BLOCKED",
        "automated_non_yasno_pass": eligible,
        "compose_yasno_disabled": compose,
        "natural_recovery_15min": recovered,
        "normal_voltage_15min": normal,
        "full_release_checks": {
            name: value["status"] for name, value in original["checks"].items()
        },
        "private_telegram_receipt": original["manual_evidence_status"],
        "manual_pending": list(original["manual_pending"])
        + [
            "normal_voltage_baseline_and_threshold_review",
            "real_client_visual_design_PNG_signoff",
            "independent_source_review",
            "explicit_signed_UI_only_release_and_rollback_authorization",
        ],
        "release_authorized": False,
    }


def inspect(args: argparse.Namespace) -> dict:
    """Reuse release checks without granting rollout permission."""

    release = load_script("ui_only_release_gate", "em50-release-preflight.py")
    original = release.inspect(release_arguments(args))
    return diagnostic_result(
        original,
        compose_status(args),
        real_recovery_status(args),
        normal_voltage_status(args),
    )


def main() -> int:
    """Report technical prerequisites; never return deployment approval."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, default=ROOT)
    parser.add_argument("--runtime-root", type=Path, default=RUNTIME_ROOT)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--incident-id", type=int, required=True)
    parser.add_argument("--acceptance-evidence")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.incident_id < 1:
        parser.error("incident-id must be positive")
    try:
        report = inspect(args)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        report = {
            "status": "BLOCKED",
            "release_authorized": False,
            "reason": type(error).__name__,
        }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(report["status"], "release_authorized=false")
    return 2 if report["status"] == "MANUAL_REVIEW_ONLY" else 1


if __name__ == "__main__":
    sys.exit(main())
