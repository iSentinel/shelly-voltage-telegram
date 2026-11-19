#!/usr/bin/env python3
"""Stage a fenced SQLite rescue in disposable QA, without activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    fence_directory,
    quiescent_writer_lease,
    update_gate,
)

SCRIPT_DIR = Path(__file__).resolve().parent
TOKEN = re.compile(r"qa-[a-zA-Z0-9_.-]{1,90}\Z")


class StageBlocked(ValueError):
    """Stable fail-closed reason for an unsafe update stage."""


def arguments() -> argparse.Namespace:
    """Require explicit isolation and no implicit production defaults."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--fence-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--token", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--inspect", action="store_true")
    mode.add_argument("--recover-receipt", action="store_true")
    return parser.parse_args()


def isolated_directory(path: Path, base: Path) -> bool:
    """Accept only one named private QA directory directly below base."""
    return (
        path.is_absolute()
        and path.parent == base
        and path.name.startswith("svt-qa-")
        and not path.is_symlink()
    )


def check_paths(args: argparse.Namespace) -> None:
    """Refuse any path capable of touching the working LXC110 service."""
    if os.geteuid() != 0 or not args.qa_only:
        raise StageBlocked("SVT_QA_ONLY_REQUIRED")
    if not re.fullmatch(r"svt-qa-[a-zA-Z0-9_-]{1,80}", args.container):
        raise StageBlocked("SVT_QA_CONTAINER_REQUIRED")
    if not TOKEN.fullmatch(args.token):
        raise StageBlocked("SVT_QA_TOKEN_INVALID")
    if not isolated_directory(args.database.parent, Path("/var/tmp")):
        raise StageBlocked("SVT_QA_DATABASE_PATH_UNSAFE")
    if not isolated_directory(args.output.parent, Path("/var/lib")):
        raise StageBlocked("SVT_QA_OUTPUT_PATH_UNSAFE")
    if not isolated_directory(args.fence_dir, Path("/var/lib")):
        raise StageBlocked("SVT_QA_FENCE_PATH_UNSAFE")


def inspect_container(name: str) -> dict:
    """Inspect an exact existing QA container without starting it."""
    result = subprocess.run(
        ("docker", "inspect", name),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        raise StageBlocked("SVT_QA_CONTAINER_MISSING")
    data = json.loads(result.stdout)
    if not isinstance(data, list) or len(data) != 1:
        raise StageBlocked("SVT_QA_INSPECT_INVALID")
    return data[0]


def check_container(info: dict, args: argparse.Namespace) -> None:
    """Demand no autonomous restarts or production network/mount access."""
    host = info.get("HostConfig", {})
    if host.get("RestartPolicy", {}).get("Name") != "no":
        raise StageBlocked("SVT_DOCKER_RESTART_UNFENCED")
    if host.get("NetworkMode") != "none":
        raise StageBlocked("SVT_QA_NETWORK_ENABLED")
    if not host.get("ReadonlyRootfs"):
        raise StageBlocked("SVT_QA_ROOTFS_WRITABLE")
    if info.get("Config", {}).get("User") != "10001:10001":
        raise StageBlocked("SVT_QA_USER_MISMATCH")
    expected = {
        (str(args.fence_dir), "/run/svt-fence", True),
        (str(args.database.parent), "/data", True),
        (str(SCRIPT_DIR.parent), "/src", False),
    }
    mounts = {
        (item.get("Source"), item.get("Destination"), item.get("RW"))
        for item in info.get("Mounts", [])
    }
    if mounts != expected:
        raise StageBlocked("SVT_QA_MOUNTS_MISMATCH")
    env = info.get("Config", {}).get("Env", [])
    if "SVT_WRITER_FENCE_DIR=/run/svt-fence" not in env:
        raise StageBlocked("SVT_QA_FENCE_ENV_MISMATCH")


def check_state(args: argparse.Namespace) -> None:
    """Validate root-controlled fence, storage and output permissions."""
    os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
    if fence_directory(args.database) != args.fence_dir:
        raise StageBlocked("SVT_QA_FENCE_MISMATCH")
    if args.database.is_symlink() or not args.database.is_file():
        raise StageBlocked("SVT_QA_DATABASE_MISSING")
    root = args.output.parent.stat()
    if root.st_uid != 0 or stat.S_IMODE(root.st_mode) != 0o700:
        raise StageBlocked("SVT_QA_OUTPUT_NOT_PRIVATE")
    if args.output.exists() or args.output.is_symlink():
        raise StageBlocked("SVT_QA_RESCUE_ALREADY_EXISTS")
    marker = args.fence_dir / ".svt-update-pending"
    if marker.exists() or marker.is_symlink():
        raise StageBlocked("SVT_QA_PREEXISTING_PENDING")


def exec_checked(command: list[str], reason: str) -> str:
    """Run one bounded subprocess and fail closed on any unexpected exit."""
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode:
        raise StageBlocked(reason)
    return result.stdout.strip()


def marker_action(action: str, args: argparse.Namespace) -> None:
    """Use the real common-inode marker CLI, never a private marker."""
    exec_checked(
        [
            sys.executable,
            str(SCRIPT_DIR / "writer-fence-marker.py"),
            action,
            "--database",
            str(args.database),
            "--token",
            args.token,
        ],
        f"SVT_QA_MARKER_{action.upper()}_FAILED",
    )


def stop_writer(args: argparse.Namespace) -> None:
    """Stop only the selected disposable QA container."""
    exec_checked(
        ["docker", "stop", "-t", "5", args.container],
        "SVT_QA_WRITER_STOP_FAILED",
    )
    info = inspect_container(args.container)
    if info.get("State", {}).get("Running") is not False:
        raise StageBlocked("SVT_QA_WRITER_STILL_RUNNING")


def rescue_newest(args: argparse.Namespace) -> None:
    """Obtain full WAL-consistent backup only after the owner has released."""
    exec_checked(
        [
            sys.executable,
            str(SCRIPT_DIR / "fenced-newest-rescue.py"),
            "--database",
            str(args.database),
            "--output",
            str(args.output),
            "--fence-dir",
            str(args.fence_dir),
        ],
        "SVT_QA_NEWEST_RESCUE_FAILED",
    )


def persist_receipt(args: argparse.Namespace) -> None:
    """Persist a QA-only record that does not authorize a signed rollout."""
    with args.output.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    receipt = args.output.with_name(args.output.name + ".qa.json")
    record = {
        "status": "QA_ONLY_RESCUE_STAGED",
        "token": args.token,
        "sha256": digest,
        "release_authorized": False,
        "rollback_authorized": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    fd = os.open(
        receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as file:
        json.dump(record, file, sort_keys=True)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    directory = os.open(args.output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_pending_token(args: argparse.Namespace) -> None:
    """Verify the exact protected update token without following links."""
    with update_gate(args.database):
        marker = args.fence_dir / ".svt-update-pending"
        fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_nlink != 1
                or info.st_size > 128
            ):
                raise StageBlocked("SVT_QA_PENDING_UNSAFE")
            if os.read(fd, 129).decode("ascii").strip() != args.token:
                raise StageBlocked("SVT_QA_PENDING_TOKEN_MISMATCH")
        finally:
            os.close(fd)


def read_private_receipt(path: Path) -> dict:
    """Read a bounded root-owned QA receipt with no symbolic links."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 4096
        ):
            raise StageBlocked("SVT_QA_RECEIPT_UNSAFE")
        data = json.loads(os.read(fd, 4097))
    finally:
        os.close(fd)
    if not isinstance(data, dict):
        raise StageBlocked("SVT_QA_RECEIPT_INVALID")
    return data


def database_digest(path: Path) -> str:
    """Require valid three-phase and PHOTO data, then hash all SQL rows."""
    uri = f"file:{quote(str(path))}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise StageBlocked("SVT_QA_SQLITE_CORRUPT")
        phases = set(
            row[0]
            for row in db.execute("SELECT DISTINCT phase FROM measurements")
        )
        if phases != {"L1", "L2", "L3"}:
            raise StageBlocked("SVT_QA_INCOMPLETE_PHASES")
        meta = dict(
            db.execute(
                "SELECT key, value FROM meta WHERE key IN (?,?)",
                (
                    "telegram_setting_meter_type",
                    "telegram_status_media_identity",
                ),
            )
        )
        if meta.get("telegram_setting_meter_type") != "pro3em":
            raise StageBlocked("SVT_QA_METER_MISMATCH")
        if not meta.get("telegram_status_media_identity"):
            raise StageBlocked("SVT_QA_PHOTO_MISSING")
        digest = hashlib.sha256()
        for row in db.iterdump():
            digest.update((row + "\n").encode())
    return digest.hexdigest()


def check_rescue_file(path: Path) -> None:
    """Reject linked or exposed QA rescue artifacts."""
    if path.is_symlink():
        raise StageBlocked("SVT_QA_RESCUE_LINKED")
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise StageBlocked("SVT_QA_RESCUE_UNSAFE")


def validate_staged_history(args: argparse.Namespace) -> None:
    """Require current and rescued histories to remain fully identical."""
    check_rescue_file(args.output)
    if database_digest(args.database) != database_digest(args.output):
        raise StageBlocked("SVT_QA_RESCUE_HISTORY_MISMATCH")


def recover_receipt(args: argparse.Namespace, info: dict) -> None:
    """Reconstruct only a missing QA receipt from proven intact history."""
    if info.get("State", {}).get("Running") is not False:
        raise StageBlocked("SVT_QA_WRITER_RUNNING")
    os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
    fence_directory(args.database)
    root = args.output.parent.stat()
    if root.st_uid != 0 or stat.S_IMODE(root.st_mode) != 0o700:
        raise StageBlocked("SVT_QA_OUTPUT_NOT_PRIVATE")
    receipt = args.output.with_name(args.output.name + ".qa.json")
    if receipt.exists() or receipt.is_symlink():
        raise StageBlocked("SVT_QA_RECEIPT_ALREADY_EXISTS")
    read_pending_token(args)
    with quiescent_writer_lease(args.database):
        read_pending_token(args)
        validate_staged_history(args)
        persist_receipt(args)


def inspect_stage(args: argparse.Namespace, info: dict) -> None:
    """Revalidate pending, stopped writer, backup and QA receipt."""
    if info.get("State", {}).get("Running") is not False:
        raise StageBlocked("SVT_QA_WRITER_RUNNING")
    os.environ["SVT_WRITER_FENCE_DIR"] = str(args.fence_dir)
    if fence_directory(args.database) != args.fence_dir:
        raise StageBlocked("SVT_QA_FENCE_MISMATCH")
    read_pending_token(args)
    root = args.output.parent.stat()
    if root.st_uid != 0 or stat.S_IMODE(root.st_mode) != 0o700:
        raise StageBlocked("SVT_QA_OUTPUT_NOT_PRIVATE")
    receipt = read_private_receipt(
        args.output.with_name(args.output.name + ".qa.json")
    )
    if (
        receipt.get("status") != "QA_ONLY_RESCUE_STAGED"
        or receipt.get("token") != args.token
        or receipt.get("release_authorized") is not False
        or receipt.get("rollback_authorized") is not False
    ):
        raise StageBlocked("SVT_QA_RECEIPT_POLICY_INVALID")
    check_rescue_file(args.output)
    with quiescent_writer_lease(args.database):
        read_pending_token(args)
        with args.output.open("rb") as file:
            actual = hashlib.file_digest(file, "sha256").hexdigest()
        if actual != receipt.get("sha256"):
            raise StageBlocked("SVT_QA_RESCUE_HASH_MISMATCH")
        validate_staged_history(args)


def report_failure(
    args: argparse.Namespace, published: bool, error: Exception
) -> int:
    """Keep a published marker and report a stable fail-closed reason."""
    state = (
        "PENDING_UNCHANGED"
        if args.inspect or args.recover_receipt
        else "PENDING_RETAINED"
        if published
        else "NO_CHANGES"
    )
    reason = (
        str(error)
        if isinstance(error, (ValueError, WriterFenceError))
        else type(error).__name__
    )
    print(f"FAIL: {reason} {state}", file=sys.stderr)
    return 3


def main() -> int:
    """Retain the marker after failures; never clear it implicitly."""
    args = arguments()
    published = False
    try:
        check_paths(args)
        info = inspect_container(args.container)
        check_container(info, args)
        if args.inspect:
            inspect_stage(args, info)
            print("INSPECT PASS: pending, stopped writer, WAL and PHOTO match")
            return 0
        if args.recover_receipt:
            recover_receipt(args, info)
            inspect_stage(args, info)
            print("RECOVER-RECEIPT PASS: SHA256 verified; pending retained")
            return 0
        check_state(args)
        if args.dry_run:
            print("DRY-RUN PASS: isolated updater stage, no changes")
            return 0
        marker_action("publish", args)
        published = True
        stop_writer(args)
        rescue_newest(args)
        persist_receipt(args)
    except (
        OSError,
        ValueError,
        WriterFenceError,
        subprocess.TimeoutExpired,
        sqlite3.DatabaseError,
    ) as exc:
        return report_failure(args, published, exc)
    print("PASS: QA_NEWEST_RESCUE_STAGED, writer stopped, pending retained")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
