#!/usr/bin/env python3
"""Read-only audit of every known Shelly writer startup entry point."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SOURCE_ROOT = Path("/opt/shelly-voltage")
UNIT_ROOT = Path("/etc/systemd/system")
EMERGENCY = Path("/usr/local/sbin/shelly-graphic-emergency-restore")

SOURCE_FILES = {
    "compose_yaml": "compose.yaml",
    "updater": "scripts/apply-update.sh",
    "installer": "scripts/install.sh",
    "rollback": "scripts/rollback.sh",
    "compose_start": "scripts/start-compose.sh",
}
UNIT_FILES = {
    "compose_unit": "shelly-voltage-compose.service",
    "manual_updater_unit": "shelly-voltage-updater-apply@.service",
    "update_check_unit": "shelly-voltage-update-check.service",
    "backup_unit": "shelly-voltage-backup.service",
    "yasno_unit": "shelly-yasno-refresh.service",
    "emergency_unit": "shelly-graphic-emergency-boot.service",
}
MAX_SOURCE_BYTES = 128 * 1024


def load_fixed(path: Path) -> str:
    """Read only one pinned regular source file and reject unsafe links."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("MISSING_OR_LINKED_FILE")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("SOURCE_SIZE_LIMIT")
    return path.read_text(encoding="utf-8")


def collect() -> tuple[dict[str, str], list[str]]:
    """Only read exact known code/unit files; never read credentials."""
    code: dict[str, str] = {}
    errors: list[str] = []
    locations = {
        **{name: SOURCE_ROOT / rel for name, rel in SOURCE_FILES.items()},
        **{name: UNIT_ROOT / rel for name, rel in UNIT_FILES.items()},
        "emergency_script": EMERGENCY,
    }
    for name, path in locations.items():
        try:
            code[name] = load_fixed(path)
        except (OSError, ValueError, UnicodeError):
            errors.append(f"SOURCE_{name.upper()}_UNVERIFIED")
    return code, errors


def has(text: str, pattern: str) -> bool:
    """Test a fixed source pattern without executing the source."""
    return re.search(pattern, text, flags=re.MULTILINE) is not None


def startup_unit_findings(code: dict[str, str]) -> list[str]:
    """Detect systemd entry points beyond ordinary ExecStart."""
    risks = []
    unit = code.get("compose_unit", "")
    expected = {
        "ExecStartPre": (
            r"^ExecStartPre=.*apply-update\.sh\s+--recover",
            "BOOT_RECOVERY_PRESTART_REQUIRES_B_A_REVIEW",
        ),
        "ExecStart": (
            r"^ExecStart=.*start-compose\.sh",
            "COMPOSE_START_REQUIRES_B_FENCE",
        ),
        "ExecReload": (
            r"^ExecReload=.*start-compose\.sh",
            "COMPOSE_RELOAD_REQUIRES_B_FENCE",
        ),
        "ExecStop": (
            r"^ExecStop=.*docker compose stop",
            "COMPOSE_STOP_REQUIRES_MAINTENANCE_FENCE",
        ),
    }
    for name, (pattern, reason) in expected.items():
        if has(unit, pattern):
            risks.append(reason)
        else:
            risks.append(f"{name.upper()}_ENTRYPOINT_UNVERIFIED")
    if has(
        code.get("manual_updater_unit", ""),
        r"^ExecStart=.*apply-update\.sh\s+--version",
    ):
        risks.append("MANUAL_UPDATER_REQUIRES_B_A_FENCE")
    else:
        risks.append("MANUAL_UPDATER_ENTRYPOINT_UNVERIFIED")
    return risks


def source_start_findings(code: dict[str, str]) -> list[str]:
    """Detect direct Compose owners and changed/unknown source paths."""
    checks = {
        "compose_yaml": (
            r"^\s*restart:\s*unless-stopped",
            "DOCKER_AUTO_RESTART_CAN_BYPASS_PENDING",
        ),
        "compose_start": (
            r"compose_cmd\s+up\s+-d",
            "START_COMPOSE_DIRECT_UP_REQUIRES_OWNER_GUARD",
        ),
        "installer": (
            r"start-compose\.sh.*--build",
            "INSTALLER_DIRECT_START_REQUIRES_B_FENCE",
        ),
        "rollback": (
            r"compose_cmd\s+up\s+-d",
            "ROLLBACK_DIRECT_START_REQUIRES_B_FENCE",
        ),
        "emergency_script": (
            r"--restart=unless-stopped",
            "EMERGENCY_RESTORE_CAN_REENABLE_DOCKER_RESTART",
        ),
    }
    reasons = []
    for key, (pattern, risk) in checks.items():
        reasons.append(
            risk
            if has(code.get(key, ""), pattern)
            else f"{key.upper()}_SOURCE_UNVERIFIED"
        )
    return reasons


def mutex_findings(code: dict[str, str]) -> list[str]:
    """Keep A updater mutex distinct from a future maintenance B lock."""
    risks = []
    script = code.get("updater", "")
    if has(script, r"/run/lock/shelly-voltage-updater\.lock"):
        risks.append("EXISTING_A_MUTEX_NOT_GLOBAL_B_FENCE")
    else:
        risks.append("UPDATER_A_MUTEX_MISSING_OR_CHANGED")
    if has(
        code.get("emergency_unit", ""), r"^ExecStart=.*--restore --execute"
    ):
        risks.append("EMERGENCY_BOOT_RESTORE_PATH_REQUIRES_REVIEW")
    else:
        risks.append("EMERGENCY_BOOT_RESTORE_ENTRYPOINT_UNVERIFIED")
    return risks


def classify_non_writer_units(
    code: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Classify known read-only units; flag unexpected unit changes."""
    classification = []
    errors = []
    checks = {
        "update_check_unit": (
            r"^ExecStart=.*github_update\.py\s+check",
            "RELEASE_CHECK_METADATA_ONLY",
        ),
        "backup_unit": (
            r"^ExecStart=.*scripts/backup\.sh",
            "BACKUP_ONLINE_SQLITE_NOT_WRITER_START",
        ),
        "yasno_unit": (
            r"^ExecStart=.*scripts/yasno-refresh\.py",
            "YASNO_REFRESH_NOT_WRITER_START",
        ),
    }
    for name, (pattern, note) in checks.items():
        if has(code.get(name, ""), pattern):
            classification.append(note)
        else:
            errors.append(f"{name.upper()}_BEHAVIOR_UNVERIFIED")
    return classification, errors


def evaluate(code: dict[str, str], errors: list[str]) -> dict:
    """Fail closed; structural source findings never authorize deployment."""
    risks = errors.copy()
    for func in (startup_unit_findings, source_start_findings, mutex_findings):
        risks.extend(func(code))
    classified, uncertain = classify_non_writer_units(code)
    risks.extend(uncertain)
    result = {
        "status": "BLOCKED" if risks else "REVIEW_REQUIRED",
        "reasons": sorted(set(risks)),
        "non_writer_classification": classified,
        "files_verified": sorted(code.keys()),
        "all_writer_owners_fenced": False,
        "owner_maintenance_authorized": False,
        "production_modified": False,
        "telegram_photo_authorized": False,
    }
    return result


def main() -> int:
    """Default inert; explicit live flag only reads known public source."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--live-read-only", action="store_true")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or not args.live_read_only:
        print("DRY-RUN PASS: no source, service or SQLite inspected")
        return 0
    code, errors = collect()
    result = evaluate(code, errors)
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("WRITER OWNER GRAPH:", result["status"])
        for reason in result["reasons"]:
            print(" -", reason)
        print("READ-ONLY. NO FENCE, INSTALL OR TELEGRAM APPROVAL.")
    return 2 if result["status"] == "BLOCKED" else 3


if __name__ == "__main__":
    raise SystemExit(main())
