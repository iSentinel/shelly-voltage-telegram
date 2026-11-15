#!/usr/bin/env python3
"""Read-only owner source audit; never authorize a production release."""

from __future__ import annotations

import argparse
import re
import stat
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "compose.yaml",
    "scripts/apply-update.sh",
    "scripts/start-compose.sh",
    "scripts/install.sh",
    "scripts/rollback.sh",
)


@dataclass(frozen=True)
class Finding:
    """Describe an unresolved owner path without leaking source content."""

    path: str
    code: str


def read_required(root: Path, relative: str) -> str | None:
    """Read only a regular non-symlink local repository file."""
    path = root / relative
    try:
        meta = path.lstat()
        if not stat.S_ISREG(meta.st_mode):
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def apply_update_risks(source: str) -> list[str]:
    """Flag updater and recovery paths not proven to own global G/B."""
    result = []
    if "shelly-voltage-updater.lock" in source and "flock" in source:
        result.append("UPDATER_A_LOCK_NOT_GLOBAL_G_B_PROOF")
    if "--recover" in source and re.search(r"flock\s+-w", source):
        result.append("BOOT_RECOVERY_A_ONLY_NOT_GLOBAL_G_B_PROOF")
    if "docker compose" in source and "stop shelly-voltage" in source:
        result.append("UPDATER_DOCKER_STOP_OWNER_HANDOFF_UNVERIFIED")
    if re.search(r'rm\s+-f\s+"\$DB-wal"', source):
        result.append("UPDATER_SQLITE_WAL_RESTORE_OWNER_UNVERIFIED")
    return result


def compose_risks(source: str) -> list[str]:
    """Flag autonomous runtime restart without full owner acceptance."""
    result = []
    if re.search(r"(?m)^\s*restart:\s*unless-stopped\b", source):
        result.append("AUTONOMOUS_DOCKER_RESTART_COVERAGE_UNVERIFIED")
    return result


def start_risks(source: str) -> list[str]:
    """Detached Compose must not count as accepted in-app G admission."""
    if re.search(r"\bcompose_cmd\s+up\s+-d\b", source):
        return ["DETACHED_COMPOSE_G_HANDOFF_UNVERIFIED"]
    return []


def install_risks(source: str) -> list[str]:
    """Installer can re-enable Docker autorestart independently of gates."""
    if "docker update --restart=unless-stopped" in source:
        return ["INSTALLER_REENABLES_AUTONOMOUS_RESTART"]
    return []


def rollback_risks(source: str) -> list[str]:
    """Flag SQLite WAL replacement and mutable source without owner proof."""
    result = []
    if re.search(r'rm\s+-f\s+"\$\{DB\}-wal"', source):
        result.append("ROLLBACK_SQLITE_WAL_SHM_REPLACE_UNVERIFIED")
    if "restore_source()" in source or "restore_source {" in source:
        result.append("ROLLBACK_SOURCE_REPLACEMENT_OWNER_UNVERIFIED")
    return result


RULES = {
    "compose.yaml": compose_risks,
    "scripts/apply-update.sh": apply_update_risks,
    "scripts/start-compose.sh": start_risks,
    "scripts/install.sh": install_risks,
    "scripts/rollback.sh": rollback_risks,
}


def inspect(root: Path) -> list[Finding]:
    """Enumerate known bypasses; missing sources also block the release."""
    found = []
    for path in SOURCES:
        source = read_required(root, path)
        if source is None:
            found.append(Finding(path, "MISSING_OR_UNSAFE_SOURCE"))
            continue
        for reason in RULES[path](source):
            found.append(Finding(path, reason))
    return found


def report(findings: list[Finding]) -> int:
    """Never print GO: clean static inspection still requires acceptance."""
    for item in findings:
        print(f"NO_GO | {item.path} | {item.code}")
    if findings:
        print(f"FAIL: {len(findings)} owner findings need review")
        return 3
    print("REVIEW_REQUIRED: static owner checks cannot authorize release")
    return 4


def main() -> int:
    """Run read-only inspection of an explicit or adjacent source tree."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Local repository source root; read-only",
    )
    args = parser.parse_args()
    return report(inspect(args.repo_root))


if __name__ == "__main__":
    raise SystemExit(main())
