#!/usr/bin/env python3
"""Read-only preflight for externally frozen legacy Shelly restore owners."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

DOCKER_NAME = "shelly-voltage-bot"
SERVICES = (
    "shelly-voltage-compose.service",
    "shelly-graphic-emergency-boot.service",
)
LOCK_LINE = re.compile(r"(?m)^lock:\s+\d+: FLOCK\s+ADVISORY\s+WRITE\b")


class FrozenOwnerRequired(RuntimeError):
    """Do not treat a partial or untrusted freeze as a release handoff."""


def trusted_gate(gate: Path) -> Path:
    """Check stable root-owned mode0700 dir and mode0640 G inode."""
    if not gate.is_absolute() or gate.resolve() != gate:
        raise FrozenOwnerRequired("GATE_PATH_UNTRUSTED")
    info = gate.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise FrozenOwnerRequired("GATE_DIRECTORY_UNTRUSTED")
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise FrozenOwnerRequired("GATE_DIRECTORY_UNSAFE_MODE")
    lock = gate / "G"
    st = lock.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
        raise FrozenOwnerRequired("G_LOCK_INODE_UNTRUSTED")
    if st.st_nlink != 1 or stat.S_IMODE(st.st_mode) != 0o640:
        raise FrozenOwnerRequired("G_LOCK_INODE_UNSAFE")
    return lock


def require_inherited_exclusive(lock: Path, inherited_fd: int) -> None:
    """Demand an actually inherited G EX, not a separate failed trylock."""
    if not 3 <= inherited_fd <= 1024:
        raise FrozenOwnerRequired("G_FD_OUT_OF_RANGE")
    actual = os.fstat(inherited_fd)
    path = lock.lstat()
    if not stat.S_ISREG(actual.st_mode):
        raise FrozenOwnerRequired("G_FD_NOT_REGULAR")
    if (actual.st_dev, actual.st_ino) != (path.st_dev, path.st_ino):
        raise FrozenOwnerRequired("G_FD_DIFFERENT_INODE")
    info = Path(f"/proc/self/fdinfo/{inherited_fd}").read_text()
    if not LOCK_LINE.search(info):
        raise FrozenOwnerRequired("INHERITED_G_EX_NOT_HELD")


def require_private_executable_path(path: Path) -> None:
    """Reject executable paths writable or replaceable by other users."""
    if not path.is_absolute() or path.resolve() != path:
        raise FrozenOwnerRequired("OWNER_COMMAND_PATH_UNTRUSTED")
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
            raise FrozenOwnerRequired("OWNER_COMMAND_PARENT_UNTRUSTED")
        sticky_tmp = parent in (Path("/tmp"), Path("/var/tmp"))
        if mode & 0o022 and not (sticky_tmp and mode & 0o1000):
            raise FrozenOwnerRequired("OWNER_COMMAND_PARENT_WRITABLE")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
        raise FrozenOwnerRequired("OWNER_COMMAND_INODE_UNTRUSTED")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022 or not mode & 0o111 or info.st_nlink != 1:
        raise FrozenOwnerRequired("OWNER_COMMAND_INODE_UNSAFE")


def trusted_command(name: str) -> Path:
    """Resolve Docker/systemd to a trusted absolute root-controlled path."""
    if name not in ("docker", "systemctl"):
        raise FrozenOwnerRequired("OWNER_COMMAND_NOT_ALLOWED")
    selected = shutil.which(name)
    if not selected:
        raise FrozenOwnerRequired("OWNER_COMMAND_MISSING")
    candidate = Path(selected)
    require_private_executable_path(candidate)
    return candidate


def readonly_command(argv: list[str]) -> str:
    """Execute only one fixed nonmutating system status operation."""
    try:
        program = trusted_command(argv[0])
        done = subprocess.run(
            [str(program), *argv[1:]],
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FrozenOwnerRequired("OWNER_INSPECTION_FAILED") from exc
    if done.returncode:
        raise FrozenOwnerRequired("OWNER_INSPECTION_FAILED")
    return done.stdout.strip()


def docker_owner_state() -> tuple[bool, str, int]:
    """Require a stopped known legacy container with restart disabled."""
    output = readonly_command(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}|"
            "{{.RestartCount}}",
            DOCKER_NAME,
        ]
    )
    match = re.fullmatch(
        r"(true|false)\|(no|unless-stopped|always|on-failure)\|(\d+)", output
    )
    if not match:
        raise FrozenOwnerRequired("DOCKER_STATE_UNVERIFIABLE")
    return match.group(1) == "true", match.group(2), int(match.group(3))


def require_docker_frozen() -> int:
    """Observe no autonomous restart over two independent inspections."""
    first = docker_owner_state()
    if first[0]:
        raise FrozenOwnerRequired("OLD_DOCKER_WRITER_STILL_RUNNING")
    if first[1] != "no":
        raise FrozenOwnerRequired("OLD_DOCKER_AUTORESTART_NOT_FROZEN")
    time.sleep(0.15)
    second = docker_owner_state()
    if second != first:
        raise FrozenOwnerRequired("DOCKER_STATE_CHANGED_DURING_PREFLIGHT")
    return second[2]


def require_systemd_frozen(unit: str) -> None:
    """Require inactive and masked state for known systemd start paths."""
    data = readonly_command(
        [
            "systemctl",
            "show",
            unit,
            "-p",
            "ActiveState",
            "-p",
            "UnitFileState",
            "--no-pager",
        ]
    )
    fields = dict(
        line.split("=", 1) for line in data.splitlines() if "=" in line
    )
    if fields.get("ActiveState") != "inactive":
        raise FrozenOwnerRequired("SYSTEMD_OWNER_STILL_ACTIVE")
    if fields.get("UnitFileState") not in ("masked", "masked-runtime"):
        raise FrozenOwnerRequired("SYSTEMD_OWNER_NOT_MASKED")


def inspect_frozen_owner(gate: Path, inherited_fd: int) -> dict:
    """Fail closed on missing G EX, Docker or known systemd owner fence."""
    if os.geteuid() != 0:
        raise FrozenOwnerRequired("ROOT_REQUIRED")
    lock = trusted_gate(gate)
    require_inherited_exclusive(lock, inherited_fd)
    for unit in SERVICES:
        require_systemd_frozen(unit)
    restarts = require_docker_frozen()
    return {
        "result": "KNOWN_OWNER_PREREQUISITES_OBSERVED",
        "g_ex_inherited": True,
        "docker_writer_stopped_and_restart_no": True,
        "restart_count": restarts,
        "systemd_masked_inactive": list(SERVICES),
        "all_manual_install_update_rollback_owners_proven": False,
        "final_cutover_authorized": False,
        "release_decision": "NO_GO",
    }


def main() -> int:
    """Default inert; owner inspection never issues Owner Approval."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--gate-dir", type=Path)
    parser.add_argument("--inherited-g-fd", type=int)
    args = parser.parse_args()
    if not args.check:
        print("DRY_RUN: no owner state inspected")
        return 0
    if args.gate_dir is None or args.inherited_g_fd is None:
        print("BLOCKED: exact G gate and inherited FD required")
        return 2
    try:
        answer = inspect_frozen_owner(args.gate_dir, args.inherited_g_fd)
    except (FrozenOwnerRequired, OSError, ValueError) as exc:
        print("OWNER_FREEZE_REFUSED", type(exc).__name__, str(exc)[:110])
        return 3
    print(json.dumps(answer, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
