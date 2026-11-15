#!/usr/bin/env python3
"""Exercise a durable metadata journal on an offline disposable guest."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

PREFIX = "svt-journal-fault-"
CONFIRM = "OFFLINE_JOURNAL_SIGKILL_QA_ONLY"
NEW = {"version": "0.5.21-hw", "digest": "sha256:" + "b" * 64}
OLD = {"version": "0.5.20-hw", "digest": "sha256:" + "a" * 64}
IMAGE_ID = "sha256:" + "d" * 64
PHASES = (
    "prepared",
    "private_written",
    "public_written",
    "published",
    "after_unlink",
    "after_unlink_fsync",
    "committed",
)


class Blocked(RuntimeError):
    """The journal cannot safely imply a completed deployment."""


def sync_dir(folder: Path) -> None:
    """Persist directory entries after rename or unlink."""
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json(path: Path, item: dict, mode: int = 0o600) -> None:
    """Atomically write and fsync one complete private fixture JSON."""
    fd, name = tempfile.mkstemp(prefix=".journal-", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(item, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict | None:
    """Reject links and malformed status rather than guessing success."""
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise Blocked("UNSAFE_STATUS_PATH")
    if path.stat().st_nlink != 1:
        raise Blocked("LINKED_STATUS_PATH")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Blocked("INVALID_JSON_SHAPE")
    return value


def journal(phase: str) -> dict:
    """Construct one synthetic release transaction record."""
    return {"phase": phase, **NEW}


def initialize(root: Path) -> None:
    """Stage entirely invented old status and pending release marker."""
    if root.exists() and any(root.iterdir()):
        raise Blocked("NONEMPTY_QA_DIRECTORY")
    root.mkdir(mode=0o700, exist_ok=True)
    write_json(root / "private.json", OLD)
    write_json(root / "public.json", OLD, 0o644)
    write_json(root / "journal.json", journal("prepared"))
    (root / "pending-update").write_text("synthetic-only\n")
    sync_dir(root)
    for file in ("maintenance.lock", "updater.lock"):
        (root / file).touch(mode=0o600, exist_ok=False)


def running_ok(verified: bool, runtime: str, image_id: str) -> bool:
    """Model only an externally supplied trust result, never verify it."""
    return (
        verified
        and runtime
        == "ghcr.io/isentinel/shelly-voltage-telegram@" + NEW["digest"]
        and image_id == IMAGE_ID
    )


def classify(root: Path, verified: bool, runtime: str, image_id: str) -> str:
    """Fail closed on missing/mismatched journal or signed runtime."""
    try:
        record = read_json(root / "journal.json")
        private = read_json(root / "private.json")
        public = read_json(root / "public.json")
    except (OSError, ValueError, Blocked):
        return "BLOCKED_INVALID_METADATA"
    if record is None or set(record) != {"phase", "version", "digest"}:
        return "BLOCKED_NO_JOURNAL"
    if {k: record[k] for k in NEW} != NEW:
        return "BLOCKED_OTHER_RELEASE"
    if record["phase"] not in {"prepared", "published", "committed"}:
        return "BLOCKED_INVALID_PHASE"
    if not running_ok(verified, runtime, image_id):
        return "BLOCKED_UNVERIFIED_RUNTIME"
    if record["phase"] == "prepared":
        return "BLOCKED_PREPARED"
    if private != NEW or public != NEW:
        return "BLOCKED_METADATA_MISMATCH"
    if record["phase"] == "committed":
        return (
            "BLOCKED_PENDING_AFTER_COMMIT"
            if (root / "pending-update").exists()
            else "COMMITTED"
        )
    return "RECONCILE_REQUIRED"


def checkpoint(root: Path, stage: str) -> None:
    """Perform exactly one synthetic metadata commit boundary."""
    if stage == "private_written":
        write_json(root / "private.json", NEW)
    elif stage == "public_written":
        write_json(root / "public.json", NEW, 0o644)
    elif stage == "published":
        write_json(root / "journal.json", journal("published"))
    elif stage == "after_unlink":
        (root / "pending-update").unlink()
    elif stage == "after_unlink_fsync":
        sync_dir(root)
    elif stage == "committed":
        write_json(root / "journal.json", journal("committed"))


def worker(root: Path, stop: str) -> None:
    """SIGKILL after a selected real fsync/rename/unlink boundary."""
    for phase in PHASES:
        if phase != "prepared":
            checkpoint(root, phase)
        if phase == stop:
            os.kill(os.getpid(), signal.SIGKILL)


def file_lock(root: Path, name: str, stack: ExitStack) -> None:
    """Acquire one separate private synthetic exclusive lock."""
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise Blocked("LOCK_PATH_INVALID")
    if path.stat().st_nlink != 1 or path.stat().st_uid != os.getuid():
        raise Blocked("LOCK_OWNER_INVALID")
    stream = stack.enter_context(path.open("rb"))
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise Blocked("LOCK_BUSY_" + name) from exc


def reconcile(root: Path, verified: bool, runtime: str, image_id: str) -> str:
    """Commit only while holding both B then A exclusive locks."""
    with ExitStack() as stack:
        file_lock(root, "maintenance.lock", stack)
        file_lock(root, "updater.lock", stack)
        status = classify(root, verified, runtime, image_id)
        if status == "COMMITTED":
            return status
        if status != "RECONCILE_REQUIRED":
            raise Blocked(status)
        pending = root / "pending-update"
        if pending.exists():
            pending.unlink()
            sync_dir(root)
        write_json(root / "journal.json", journal("committed"))
        status = classify(root, verified, runtime, image_id)
        if status != "COMMITTED":
            raise Blocked(status)
        return status


def guest_guard() -> None:
    """Never allow actual fault tests on a networked/production guest."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise Blocked("OFFLINE_LXC114_ONLY")
    if {item for _, item in socket.if_nameindex()} != {"lo"}:
        raise Blocked("EXTERNAL_INTERFACE_PRESENT")
    output = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
            "svt-qa-signed-writer-114",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=6,
    )
    if output.stdout.strip() != "false|no":
        raise Blocked("ORIGINAL_QA_WRITER_ACTIVE")
    for unit in ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer"):
        item = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if item.stdout.strip() != "disabled":
            raise Blocked("QA_SYSTEMD_OWNER_ENABLED")


def trusted() -> tuple[bool, str, str]:
    """Return explicitly mocked trust and synthetic running identity."""
    return (
        True,
        "ghcr.io/isentinel/shelly-voltage-telegram@" + NEW["digest"],
        IMAGE_ID,
    )


def check_sigkill(phase: str) -> dict:
    """Spawn one sacrificial Python worker and classify its disk state."""
    with tempfile.TemporaryDirectory(prefix=PREFIX, dir="/var/tmp") as name:
        root = Path(name)
        initialize(root)
        args = [
            sys.executable,
            __file__,
            "--worker-root",
            str(root),
            "--stop-stage",
            phase,
            "--confirm",
            CONFIRM,
        ]
        result = subprocess.run(
            args, check=False, capture_output=True, text=True, timeout=10
        )
        if result.returncode != -signal.SIGKILL:
            raise Blocked("WORKER_NOT_KILLED: " + str(result.returncode))
        current = classify(root, *trusted())
        expected = (
            "BLOCKED_PREPARED"
            if PHASES.index(phase) < PHASES.index("published")
            else "COMMITTED"
            if phase == "committed"
            else "RECONCILE_REQUIRED"
        )
        if current != expected:
            raise Blocked("CLASSIFICATION_MISMATCH_" + phase + "_" + current)
        if (
            current == "RECONCILE_REQUIRED"
            and reconcile(root, *trusted()) != "COMMITTED"
        ):
            raise Blocked("RECONCILIATION_FAILED")
        return {
            "stage": phase,
            "initial": current,
            "final": classify(root, *trusted()),
        }


def run_selected(args: argparse.Namespace) -> int:
    """Run only private QA cases after explicit consent and guard."""
    try:
        guest_guard()
        if args.worker_root is not None:
            root = args.worker_root
            if root.parent != Path("/var/tmp") or not root.name.startswith(
                PREFIX
            ):
                raise Blocked("UNSAFE_WORKER_TARGET")
            if stat.S_IMODE(root.stat().st_mode) != 0o700:
                raise Blocked("WORKER_ROOT_MODE")
            worker(root, args.stop_stage)
            return 3
        results = [check_sigkill(stage) for stage in PHASES]
    except (Blocked, OSError, ValueError, subprocess.SubprocessError) as exc:
        print("BLOCKED", type(exc).__name__, str(exc)[:250])
        return 3
    print(
        json.dumps(
            {
                "result": "PASS_OFFLINE_JOURNAL_SIGKILL",
                "cases": results,
                "signature": "MOCKED",
                "production_changed": False,
            }
        )
    )
    return 0


def main() -> int:
    """Default no-op; only real offline guest may initiate SIGKILL QA."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--worker-root", type=Path)
    parser.add_argument("--stop-stage", choices=PHASES)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable and args.worker_root is None:
        print("DRY_RUN: journal SIGKILL tests not started")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact consent missing")
        return 2
    return run_selected(args)


if __name__ == "__main__":
    raise SystemExit(main())
