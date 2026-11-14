#!/usr/bin/env python3
"""Test isolated systemd-supervised Docker SQLite writer and A/B recovery."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import closing
from pathlib import Path

CONFIRM = "DISPOSABLE_SUPERVISED_SQLITE_WRITER_QA_ONLY"
PREFIX = "svt-qa-db-"
PRODUCTION = "shelly-voltage-bot"
NAME_PATTERN = re.compile(r"svt-qa-db-[a-f0-9]{12}\Z")
IMAGE_PATTERN = re.compile(r"sha256:[a-f0-9]{64}\Z")
MAX_OUTPUT = 4096
MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
SOURCE_MARKER = "DISPOSABLE SOURCE QA ONLY. NOT A SIGNED RELEASE." + chr(10)
WAIT_SECONDS = 8
WRITER_CODE = """
import sqlite3
import time
conn = sqlite3.connect('/qa/history.sqlite3', timeout=5, isolation_level=None)
conn.execute('PRAGMA journal_mode=WAL')
while True:
    conn.execute(
        "INSERT INTO measurements(ts,voltage,phase) VALUES(?,?,?)",
        (time.time(), 222.2, 'L1'),
    )
    time.sleep(0.15)
"""


class QAError(Exception):
    """An expected fail-closed condition in disposable regression."""


def run(args: list[str], *, failure_allowed: bool = False) -> str:
    """Run an argv-only process, rejecting failures and oversized output."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise QAError("QA_COMMAND_UNAVAILABLE") from exc
    if len(proc.stdout) > MAX_OUTPUT or len(proc.stderr) > MAX_OUTPUT:
        raise QAError("QA_UNEXPECTED_OUTPUT")
    if proc.returncode and not failure_allowed:
        raise QAError("QA_COMMAND_FAILED")
    return proc.stdout.strip()


def safe_name(name: str) -> None:
    """Reject every caller-controlled or production Docker target."""
    if not NAME_PATTERN.fullmatch(name) or name == PRODUCTION:
        raise QAError("QA_TARGET_NAME_UNSAFE")


def unit_name(name: str, suffix: str) -> str:
    """Generate exactly one private test service for a known phase."""
    safe_name(name)
    if suffix not in ("graceful", "kill", "resume"):
        raise QAError("QA_UNIT_SUFFIX_UNSAFE")
    return f"{name}-{suffix}.service"


def docker(name: str, command: str, *args: str) -> str:
    """Operate only on a generated QA Docker container."""
    safe_name(name)
    if command not in ("start", "stop", "update", "kill", "inspect"):
        raise QAError("QA_DOCKER_ACTION_UNSAFE")
    return run(["docker", command, *args, name])


def state(name: str) -> tuple[str, str, str]:
    """Inspect the disposable writer's running state and isolation."""
    output = docker(
        name,
        "inspect",
        "--format",
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}"
        "|{{.HostConfig.NetworkMode}}",
    )
    parts = output.split("|")
    if len(parts) != 3 or parts[0] not in ("true", "false"):
        raise QAError("QA_DOCKER_STATE_UNKNOWN")
    return tuple(parts)


def production_identity() -> tuple[str, str, str, str]:
    """Read the actual bot container identity without changing it."""
    output = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.RestartCount}}|{{.State.Running}}|{{.Image}}",
            PRODUCTION,
        ]
    )
    parts = output.split("|")
    if len(parts) != 4 or not IMAGE_PATTERN.fullmatch(parts[3]):
        raise QAError("PRODUCTION_INSPECT_FAILED")
    return tuple(parts)


def prepare_fixture(root: Path) -> None:
    """Create an owner-private synthetic database and source directory."""
    (root / "source").mkdir(mode=0o700)
    (root / "source" / "README.txt").write_text(SOURCE_MARKER)
    with closing(sqlite3.connect(root / "history.sqlite3")) as db:
        db.execute("PRAGMA user_version=7")
        db.execute(
            "CREATE TABLE measurements("
            "id INTEGER PRIMARY KEY, ts REAL,voltage REAL,phase TEXT)"
        )
        db.execute("CREATE TABLE outages(id INTEGER,started REAL,ended REAL)")
        db.execute("CREATE TABLE meta(k TEXT,v TEXT)")
        db.executemany(
            "INSERT INTO measurements(ts,voltage,phase) VALUES(?,?,?)",
            [(i, 218.0 + i % 10, "L1") for i in range(120)],
        )
        db.execute("INSERT INTO outages VALUES(30,10,20)")
        db.execute("INSERT INTO meta VALUES('origin','QA_SYNTHETIC')")
        db.commit()


def db_digest(path: Path) -> tuple[str, int]:
    """Hash all logical SQLite rows and verify the database is intact."""
    with closing(sqlite3.connect(path)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise QAError("QA_SQLITE_CHECK_FAILED")
        rows = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
        dump = "\n".join(db.iterdump()).encode("utf-8")
        flags = (
            db.execute("PRAGMA user_version").fetchone()[0],
            db.execute("PRAGMA application_id").fetchone()[0],
        )
    digest = hashlib.sha256(dump + repr(flags).encode("ascii")).hexdigest()
    return digest, rows


def assert_stopped(name: str, root: Path) -> int:
    """Prove the fenced synthetic writer no longer changes SQLite."""
    if state(name) != ("false", "no", "none"):
        raise QAError("QA_WRITER_FENCE_LOST")
    initial = db_digest(root / "history.sqlite3")
    time.sleep(0.45)
    if db_digest(root / "history.sqlite3") != initial:
        raise QAError("QA_WRITER_STILL_CHANGING_SQLITE")
    return initial[1]


def capture_one(root: Path, phase: str, copy: str) -> tuple[str, int]:
    """Capture one synthetic whole-history SQLite and source archive."""
    target = root / "snapshots" / phase / copy
    target.mkdir(parents=True, mode=0o700)
    database = root / "history.sqlite3"
    backed_up = target / "history.backup.sqlite3"
    with (
        closing(sqlite3.connect(database)) as source,
        closing(sqlite3.connect(backed_up)) as destination,
    ):
        source.backup(destination)
    with (
        backed_up.open("rb") as source,
        gzip.open(target / "history.sqlite3.gz", "wb") as output,
    ):
        shutil.copyfileobj(source, output)
    backed_up.unlink()
    with tarfile.open(target / "source.tar.gz", "w:gz") as output:
        output.add(root / "source", arcname="synthetic-source")
    return db_digest(database)


def verify_copy(root: Path, phase: str, copy: str) -> tuple[str, int]:
    """Restore each QA archive and compare all logical SQLite history."""
    folder = root / "snapshots" / phase / copy
    restored = folder / "verified.sqlite3"
    try:
        with (
            gzip.open(folder / "history.sqlite3.gz", "rb") as source,
            restored.open("xb") as target,
        ):
            data = source.read(MAX_ARCHIVE_BYTES + 1)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise QAError("QA_SQLITE_ARCHIVE_TOO_LARGE")
            target.write(data)
        with tarfile.open(folder / "source.tar.gz", "r:gz") as source:
            names = source.getnames()
            if names != ["synthetic-source", "synthetic-source/README.txt"]:
                raise QAError("QA_SOURCE_ARCHIVE_UNEXPECTED")
            file = source.extractfile("synthetic-source/README.txt")
            if file is None or file.read(256) != SOURCE_MARKER.encode():
                raise QAError("QA_SOURCE_ARCHIVE_CONTENT_CHANGED")
        return db_digest(restored)
    finally:
        restored.unlink(missing_ok=True)


def capture_pair(name: str, root: Path, phase: str) -> int:
    """Prove both test backups preserve the exact stopped SQLite state."""
    before = assert_stopped(name, root)
    digest = db_digest(root / "history.sqlite3")
    for copy in ("A", "B"):
        assert_stopped(name, root)
        if capture_one(root, phase, copy) != digest:
            raise QAError("QA_SQLITE_CHANGED_DURING_CAPTURE")
        if verify_copy(root, phase, copy) != digest:
            raise QAError("QA_BACKUP_RESTORE_MISMATCH")
    if assert_stopped(name, root) != before:
        raise QAError("QA_SNAPSHOT_ROWS_CHANGED")
    print(
        f"PASS {phase} stopped-writer A/B full-history parity: {before} rows"
    )
    return before


def writer_creation_args(name: str, image: str, root: Path) -> list[str]:
    """Construct only a networkless QA SQLite writer Docker command."""
    safe_name(name)
    if not IMAGE_PATTERN.fullmatch(image):
        raise QAError("QA_IMAGE_INVALID")
    return [
        "docker",
        "create",
        "--name",
        name,
        "--network",
        "none",
        "--label",
        "svt.qa.disposable=true",
        "--read-only",
        "--no-healthcheck",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "96m",
        "--pids-limit",
        "32",
        "--user",
        "0:0",
        "--mount",
        f"type=bind,src={root},dst=/qa",
        "--restart",
        "unless-stopped",
        "--entrypoint",
        "python3",
        image,
        "-u",
        "-c",
        WRITER_CODE,
    ]


def create_writer(name: str, image: str, root: Path) -> None:
    """Create an isolated QA writer with exactly one synthetic mount."""
    run(writer_creation_args(name, image, root))
    detail = docker(
        name,
        "inspect",
        "--format",
        "{{.HostConfig.NetworkMode}}|{{len .Mounts}}"
        "|{{.HostConfig.ReadonlyRootfs}}",
    )
    if detail != "none|1|true":
        raise QAError("QA_CONTAINER_ISOLATION_FAILED")


def start_unit(name: str, phase: str) -> str:
    """Have a temporary systemd unit supervise only one QA writer."""
    unit = unit_name(name, phase)
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            unit,
            "--property",
            "Type=simple",
            "--property",
            "Restart=no",
            "--property",
            "NoNewPrivileges=yes",
            "--property",
            "MemoryMax=64M",
            "--property",
            f"ExecStop=/usr/bin/docker stop -t 3 {name}",
            "/usr/bin/docker",
            "start",
            "--attach",
            name,
        ]
    )
    return unit


def count_rows(root: Path) -> int:
    """Read count from a synthetic database that may be in WAL mode."""
    return db_digest(root / "history.sqlite3")[1]


def await_growth(root: Path, previous: int) -> int:
    """Require new QA measurements shortly after the supervised start."""
    end = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < end:
        current = count_rows(root)
        if current > previous:
            return current
        time.sleep(0.2)
    raise QAError("QA_WRITER_DID_NOT_RESUME")


def stop_graceful(name: str, unit: str, root: Path) -> int:
    """Neutralize restart policy before stopping the QA unit/writer."""
    docker(name, "update", "--restart=no")
    run(["systemctl", "stop", unit])
    return assert_stopped(name, root)


def stop_abrupt(name: str, unit: str, root: Path) -> int:
    """SIGKILL only the QA writer with restart policy neutralized."""
    docker(name, "update", "--restart=no")
    docker(name, "kill", "--signal", "KILL")
    run(["systemctl", "stop", unit], failure_allowed=True)
    return assert_stopped(name, root)


def cleanup(name: str, units: list[str]) -> None:
    """Remove generated QA resources while avoiding all live units."""
    safe_name(name)
    for unit in units:
        if unit not in {
            unit_name(name, phase) for phase in ("graceful", "kill", "resume")
        }:
            raise QAError("QA_CLEANUP_UNIT_UNSAFE")
        run(["systemctl", "stop", unit], failure_allowed=True)
        run(["systemctl", "reset-failed", unit], failure_allowed=True)
    run(["docker", "rm", "-f", name], failure_allowed=True)


def ensure_no_orphans(name: str, units: list[str]) -> None:
    """Fail if a QA Docker object survived cleanup."""
    output = run(
        [
            "docker",
            "ps",
            "-a",
            "--format",
            "{{.Names}}",
            "--filter",
            f"name=^{name}$",
        ]
    )
    if output:
        raise QAError("QA_CONTAINER_CLEANUP_INCOMPLETE")
    for unit in units:
        result = run(
            ["systemctl", "show", unit, "-p", "ActiveState", "--value"],
            failure_allowed=True,
        )
        if result == "active":
            raise QAError("QA_SYSTEMD_CLEANUP_INCOMPLETE")


def prerequisites() -> None:
    """Require local Docker+systemd without user-selectable targets."""
    if os.geteuid() != 0:
        raise QAError("QA_ROOT_REQUIRED")
    for command in ("docker", "systemd-run", "systemctl"):
        if shutil.which(command) is None:
            raise QAError("QA_SYSTEM_TOOL_UNAVAILABLE")


def supervise(name: str, root: Path, units: list[str]) -> None:
    """Run graceful A/B, abrupt KILL A/B, then controlled writer resume."""
    initial = count_rows(root)
    if start_unit(name, "graceful") != units[0]:
        raise QAError("QA_SYSTEMD_UNIT_DRIFT")
    print(f"PASS QA initial writer grew to {await_growth(root, initial)}")
    stop_graceful(name, units[0], root)
    captured = capture_pair(name, root, "graceful")
    if start_unit(name, "kill") != units[1]:
        raise QAError("QA_SYSTEMD_UNIT_DRIFT")
    await_growth(root, captured)
    stop_abrupt(name, units[1], root)
    captured = capture_pair(name, root, "kill")
    if start_unit(name, "resume") != units[2]:
        raise QAError("QA_SYSTEMD_UNIT_DRIFT")
    latest = await_growth(root, captured)
    if state(name) != ("true", "no", "none"):
        raise QAError("QA_CONTROLLED_RESTART_UNVERIFIED")
    docker(name, "update", "--restart=unless-stopped")
    print(f"PASS QA writer resumed after SIGKILL: {latest} rows")


def execute() -> None:
    """Run the entire integration only on disposable test resources."""
    prerequisites()
    original = production_identity()
    if original[2] != "true":
        raise QAError("PRODUCTION_NOT_RUNNING")
    name = PREFIX + secrets.token_hex(6)
    safe_name(name)
    units = [
        unit_name(name, phase) for phase in ("graceful", "kill", "resume")
    ]
    failed: QAError | None = None
    with tempfile.TemporaryDirectory(
        prefix="svt-supervised-db-qa-", dir="/var/tmp"
    ) as directory:
        try:
            root = Path(directory)
            prepare_fixture(root)
            create_writer(name, original[3], root)
            supervise(name, root, units)
        except (
            QAError,
            OSError,
            sqlite3.Error,
            tarfile.TarError,
            EOFError,
        ) as exc:
            failed = QAError(str(exc))
        finally:
            cleanup(name, units)
            ensure_no_orphans(name, units)
    if production_identity() != original:
        raise QAError("PRODUCTION_CONTAINER_CHANGED")
    if failed is not None:
        raise failed
    print("PASS disposable full lifecycle, live production unchanged")


def main() -> int:
    """Use opt-in confirmation and clear PASS/FAIL exit statuses."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY-RUN PASS: no commands executed")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: explicit QA-only confirmation is required")
        return 2
    try:
        execute()
    except (QAError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
