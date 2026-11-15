#!/usr/bin/env python3
"""Read-only inspection of legacy production owners before G/B/W/A cutover."""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path("/opt/shelly-voltage")
CONTAINER = "shelly-voltage-bot"
UNIT = "shelly-voltage-compose.service"
LOCK_G = Path("/run/shelly-owner/G")
CURRENT_DB = Path("/var/lib/shelly-voltage/voltage.sqlite3")
BACKUPS = Path("/var/backups/shelly-voltage")
SCRIPTS = (
    "compose.yaml",
    "scripts/apply-update.sh",
    "scripts/start-compose.sh",
    "scripts/install.sh",
    "scripts/rollback.sh",
)


def run_readonly(args: list[str]) -> str | None:
    """Run one fixed read-only inspection with no shell or environment."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return result.stdout


def docker_findings() -> list[str]:
    """An old running/autorestarting Docker writer blocks migration."""
    data = run_readonly(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            CONTAINER,
        ]
    )
    if data is None:
        return ["DOCKER_INSPECT_UNAVAILABLE"]
    fields = data.strip().split("|")
    if len(fields) != 2 or fields[0] not in {"true", "false"}:
        return ["DOCKER_STATE_UNVERIFIABLE"]
    found = []
    if fields[0] == "true":
        found.append("OLD_WRITER_NOT_QUIESCENT")
    if fields[1] != "no":
        found.append("DOCKER_AUTORESTART_NOT_FROZEN")
    return found


def systemd_findings() -> list[str]:
    """Read actual systemd owners without changing any unit or timer."""
    unit = run_readonly(["systemctl", "cat", UNIT])
    if not unit:
        return ["SYSTEMD_OWNER_UNVERIFIABLE"]
    hazards = (
        ("ExecStartPre=", "apply-update.sh", "RECOVER_PRESTART_UNGATED"),
        ("ExecStart=", "start-compose.sh", "COMPOSE_START_UNGATED"),
        ("ExecReload=", "start-compose.sh", "COMPOSE_RELOAD_UNGATED"),
    )
    findings = []
    for prefix, script, code in hazards:
        lines = (
            line
            for line in unit.splitlines()
            if line.strip().startswith(prefix)
        )
        if any(script in line for line in lines):
            findings.append(code)
    if not findings:
        findings.append("SYSTEMD_OWNER_INDEPENDENTLY_UNREVIEWED")
    return findings


def read_source(root: Path, relative: str) -> str | None:
    """Open only regular nonsymlink files from a local source directory."""
    path = root / relative
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def source_findings(root: Path) -> list[str]:
    """Report each known inherited ungated owner source pattern."""
    contents = {name: read_source(root, name) for name in SCRIPTS}
    if any(value is None for value in contents.values()):
        return ["OWNER_SOURCE_MISSING_OR_UNSAFE"]
    found = []
    if re.search(
        r"(?m)^\s*restart:\s*unless-stopped\b", contents["compose.yaml"]
    ):
        found.append("COMPOSE_AUTORESTART_UNGATED")
    if "shelly-voltage-updater.lock" in contents["scripts/apply-update.sh"]:
        found.append("UPDATER_RECOVERY_A_ONLY")
    if "compose_cmd up -d" in contents["scripts/start-compose.sh"]:
        found.append("DETACHED_START_HANDOFF_UNREVIEWED")
    if (
        "docker update --restart=unless-stopped"
        in (contents["scripts/install.sh"])
    ):
        found.append("INSTALLER_OLD_AUTORESTART")
    if "restore_source()" in contents["scripts/rollback.sh"]:
        found.append("ROLLBACK_OLD_SOURCE_UNREVIEWED")
    return found


def lock_findings(g_path: Path) -> list[str]:
    """Verify stable file metadata without creating or locking anything."""
    try:
        meta = g_path.lstat()
    except OSError:
        return ["PRODUCTION_G_INODE_MISSING"]
    if not stat.S_ISREG(meta.st_mode):
        return ["PRODUCTION_G_INODE_NOT_REGULAR"]
    if meta.st_uid != 0 or meta.st_nlink != 1:
        return ["PRODUCTION_G_INODE_UNTRUSTED"]
    if stat.S_IMODE(meta.st_mode) & 0o022:
        return ["PRODUCTION_G_INODE_WRITABLE"]
    return []


def backup_findings(database: Path, directory: Path) -> list[str]:
    """Check local filesystem identity, never infer physical independence."""
    try:
        db = database.stat()
        backups = directory.stat()
    except OSError:
        return ["CURRENT_BACKUP_LOCATION_UNVERIFIABLE"]
    if db.st_dev == backups.st_dev:
        return ["BACKUP_SAME_FILESYSTEM_AS_CURRENT_SQLITE"]
    return ["BACKUP_PHYSICAL_INDEPENDENCE_NOT_PROVEN"]


def audit(root: Path) -> list[tuple[str, str]]:
    """Never accept a source-only owner inventory as release approval."""
    groups = (
        ("docker", docker_findings()),
        ("systemd", systemd_findings()),
        ("source", source_findings(root)),
        ("admission", lock_findings(LOCK_G)),
        ("backup", backup_findings(CURRENT_DB, BACKUPS)),
    )
    return [(group, code) for group, values in groups for code in values]


def report(findings: list[tuple[str, str]]) -> int:
    """Show only coarse risk codes, never secrets or actual command output."""
    for actor, finding in findings:
        print(f"NO_GO | {actor} | {finding}")
    if findings:
        print(f"FAIL: {len(findings)} live owner checks block cutover")
        return 3
    print("REVIEW_REQUIRED: read-only scan is not Owner Approval")
    return 4


def main() -> int:
    """Default inert; --audit performs inspection only, not freeze actions."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Inspect known installed owners without making changes",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="Installed source directory, read-only",
    )
    args = parser.parse_args()
    if not args.audit:
        print("DRY_RUN: no runtime inspection or changes")
        return 0
    return report(audit(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
