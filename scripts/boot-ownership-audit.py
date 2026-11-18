#!/usr/bin/env python3
"""Read-only audit of systemd recovery and Docker restart ownership."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path

EXPECTED_RECOVER = (
    "/bin/bash /opt/shelly-voltage/scripts/apply-update.sh --recover"
)
EXPECTED_START = "/bin/bash /opt/shelly-voltage/scripts/start-compose.sh"
EXPECTED_STOP = "/usr/bin/docker compose stop"


class Blocked(ValueError):
    """Emit a stable reason code without exposing service environment."""


def unit_text(path: Path) -> str:
    """Accept only regular, non-linked unit files of bounded size."""

    if not path.is_absolute() or path.is_symlink():
        raise Blocked("SERVICE_UNIT_PATH_UNSAFE")
    try:
        info = path.stat()
    except OSError as exc:
        raise Blocked("SERVICE_UNIT_UNAVAILABLE") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Blocked("SERVICE_UNIT_UNSAFE")
    if not 0 < info.st_size <= 65536:
        raise Blocked("SERVICE_UNIT_SIZE_INVALID")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise Blocked("SERVICE_UNIT_READ_FAILED") from exc


def parse_service(text: str) -> dict[str, str]:
    """Read only concrete Exec directives from a systemd Service section."""

    active = False
    result = {}
    for line in text.splitlines():
        value = line.strip()
        if value.startswith("[") and value.endswith("]"):
            active = value == "[Service]"
            continue
        if not active or not value or value.startswith(("#", ";")):
            continue
        if "=" not in value:
            continue
        key, raw = value.split("=", 1)
        if key in ("ExecStartPre", "ExecStart", "ExecStop", "Type"):
            if key in result:
                raise Blocked("SERVICE_UNIT_DUPLICATED_DIRECTIVE")
            result[key] = raw.strip()
    return result


def pending_status(path: Path) -> bool:
    """Read marker presence only; never open untrusted pending contents."""

    if not path.is_absolute() or path.is_symlink():
        raise Blocked("PENDING_MARKER_PATH_UNSAFE")
    if not path.exists():
        return False
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Blocked("PENDING_MARKER_UNSAFE")
    return True


def inspect_restart_policy(name: str) -> tuple[str, bool]:
    """Inspect only restart policy and running state, not secrets or env."""

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise Blocked("CONTAINER_NAME_INVALID")
    command = [
        "docker",
        "inspect",
        name,
        "--format",
        "{{.HostConfig.RestartPolicy.Name}}|{{.State.Running}}",
    ]
    try:
        run = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("CONTAINER_INSPECT_UNAVAILABLE") from exc
    if run.returncode:
        raise Blocked("CONTAINER_INSPECT_FAILED")
    output = run.stdout.strip().split("|")
    if len(output) != 2 or output[1] not in ("true", "false"):
        raise Blocked("CONTAINER_INSPECT_INVALID")
    return output[0], output[1] == "true"


SYSTEMD_KEYS = (
    "FragmentPath",
    "DropInPaths",
    "Type",
    "LoadState",
    "ActiveState",
    "UnitFileState",
    "NeedDaemonReload",
    "ExecStartPre",
    "ExecStart",
    "ExecStop",
)


def inspect_systemd_service(name: str) -> dict[str, str]:
    """Read effective systemd properties without changing loaded units."""

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,95}\.service", name):
        raise Blocked("SYSTEMD_UNIT_NAME_INVALID")
    try:
        run = subprocess.run(
            [
                "systemctl",
                "show",
                name,
                "--no-pager",
                "--property=" + ",".join(SYSTEMD_KEYS),
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("SYSTEMD_INSPECT_UNAVAILABLE") from exc
    if run.returncode or len(run.stdout) > 16384:
        raise Blocked("SYSTEMD_INSPECT_FAILED")
    fields = {}
    for line in run.stdout.splitlines():
        if "=" not in line:
            raise Blocked("SYSTEMD_INSPECT_INVALID")
        key, value = line.split("=", 1)
        if key not in SYSTEMD_KEYS or key in fields:
            raise Blocked("SYSTEMD_INSPECT_INVALID")
        fields[key] = value
    if set(fields) != set(SYSTEMD_KEYS):
        raise Blocked("SYSTEMD_INSPECT_INCOMPLETE")
    return fields


def effective_exec(raw: str) -> str:
    """Parse one loaded systemd command and deny ambiguous execution."""

    if raw.count("{") != 1 or raw.count("}") != 1:
        raise Blocked("SYSTEMD_EXEC_AMBIGUOUS")
    if not raw.startswith("{") or not raw.endswith("}"):
        raise Blocked("SYSTEMD_EXEC_AMBIGUOUS")
    args = re.findall(r"argv\[\]=([^;{}]+);", raw)
    paths = re.findall(r"(?:\{|;)\s*path=([^;{}]+);", raw)
    ignore = re.findall(r"(?:\{|;)\s*ignore_errors=([^;{}]+);", raw)
    if len(args) != 1 or len(paths) != 1 or len(ignore) != 1:
        raise Blocked("SYSTEMD_EXEC_AMBIGUOUS")
    argv = args[0].strip()
    if (
        not argv.startswith(paths[0].strip() + " ")
        and argv != paths[0].strip()
    ):
        raise Blocked("SYSTEMD_EXEC_PATH_MISMATCH")
    if ignore[0].strip() != "no":
        raise Blocked("SYSTEMD_EXEC_IGNORED_ERROR")
    return argv


def effective_issues(fields: dict[str, str], installed: Path) -> list[str]:
    """Refuse loaded-vs-source unit mismatches and unreviewed drop-ins."""

    issues = []
    if fields["FragmentPath"] != str(installed):
        issues.append("BOOT_EFFECTIVE_FRAGMENT_DRIFT")
    if fields["DropInPaths"].strip():
        issues.append("BOOT_EFFECTIVE_DROPINS_UNREVIEWED")
    if fields["Type"] != "oneshot" or fields["ActiveState"] != "active":
        issues.append("BOOT_EFFECTIVE_SERVICE_STATE_UNVERIFIED")
    if fields["LoadState"] != "loaded":
        issues.append("BOOT_SERVICE_NOT_LOADED")
    if fields["UnitFileState"] != "enabled":
        issues.append("BOOT_SERVICE_NOT_ENABLED")
    if fields["NeedDaemonReload"] != "no":
        issues.append("BOOT_DAEMON_RELOAD_REQUIRED")
    for key, expected, reason in (
        ("ExecStartPre", EXPECTED_RECOVER, "BOOT_EFFECTIVE_RECOVERY_CHANGED"),
        ("ExecStart", EXPECTED_START, "BOOT_EFFECTIVE_START_CHANGED"),
        ("ExecStop", EXPECTED_STOP, "BOOT_EFFECTIVE_STOP_CHANGED"),
    ):
        if effective_exec(fields[key]) != expected:
            issues.append(reason)
    return issues


def unit_issues(installed: str, source: str) -> list[str]:
    """Return unexpected or mismatched systemd execution directives."""

    live = parse_service(installed)
    repo = parse_service(source)
    issues = []
    if live.get("ExecStartPre") != EXPECTED_RECOVER:
        issues.append("BOOT_RECOVERY_GATE_MISSING_OR_CHANGED")
    if live.get("ExecStart") != EXPECTED_START:
        issues.append("BOOT_START_PATH_UNEXPECTED")
    if live.get("ExecStop") != EXPECTED_STOP:
        issues.append("BOOT_STOP_PATH_UNEXPECTED")
    if live.get("Type") != "oneshot":
        issues.append("BOOT_SERVICE_TYPE_UNEXPECTED")
    if live != repo:
        issues.append("BOOT_INSTALLED_SOURCE_DRIFT")
    return issues


def assess(
    installed: str,
    source: str,
    pending: bool,
    restart_policy: str,
    running: bool,
) -> dict:
    """Block unsafe boot ownership, pending updates and source drift."""

    issues = unit_issues(installed, source)
    if pending:
        issues.append("BOOT_PENDING_RECOVERY_REQUIRES_REVIEW")
    if restart_policy in ("always", "unless-stopped", "on-failure"):
        issues.append("BOOT_DOCKER_RESTART_OWNER_UNFENCED")
    if not running:
        issues.append("BOOT_CONTAINER_NOT_RUNNING")
    if restart_policy not in ("no", "always", "unless-stopped", "on-failure"):
        issues.append("BOOT_RESTART_POLICY_UNKNOWN")
    return {
        "status": "BLOCKED" if issues else "PASS_STATIC_EVIDENCE_ONLY",
        "reasons": issues,
        "pending_marker_present": pending,
        "docker_running": running,
        "docker_restart_policy": restart_policy,
        "installed_unit_sha256": hashlib.sha256(
            installed.encode()
        ).hexdigest(),
        "source_unit_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "boot_continuity_verified": False,
        "single_writer_fenced_verified": False,
        "source_install_authorized": False,
        "rollback_authorized": False,
        "production_modified": False,
    }


def read_live_report(args: argparse.Namespace) -> dict:
    """Combine source files, Docker ownership and effective systemd state."""

    report = assess(
        unit_text(args.installed_unit),
        unit_text(args.source_unit),
        pending_status(args.pending_marker),
        *inspect_restart_policy(args.container),
    )
    reasons = effective_issues(
        inspect_systemd_service(args.unit_name), args.installed_unit
    )
    report["reasons"].extend(reasons)
    if reasons:
        report["status"] = "BLOCKED"
    report["effective_systemd_inspected"] = True
    return report


def main() -> int:
    """Print one PASS/BLOCKED report; never call systemctl start or stop."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-unit", type=Path, required=True)
    parser.add_argument("--source-unit", type=Path, required=True)
    parser.add_argument("--pending-marker", type=Path, required=True)
    parser.add_argument("--container", default="shelly-voltage-bot")
    parser.add_argument(
        "--unit-name", default="shelly-voltage-compose.service"
    )
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        report = read_live_report(args)
    except Blocked as exc:
        report = {
            "status": "BLOCKED",
            "reasons": [str(exc)],
            "production_modified": False,
            "boot_continuity_verified": False,
            "rollback_authorized": False,
        }
    if args.jsonl:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        print("BOOT OWNER AUDIT:", report["status"])
        for reason in report["reasons"]:
            print("BLOCKED:", reason)
    return 0 if report["status"] == "PASS_STATIC_EVIDENCE_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
