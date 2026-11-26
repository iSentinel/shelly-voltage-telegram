#!/usr/bin/env python3
"""Rehearse durable paused-mode configuration recovery in isolated QA."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from writer_fence import (  # noqa: E402
    WriterFenceError,
    quiescent_writer_lease,
    update_gate,
)

OLD = b"SCHEDULED_REPORTS_MODE=enabled\n"
NEW = b"SCHEDULED_REPORTS_MODE=paused\n"
PREFIX = "svt-report-mode-tx-qa."
QA_IMAGE = (
    "sha256:07de8c4ade6e9a47001d99c8894880ab2478113c132c22e0c97cbd605b3ba668"
)


def marker_module():
    """Load the existing shared-marker implementation."""
    spec = importlib.util.spec_from_file_location(
        "mode_marker", Path(__file__).with_name("writer-fence-marker.py")
    )
    if spec is None or spec.loader is None:
        raise ValueError("marker helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marker_path(root: Path) -> Path:
    """Use the same durable gate as the monitor and supervisor."""
    return root / "fence/.svt-update-pending"


def own_marker(root: Path) -> None:
    """Require an exact root-controlled marker, never a foreign update."""
    path = marker_path(root)
    protected(path)
    if marker_module().marker_value(path) != root.name:
        raise ValueError("foreign startup marker")


def prepare_fenced(root: Path) -> dict:
    """Block starts before durable pause intent is committed."""
    if os.path.lexists(marker_path(root)):
        raise ValueError("existing startup marker")
    if (
        any(
            os.path.lexists(root / name)
            for name in ("before.env", "transaction.json")
        )
        or (root / ".env").read_bytes() != OLD
    ):
        raise ValueError("existing or unexpected transaction state")
    marker_module().publish(marker_path(root), root.name)
    return prepare(root)


def recover_fenced(root: Path) -> dict:
    """Recover configuration while preserving the startup fence."""
    own_marker(root)
    return recover(root)


def inspect_qa_container(root: Path, name: str) -> dict:
    """Read only a named stopped, networkless fixture container."""
    if name != "svt-qa-" + root.name:
        raise ValueError("unexpected QA container name")
    result = subprocess.run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    items = json.loads(result.stdout)
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError("unexpected container inspection")
    return items[0]


def validate_paused(root: Path, info: dict, *, running: bool = False) -> None:
    """Require the exact stopped image, mode and two shared mounts."""
    host = info["HostConfig"]
    env = info["Config"]["Env"]
    modes = [v for v in env if v.split("=", 1)[0] == "SCHEDULED_REPORTS_MODE"]
    if (
        info["State"]["Running"] is not running
        or info["Image"] != QA_IMAGE
        or host["NetworkMode"] != "none"
        or host["RestartPolicy"]["Name"] != "no"
        or modes != ["SCHEDULED_REPORTS_MODE=paused"]
    ):
        raise ValueError("unverified paused container")
    for destination, source in (
        ("/data", root / "data"),
        ("/run/svt-fence", root / "fence"),
    ):
        mounts = [m for m in info["Mounts"] if m["Destination"] == destination]
        if len(mounts) != 1 or (
            mounts[0]["Source"] != str(source)
            or mounts[0]["RW"] is not True
            or mounts[0]["Type"] != "bind"
        ):
            raise ValueError("unverified QA mount")


def arm_paused(root: Path, name: str) -> dict:
    """Release only our startup marker after paused container validation."""
    record = read_journal(root)
    if record["phase"] != "configured" or (root / ".env").read_bytes() != NEW:
        raise ValueError("configuration not committed")
    protected(root / "before.env")
    if (root / "before.env").read_bytes() != OLD:
        raise ValueError("original configuration rescue changed")
    if os.path.lexists(marker_path(root)):
        own_marker(root)
    info = inspect_qa_container(root, name)
    validate_paused(root, info)
    armed = bind_armed_identity(root, info)
    if os.path.lexists(marker_path(root)):
        own_marker(root)
        marker_module().clear(marker_path(root), root.name)
    return {**record, **armed, "startup_gate_released": True}


def bind_armed_identity(root: Path, info: dict) -> dict:
    """Persist immutable identity before release; replay only that identity."""
    identity = info["Id"]
    if not isinstance(identity, str) or not re.fullmatch(
        "[a-f0-9]{64}", identity
    ):
        raise ValueError("invalid container identity")
    record = {
        "container_id": identity,
        "image_id": QA_IMAGE,
        "mode": "paused",
        "runtime_verified": False,
    }
    path = root / "armed-container.json"
    if os.path.lexists(path):
        protected(path)
        if (
            path.stat().st_size > 4096
            or json.loads(path.read_text()) != record
        ):
            raise ValueError("armed container identity changed")
    else:
        own_marker(root)
        atomic_write(
            path, (json.dumps(record, sort_keys=True) + "\n").encode()
        )
    return record


def read_record(path: Path) -> dict:
    """Read a bounded root-controlled journal object."""
    protected(path)
    if path.stat().st_size > 4096:
        raise ValueError("oversized record")
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise ValueError("invalid record")
    return record


def start_binding(root: Path, name: str, *, running: bool) -> dict:
    """Verify the original ARM identity and committed pause configuration."""
    if os.path.lexists(marker_path(root)):
        raise ValueError("startup marker still present")
    if (
        read_journal(root)["phase"] != "configured"
        or (root / ".env").read_bytes() != NEW
    ):
        raise ValueError("configuration not committed")
    info = inspect_qa_container(root, name)
    validate_paused(root, info, running=running)
    armed = read_record(root / "armed-container.json")
    if armed != {
        "container_id": info["Id"],
        "image_id": QA_IMAGE,
        "mode": "paused",
        "runtime_verified": False,
    }:
        raise ValueError("armed identity mismatch")
    return info


def history_snapshot(root: Path, through: int | None = None) -> dict:
    """Hash historical rows and report metadata in a read-only snapshot."""
    path = root / "data/qa.sqlite3"
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("invalid QA database")
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("database integrity failed")
        count, last = db.execute(
            "SELECT count(*),max(id) FROM measurements"
        ).fetchone()
        if not count or last is None:
            raise ValueError("empty history")
        rows = db.execute(
            "SELECT * FROM measurements WHERE id<=? ORDER BY id",
            (last if through is None else through,),
        ).fetchall()
        meta = db.execute(
            "SELECT key,value FROM meta WHERE key='daily_summary_day' OR "
            "key GLOB 'telegram_report_pending:*' OR "
            "key GLOB 'telegram_weekly_private_sent_*' OR "
            "key GLOB 'telegram_status_*' ORDER BY key"
        ).fetchall()
    after = path.lstat()
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("database identity changed")
    return {
        "inode": before.st_ino,
        "device": before.st_dev,
        "rows": count,
        "last_id": last,
        "prefix_sha256": sha(json.dumps(rows).encode()),
        "metadata_sha256": sha(json.dumps(meta).encode()),
    }


def record_start(root: Path, name: str) -> dict:
    """Publish a one-shot START intent before an operator starts Docker."""
    path = root / "start-intent.json"
    if os.path.lexists(path):
        raise ValueError("start already attempted; observe, do not repeat")
    info = start_binding(root, name, running=False)
    record = {
        "container_id": info["Id"],
        "previous_started_at": info["State"]["StartedAt"],
        "baseline": history_snapshot(root),
        "deployment_authorized": False,
    }
    atomic_write(path, (json.dumps(record, sort_keys=True) + "\n").encode())
    return record


def observe_start(root: Path, name: str) -> dict:
    """Review an existing QA launch without starting or adopting any writer."""
    intent = read_record(root / "start-intent.json")
    info = start_binding(root, name, running=True)
    started = info["State"]["StartedAt"]
    if (
        info["Id"] != intent["container_id"]
        or started == intent["previous_started_at"]
    ):
        raise ValueError("no new armed launch")
    baseline = intent["baseline"]
    snapshot = history_snapshot(root, baseline["last_id"])
    for key in ("inode", "device", "prefix_sha256", "metadata_sha256"):
        if snapshot[key] != baseline[key]:
            raise ValueError("runtime history or metadata changed")
    if snapshot["rows"] <= baseline["rows"]:
        raise ValueError("no new measurements")
    final = start_binding(root, name, running=True)
    if final["Id"] != info["Id"] or final["State"]["StartedAt"] != started:
        raise ValueError("launch changed during review")
    record = {
        "container_id": info["Id"],
        "started_at": started,
        "baseline_rows": baseline["rows"],
        "observed_rows": snapshot["rows"],
        "history_verified": True,
        "runtime_verified": False,
        "deployment_authorized": False,
    }
    path = root / "start-observation.json"
    if os.path.lexists(path):
        prior = read_record(path)
        if (
            prior["container_id"] != record["container_id"]
            or prior["started_at"] != started
        ):
            raise ValueError("observed launch changed")
        return prior
    atomic_write(path, (json.dumps(record, sort_keys=True) + "\n").encode())
    return record


def sha(data: bytes) -> str:
    """Hash exact configuration bytes."""
    return hashlib.sha256(data).hexdigest()


def protected(path: Path, directory: bool = False) -> None:
    """Reject linked or untrusted files and ancestors."""
    for item in (path, *path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unprotected QA path")
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("invalid QA directory")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("invalid QA file")


def require_qa(root: Path) -> None:
    """Allow only dedicated, root-owned offline rehearsal directories."""
    if (
        os.geteuid() != 0
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or root.parent != Path("/var/lib")
        or not root.name.startswith(PREFIX)
        or sorted(name for _, name in socket.if_nameindex()) != ["lo"]
    ):
        raise ValueError("isolated QA only")
    protected(root, directory=True)
    protected(root / ".env")
    protected(root / "operator.lock")
    protected(root / "fence", directory=True)
    if os.environ.get("SVT_WRITER_FENCE_DIR") != str(root / "fence"):
        raise ValueError("wrong QA fence")


def sync_dir(root: Path) -> None:
    """Persist a directory entry after atomic publication."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Write a private file then atomically publish its durable contents."""
    fd, name = tempfile.mkstemp(prefix=".mode-tx-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def journal_value(phase: str) -> dict:
    """Restrict the prototype to one exact enabled-to-paused transition."""
    return {
        "phase": phase,
        "old_sha256": sha(OLD),
        "new_sha256": sha(NEW),
        "runtime_verified": False,
        "deployment_authorized": False,
    }


def read_journal(root: Path) -> dict:
    """Fail closed on an unrecognized journal or arbitrary hashes."""
    path = root / "transaction.json"
    protected(path)
    if path.stat().st_size > 4096:
        raise ValueError("oversized journal")
    record = json.loads(path.read_text())
    if record not in (journal_value("prepared"), journal_value("configured")):
        raise ValueError("unrecognized journal")
    return record


def save_journal(root: Path, phase: str) -> None:
    """Publish the durable phase, never claiming runtime verification."""
    atomic_write(
        root / "transaction.json",
        (json.dumps(journal_value(phase), sort_keys=True) + "\n").encode(),
    )


def prepare(root: Path) -> dict:
    """Retain exact original bytes and publish intent before changing mode."""
    env = root / ".env"
    for name in ("transaction.json", "before.env"):
        if os.path.lexists(root / name):
            raise ValueError("existing transaction or rescue")
    if env.read_bytes() != OLD:
        raise ValueError("unexpected original QA configuration")
    atomic_write(root / "before.env", OLD)
    save_journal(root, "prepared")
    return journal_value("prepared")


def recover(root: Path) -> dict:
    """Roll forward only an exact prepared transition; never re-enable."""
    record = read_journal(root)
    protected(root / "before.env")
    if (root / "before.env").read_bytes() != OLD:
        raise ValueError("original configuration rescue changed")
    current = (root / ".env").read_bytes()
    if record["phase"] == "configured":
        if current != NEW:
            raise ValueError("configured mode drifted")
        return record
    if current not in (OLD, NEW):
        raise ValueError("unexpected configuration drift")
    if current == OLD:
        atomic_write(root / ".env", NEW)
    save_journal(root, "configured")
    return journal_value("configured")


@contextmanager
def exclusive(root: Path, *, observation: bool = False):
    """Serialize operator work and gate a consistent live observation."""
    fd = os.open(root / "operator.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        database = root / "data/qa.sqlite3"
        if observation:
            with update_gate(database):
                yield
        else:
            with quiescent_writer_lease(database), update_gate(database):
                yield
    finally:
        os.close(fd)


def main() -> int:
    """Prepare or replay a configuration-only QA transaction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("prepare", "recover", "arm", "start-intent", "observe"),
    )
    parser.add_argument("--container")
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        require_qa(args.root)
        with exclusive(args.root, observation=args.action == "observe"):
            if args.action == "prepare":
                result = prepare_fenced(args.root)
            elif args.action == "recover":
                result = recover_fenced(args.root)
            elif args.action == "arm":
                result = arm_paused(args.root, args.container)
            elif args.action == "start-intent":
                result = record_start(args.root, args.container)
            else:
                result = observe_start(args.root, args.container)
    except (
        OSError,
        ValueError,
        WriterFenceError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        sqlite3.Error,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
