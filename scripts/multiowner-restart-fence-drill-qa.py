#!/usr/bin/env python3
"""Simulate restart-owner fencing in a private fixture, never live services."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MARKER = ".restart-fence-drill-qa"
MARKER_VALUE = "DISPOSABLE_MULTI_OWNER_RESTART_FENCE_QA\n"
STATE = "writer-state.json"
JOURNAL = ".restart-fence-transaction.json"
LOCK = ".restart-fence.lock"
STEPS = (
    "disable_updater",
    "disable_compose",
    "neutralize_restart",
    "stop_writer",
    "capture_a",
    "capture_b",
    "start_writer",
    "restore_restart",
    "enable_compose",
    "enable_updater",
)
OWNER_FIELDS = (
    "updater_enabled",
    "compose_enabled",
    "compose_active",
    "docker_running",
    "restart_policy",
)
BOOL_FIELDS = (
    "updater_enabled",
    "updater_active",
    "compose_enabled",
    "compose_active",
    "docker_running",
    "boot_recovery_hook",
    "pending_update",
    "snapshot_a",
    "snapshot_b",
)


class Blocked(Exception):
    """Safely refuse an unverified disposable transaction."""


def safe_root(root: Path) -> None:
    """Refuse unmarked, aliased or non-private production paths."""
    if not root.is_absolute() or ".." in root.parts:
        raise Blocked("QA_ROOT_UNSAFE")
    if root.parts[:2] == ("/", "tmp"):
        minimum = 3
    elif root.parts[:3] == ("/", "var", "tmp"):
        minimum = 4
    else:
        raise Blocked("PRODUCTION_PATH_FORBIDDEN")
    if len(root.parts) < minimum:
        raise Blocked("QA_ROOT_UNSAFE")
    current = Path("/")
    for part in root.parts[1:]:
        current /= part
        if current.is_symlink():
            raise Blocked("QA_LINKED_ROOT")
    if not root.is_dir():
        raise Blocked("QA_ROOT_MISSING")
    info = root.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Blocked("QA_ROOT_NOT_PRIVATE")
    marker = root / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise Blocked("QA_MARKER_MISSING")
    if marker.read_text(encoding="ascii") != MARKER_VALUE:
        raise Blocked("QA_MARKER_INVALID")


def read_private(path: Path) -> dict:
    """Read one private regular JSON state without links or extra bytes."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 8192
        ):
            raise Blocked("QA_STATE_FILE_UNSAFE")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            try:
                data = json.load(handle)
            except (ValueError, UnicodeError) as exc:
                raise Blocked("QA_STATE_INVALID_JSON") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(data, dict):
        raise Blocked("QA_STATE_INVALID")
    return data


def sync_directory(root: Path) -> None:
    """Persist journal and state renames in the private fixture."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_private(root: Path, name: str, data: dict) -> None:
    """Replace only a fixed state file via an fsync'd temporary file."""
    if name not in (STATE, JOURNAL):
        raise Blocked("QA_PATH_UNSAFE")
    fd, temp = tempfile.mkstemp(prefix=".qa-state-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, root / name)
        sync_directory(root)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def validate_state(data: dict) -> None:
    """Reject unexpected fixture schema, identity or unstable updater."""
    if any(type(data.get(key)) is not bool for key in BOOL_FIELDS):
        raise Blocked("QA_STATE_BAD_BOOL")
    if data.get("restart_policy") not in (
        "no",
        "always",
        "unless-stopped",
        "on-failure",
    ):
        raise Blocked("QA_POLICY_UNSUPPORTED")
    for key in ("source_version", "image_version"):
        if not isinstance(data.get(key), str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", data[key]
        ):
            raise Blocked("QA_VERSION_UNSAFE")
    if not isinstance(data.get("sqlite_digest"), str) or not re.fullmatch(
        r"[a-f0-9]{64}", data["sqlite_digest"]
    ):
        raise Blocked("QA_DIGEST_INVALID")


@contextmanager
def qa_lock(root: Path) -> Iterator[None]:
    """Serialize state-only drills without touching a real Docker lock."""
    path = root / LOCK
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise Blocked("QA_LOCK_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked("QA_LOCK_BUSY") from exc
        yield
    finally:
        os.close(descriptor)


def require_fenced(state: dict) -> None:
    """Require modeled quiescence before the two mock snapshot stages."""
    if (
        state["docker_running"]
        or state["compose_active"]
        or state["compose_enabled"]
        or state["updater_enabled"]
        or state["updater_active"]
        or state["restart_policy"] != "no"
        or state["pending_update"]
    ):
        raise Blocked("QA_WRITER_NOT_FENCED")


def action(state: dict, step: str, original: dict) -> None:
    """Apply one synthetic owner transition with explicit fail-closed gates."""
    if step == "disable_updater":
        state["updater_enabled"] = False
    elif step == "disable_compose":
        state["compose_enabled"] = False
    elif step == "neutralize_restart":
        state["restart_policy"] = "no"
    elif step == "stop_writer":
        state["compose_active"] = False
        state["docker_running"] = False
    elif step in ("capture_a", "capture_b"):
        require_fenced(state)
        if state["sqlite_digest"] != original["sqlite_digest"]:
            raise Blocked("QA_SQLITE_HISTORY_CHANGED")
        state["snapshot_a" if step == "capture_a" else "snapshot_b"] = True
    elif step == "start_writer":
        require_fenced(state)
        if not (state["snapshot_a"] and state["snapshot_b"]):
            raise Blocked("QA_INCOMPLETE_AB")
        state["docker_running"] = original["docker_running"]
        state["compose_active"] = original["compose_active"]
    elif step == "restore_restart":
        if not (state["snapshot_a"] and state["snapshot_b"]):
            raise Blocked("QA_INCOMPLETE_AB")
        if state["docker_running"] != original["docker_running"]:
            raise Blocked("QA_WRITER_RESUME_UNVERIFIED")
        state["restart_policy"] = original["restart_policy"]
    elif step == "enable_compose":
        state["compose_enabled"] = original["compose_enabled"]
    elif step == "enable_updater":
        state["updater_enabled"] = original["updater_enabled"]
    else:
        raise Blocked("QA_UNKNOWN_STEP")


def preflight(state: dict) -> None:
    """Require a same-version, no-pending-update disposable fixture."""
    validate_state(state)
    if state["source_version"] != state["image_version"]:
        raise Blocked("QA_SOURCE_IMAGE_MISMATCH")
    if state["pending_update"] or state["updater_active"]:
        raise Blocked("QA_RECOVERY_OR_UPDATER_ACTIVE")
    if state["snapshot_a"] or state["snapshot_b"]:
        raise Blocked("QA_PREEXISTING_SNAPSHOT")


def simulate(root: Path, fail_after: str, restart_after: str) -> dict:
    """Write a journal before mock state transitions; never invoke Docker."""
    if (root / JOURNAL).exists() or (root / JOURNAL).is_symlink():
        raise Blocked("QA_RECOVERY_REQUIRED")
    original = read_private(root / STATE)
    preflight(original)
    state = original.copy()
    write_private(root, JOURNAL, {"original": original, "last_step": None})
    for step in STEPS:
        action(state, step, original)
        if step == restart_after:
            state["docker_running"] = True
        write_private(root, STATE, state)
        write_private(root, JOURNAL, {"original": original, "last_step": step})
        if step == fail_after:
            raise Blocked("QA_INJECTED_PROCESS_INTERRUPTION")
    if state["sqlite_digest"] != original["sqlite_digest"]:
        raise Blocked("QA_SQLITE_HISTORY_CHANGED")
    (root / JOURNAL).unlink()
    sync_directory(root)
    return state


def recover(root: Path) -> dict:
    """Restore only mock owner fields, never a historical SQLite copy."""
    record = read_private(root / JOURNAL)
    original = record.get("original")
    if not isinstance(original, dict):
        raise Blocked("QA_JOURNAL_INVALID")
    validate_state(original)
    current = read_private(root / STATE)
    validate_state(current)
    if current["sqlite_digest"] != original["sqlite_digest"]:
        raise Blocked("QA_LIVE_HISTORY_DIVERGED")
    immutable = (
        "source_version",
        "image_version",
        "pending_update",
        "updater_active",
        "boot_recovery_hook",
    )
    if any(current[key] != original[key] for key in immutable):
        raise Blocked("QA_RECOVERY_IDENTITY_CHANGED")
    for key in OWNER_FIELDS:
        current[key] = original[key]
    current["snapshot_a"] = False
    current["snapshot_b"] = False
    write_private(root, STATE, current)
    (root / JOURNAL).unlink()
    sync_directory(root)
    return current


def main() -> int:
    """Run only marked scratch transitions and provide PASS/FAIL."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--simulate-disposable", action="store_true")
    mode.add_argument("--recover-disposable", action="store_true")
    parser.add_argument("--fail-after", choices=STEPS, default="")
    parser.add_argument(
        "--inject-restart-after",
        choices=("stop_writer", "capture_a", "capture_b"),
        default="",
    )
    args = parser.parse_args()
    try:
        safe_root(args.root)
        with qa_lock(args.root):
            if args.recover_disposable:
                result = recover(args.root)
            elif args.simulate_disposable:
                result = simulate(
                    args.root, args.fail_after, args.inject_restart_after
                )
            else:
                result = read_private(args.root / STATE)
                preflight(result)
    except (Blocked, OSError, UnicodeError) as exc:
        print(f"QA MULTI-OWNER FENCE BLOCKED: {exc}", file=sys.stderr)
        return 3
    print(
        "QA MULTI-OWNER FENCE PASS:",
        "source=",
        result["source_version"],
        "snapshots=",
        result["snapshot_a"] and result["snapshot_b"],
    )
    print("SIMULATION ONLY: Docker/systemd/SQLite/Telegram untouched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
