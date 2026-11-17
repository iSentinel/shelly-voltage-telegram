#!/usr/bin/env python3
"""Audit sole-owner NVMe risk acceptance without authorizing a release."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = {
    "issue": 402,
    "owner_approval": "GRANTED",
    "owner_approved_single_nvme_risk": True,
    (
        "owner_acknowledged_potential_irrecoverable_history_"
        "and_local_backup_loss"
    ): True,
    "sole_owner_review_model_approved": True,
    "physical_backup_failure_domains": 1,
    "additional_independent_device_planned": False,
    "independent_code_reviewer_available": False,
    "offhost_backup_verified": False,
    "release_authorized": False,
    "specific_production_cutover_approved": False,
    "public_telegram_photo_approved": False,
}
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def protected_receipt(path: Path) -> dict:
    """Read one private regular file without trusting symbolic links."""

    if path.is_symlink() or any(
        parent.is_symlink() for parent in path.parents
    ):
        raise ValueError("symlink in owner receipt path")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or not 64 <= info.st_size <= 8192
    ):
        raise ValueError("owner receipt file metadata invalid")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("owner receipt is not an object")
    return result


def owner_risk_valid(data: dict, now: datetime) -> bool:
    """Accept the explicit risk decision without inventing release scope."""

    for key, expected in REQUIRED.items():
        value = data.get(key)
        if type(value) is not type(expected) or value != expected:
            return False
    if data.get("exact_version_or_tag_approved") is not None:
        return False
    if data.get("sole_owner_source_review_of_specific_candidate") is not None:
        return False
    if data.get("signed_current_oci_verified") is not False:
        return False
    when = datetime.fromisoformat(data["recorded_at_kyiv"])
    return when.tzinfo is not None and when <= now


def inspect(path: Path, candidate: str, now: datetime) -> dict:
    """Return distinct risk and release statuses, never automatic approval."""

    if not COMMIT_PATTERN.fullmatch(candidate):
        raise ValueError("candidate commit format invalid")
    accepted = owner_risk_valid(protected_receipt(path), now)
    return {
        "status": "RISK_ACCEPTED" if accepted else "BLOCKED",
        "reason": "ok" if accepted else "owner_receipt_invalid",
        "candidate_commit_label": candidate,
        "candidate_commit_verified": False,
        "owner_single_nvme_risk_approved": accepted,
        "sole_owner_review_model_approved": accepted,
        "offhost_backup_verified": False,
        "source_review_approved": False,
        "signed_oci_verified": False,
        "release_authorized": False,
        "photo_authorized": False,
    }


def arguments() -> argparse.Namespace:
    """Parse the read-only governance check and fail-closed release mode."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--risk-receipt", required=True, type=Path)
    cli.add_argument("--candidate-commit", required=True)
    cli.add_argument("--enforce-release", action="store_true")
    cli.add_argument("--jsonl", action="store_true")
    return cli.parse_args()


def main() -> int:
    """Report a valid risk waiver separately from release authorization."""

    args = arguments()
    try:
        report = inspect(
            args.risk_receipt,
            args.candidate_commit,
            datetime.now(timezone.utc),
        )
    except (ValueError, OSError, KeyError, TypeError, UnicodeError):
        report = {"status": "BLOCKED", "reason": "receipt_unavailable"}
    report["release_authorized"] = False
    if args.jsonl:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"OWNER RISK AUDIT: {report['status']}")
    if args.enforce_release and report["status"] == "RISK_ACCEPTED":
        return 2
    return 0 if report["status"] == "RISK_ACCEPTED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
