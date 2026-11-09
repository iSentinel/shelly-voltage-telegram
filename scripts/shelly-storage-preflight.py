#!/usr/bin/env python3
"""Check Shelly storage headroom and backup age without changing files."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from pathlib import Path

MIB = 1024 * 1024


def parser() -> argparse.ArgumentParser:
    """Create a bounded, non-interactive storage health CLI."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "--data-dir", type=Path, default=Path("/var/lib/shelly-voltage")
    )
    cli.add_argument(
        "--backup-dir", type=Path, default=Path("/var/backups/shelly-voltage")
    )
    cli.add_argument("--warn-free-mib", type=float, default=1024)
    cli.add_argument("--fail-free-mib", type=float, default=256)
    cli.add_argument("--max-backup-age-hours", type=float, default=36)
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--json", action="store_true")
    return cli


def validate_args(args: argparse.Namespace) -> None:
    """Check operator thresholds before accessing the filesystem."""

    values = (
        args.fail_free_mib,
        args.warn_free_mib,
        args.max_backup_age_hours,
    )
    if not all(
        isinstance(v, (int, float)) and math.isfinite(v) for v in values
    ):
        raise ValueError("threshold must be finite")
    if not 0 < args.fail_free_mib < args.warn_free_mib:
        raise ValueError("invalid free-space thresholds")
    if not 0 < args.max_backup_age_hours <= 720:
        raise ValueError("invalid backup age threshold")


def backup_summary(path: Path, now: float) -> tuple[int, float | None, int]:
    """Count plausible backups without reading their contents."""

    valid = []
    ignored = 0
    for file in path.glob("voltage-*.sqlite3.gz"):
        if file.is_symlink() or not file.is_file():
            ignored += 1
            continue
        metadata = file.stat()
        if metadata.st_size <= 0 or metadata.st_mtime > now + 300:
            ignored += 1
            continue
        valid.append(metadata.st_mtime)
    if not valid:
        return 0, None, ignored
    return len(valid), max(0.0, (now - max(valid)) / 3600), ignored


def storage_status(reasons: list[str]) -> str:
    """Separate hard capacity failures from warning-only conditions."""

    if any(
        key in reasons for key in ("insufficient_free_space", "backup_missing")
    ):
        return "fail"
    return "warn" if reasons else "pass"


def check_storage(args: argparse.Namespace, now: float | None = None) -> dict:
    """Classify current storage capacity and backup recency."""

    validate_args(args)
    now = time.time() if now is None else now
    if not math.isfinite(now):
        raise ValueError("invalid current timestamp")
    usage = shutil.disk_usage(args.data_dir)
    count, backup_age, ignored = backup_summary(args.backup_dir, now)
    free_mib = usage.free / MIB
    reasons = []
    if free_mib < args.fail_free_mib:
        reasons.append("insufficient_free_space")
    elif free_mib < args.warn_free_mib:
        reasons.append("low_free_space")
    if count == 0:
        reasons.append("backup_missing")
    elif backup_age is not None and backup_age > args.max_backup_age_hours:
        reasons.append("backup_stale")
    if ignored:
        reasons.append("backup_metadata_untrusted")
    status = storage_status(reasons)
    return {
        "status": status,
        "reasons": reasons,
        "free_mib": round(free_mib, 1),
        "filesystem_used_percent": round(100 * usage.used / usage.total, 1),
        "backup_count": count,
        "ignored_backup_entries": ignored,
        "latest_backup_age_hours": round(backup_age, 1)
        if backup_age is not None
        else None,
        "filesystem_writes": 0,
        "telegram_actions": 0,
        "shelly_rpc_calls": 0,
        "private_paths_disclosed": False,
    }


def main() -> int:
    """Use 0 PASS/dry run, 2 FAIL, or 3 WARN as stable exit codes."""

    args = parser().parse_args()
    try:
        validate_args(args)
        state = (
            {"status": "dry_run", "reasons": [], "filesystem_writes": 0}
            if args.dry_run
            else check_storage(args)
        )
    except (OSError, ValueError, ZeroDivisionError):
        state = {"status": "fail", "reasons": ["storage_unavailable"]}
    if args.json:
        print(json.dumps(state, ensure_ascii=False, allow_nan=False))
    else:
        print(
            f"{state['status'].upper()}: {', '.join(state['reasons']) or 'ok'}"
        )
    return {"pass": 0, "dry_run": 0, "warn": 3, "fail": 2}[state["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
