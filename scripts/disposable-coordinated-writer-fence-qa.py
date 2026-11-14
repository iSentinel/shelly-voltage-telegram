#!/usr/bin/env python3
"""Prove coordinated disposable Docker, supervisor and timer writer fencing."""

from __future__ import annotations

import argparse
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

CONFIRM = "DISPOSABLE_COORDINATED_WRITER_FENCE_QA_ONLY"
PREFIX = "svt-qa-multi-"
NAME = re.compile(r"svt-qa-multi-[a-f0-9]{12}\Z")
IMAGE = re.compile(r"sha256:[a-f0-9]{64}\Z")
PRODUCTION = "shelly-voltage-bot"
SOURCE_MARKER = "SYNTHETIC SOURCE ONLY; NOT AN OFFICIAL RELEASE.\n"
WRITER = """
import sqlite3
import time
db = sqlite3.connect('/qa/history.sqlite3', timeout=5, isolation_level=None)
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
while True:
    db.execute('INSERT INTO measurements(ts,voltage,phase) VALUES(?,?,?)',
               (time.time(),222.2,'L1'))
    time.sleep(.15)
"""


class Blocked(Exception):
    """A disposable-only safety or history acceptance condition failed."""


def run(args: list[str], *, allow_failure: bool = False) -> str:
    """Execute exact argument arrays and reject unexpected command output."""
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=23,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_COMMAND_UNAVAILABLE") from exc
    if len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise Blocked("QA_OUTPUT_TOO_LARGE")
    if result.returncode and not allow_failure:
        raise Blocked("QA_COMMAND_FAILED")
    return result.stdout.strip()


def safe_name(name: str) -> None:
    """Only random isolated names may be addressed."""
    if not NAME.fullmatch(name) or name == PRODUCTION:
        raise Blocked("QA_TARGET_NAME_UNSAFE")


def unit(name: str, purpose: str) -> str:
    """Build the exact two permitted transient systemd unit names."""
    safe_name(name)
    if purpose not in ("supervisor", "restart"):
        raise Blocked("QA_SYSTEMD_UNIT_UNSAFE")
    return name + "-" + purpose


def docker(name: str, action: str, *args: str) -> str:
    """Restrict Docker commands to a generated disposable container."""
    safe_name(name)
    if action not in ("start", "stop", "update", "inspect"):
        raise Blocked("QA_DOCKER_ACTION_UNSAFE")
    return run(["docker", action, *args, name])


def production_identity() -> tuple[str, ...]:
    """Read real container metadata without any production mutations."""
    output = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}}|{{.RestartCount}}|{{.State.Running}}|{{.Image}}",
            PRODUCTION,
        ]
    )
    fields = output.split("|")
    if len(fields) != 4 or not IMAGE.fullmatch(fields[3]):
        raise Blocked("PRODUCTION_INSPECT_UNAVAILABLE")
    return tuple(fields)


def production_owners() -> tuple[str, str]:
    """Read production Compose and update timer states without mutation."""
    units = (
        "shelly-voltage-compose.service",
        "shelly-voltage-update-check.timer",
    )
    return tuple(
        run(
            [
                "systemctl",
                "show",
                name,
                "-p",
                "ActiveState",
                "-p",
                "UnitFileState",
                "--no-pager",
            ]
        )
        for name in units
    )


def docker_state(name: str) -> tuple[str, str, str, str]:
    """Inspect disposable running state, policy, network and mount count."""
    fields = docker(
        name,
        "inspect",
        "--format",
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}"
        "|{{.HostConfig.NetworkMode}}|{{len .Mounts}}",
    ).split("|")
    if len(fields) != 4 or fields[0] not in ("true", "false"):
        raise Blocked("QA_DOCKER_STATE_INVALID")
    return tuple(fields)


def create_fixture(root: Path) -> None:
    """Make synthetic SQLite and a harmless source marker in 0700 scratch."""
    source = root / "source"
    source.mkdir(mode=0o700)
    (source / "README.txt").write_text(SOURCE_MARKER)
    with closing(sqlite3.connect(root / "history.sqlite3")) as db:
        db.execute(
            "CREATE TABLE measurements("
            "id INTEGER PRIMARY KEY,ts REAL,voltage REAL,phase TEXT)"
        )
        db.execute("CREATE TABLE outages(id INTEGER,started REAL,ended REAL)")
        db.execute("CREATE TABLE meta(key TEXT,value TEXT)")
        db.execute("PRAGMA user_version=7")
        db.executemany(
            "INSERT INTO measurements(ts,voltage,phase) VALUES(?,?,?)",
            [(i, 220 + i % 6, "L1") for i in range(120)],
        )
        db.execute("INSERT INTO outages VALUES(30,10,20)")
        db.execute("INSERT INTO meta VALUES('qa','synthetic')")
        db.commit()


def source_identity(root: Path) -> str:
    """Hash the only permitted synthetic source content."""
    payload = (root / "source/README.txt").read_bytes()
    if payload != SOURCE_MARKER.encode("utf-8"):
        raise Blocked("QA_SOURCE_MARKER_CHANGED")
    return hashlib.sha256(payload).hexdigest()


def db_identity(path: Path) -> tuple[str, int]:
    """Read full SQLite logical history, including committed WAL values."""
    digest = hashlib.sha256()
    with closing(sqlite3.connect(path, timeout=3)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("QA_DATABASE_INVALID")
        for line in db.iterdump():
            digest.update(line.encode("utf-8"))
            digest.update(b"\n")
        for key in ("user_version", "application_id"):
            value = db.execute(f"PRAGMA {key}").fetchone()[0]
            digest.update(f"{key}:{value}\n".encode("ascii"))
        rows = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
    return digest.hexdigest(), rows


def create_container(name: str, image: str, root: Path) -> None:
    """Create one networkless QA writer with only its synthetic bind mount."""
    safe_name(name)
    if not IMAGE.fullmatch(image):
        raise Blocked("QA_IMAGE_NOT_IMMUTABLE")
    run(
        [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            "svt.qa.disposable=true",
            "--network",
            "none",
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
            "--restart=unless-stopped",
            "--entrypoint",
            "python3",
            image,
            "-u",
            "-c",
            WRITER,
        ]
    )
    if docker_state(name) != ("false", "unless-stopped", "none", "1"):
        raise Blocked("QA_CONTAINER_ISOLATION_FAILED")


def start_supervisor(name: str) -> None:
    """Supervise QA Docker through a transient unit, never real Compose."""
    base = unit(name, "supervisor")
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            base,
            "--property=Type=simple",
            "--property=Restart=no",
            "--property=NoNewPrivileges=yes",
            f"--property=ExecStop=/usr/bin/docker stop -t 3 {name}",
            "/usr/bin/docker",
            "start",
            "--attach",
            name,
        ]
    )


def await_supervisor_exit(name: str) -> None:
    """Prevent the old supervisor ExecStop from racing a timer restart."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if supervisor_state(name) not in ("active", "activating"):
            return
        time.sleep(0.2)
    raise Blocked("QA_SUPERVISOR_STILL_SHUTTING_DOWN")


def arm_external_timer(name: str) -> None:
    """Simulate a second independent writer owner with a precise timer."""
    run(
        [
            "systemd-run",
            "--collect",
            "--unit",
            unit(name, "restart"),
            "--on-active=3s",
            "--timer-property=AccuracySec=1ms",
            "--timer-property=RandomizedDelaySec=0",
            "--property=Type=oneshot",
            "/usr/bin/docker",
            "start",
            name,
        ]
    )


def timer_state(name: str) -> str:
    """Read only the generated test timer's current state."""
    return run(
        [
            "systemctl",
            "show",
            unit(name, "restart") + ".timer",
            "-p",
            "ActiveState",
            "--value",
        ],
        allow_failure=True,
    )


def supervisor_state(name: str) -> str:
    """Read only the generated test service's current state."""
    return run(
        [
            "systemctl",
            "show",
            unit(name, "supervisor") + ".service",
            "-p",
            "ActiveState",
            "--value",
        ],
        allow_failure=True,
    )


def stop_owners(name: str) -> None:
    """Neutralize every modeled restart owner before stopping QA writer."""
    run(
        [
            "systemctl",
            "stop",
            unit(name, "restart") + ".timer",
            unit(name, "restart") + ".service",
        ],
        allow_failure=True,
    )
    run(
        [
            "systemctl",
            "stop",
            unit(name, "supervisor") + ".service",
        ],
        allow_failure=True,
    )
    docker(name, "update", "--restart=no")
    docker(name, "stop", "-t", "3")


def assert_fenced(name: str, root: Path) -> tuple[str, int]:
    """Reject an active supervisor, timer, restart policy or SQLite drift."""
    if docker_state(name) != ("false", "no", "none", "1"):
        raise Blocked("QA_WRITER_NOT_STOPPED_OR_RESTART_ENABLED")
    if timer_state(name) == "active" or supervisor_state(name) == "active":
        raise Blocked("QA_EXTERNAL_RESTART_OWNER_STILL_ACTIVE")
    initial = db_identity(root / "history.sqlite3")
    time.sleep(0.4)
    if db_identity(root / "history.sqlite3") != initial:
        raise Blocked("QA_SQLITE_STILL_CHANGING")
    return initial


def await_growth(root: Path, baseline: int) -> int:
    """Prove live synthetic SQLite rows advance after a writer starts."""
    deadline = time.monotonic() + 9
    while time.monotonic() < deadline:
        count = db_identity(root / "history.sqlite3")[1]
        if count > baseline:
            return count
        time.sleep(0.2)
    raise Blocked("QA_WRITER_NOT_ADVANCING")


def await_timer_restart(name: str, root: Path, baseline: int) -> int:
    """Require the unfenced independent timer to restart the QA writer."""
    deadline = time.monotonic() + 11
    while time.monotonic() < deadline:
        if docker_state(name)[0] == "true":
            return await_growth(root, baseline)
        time.sleep(0.25)
    raise Blocked("QA_EXTERNAL_TIMER_DID_NOT_RESTART")


def backup_pair(name: str, root: Path) -> int:
    """Capture and independently verify A/B whole-history SQLite and source."""
    original = assert_fenced(name, root)
    source_hash = source_identity(root)
    for label in ("A", "B"):
        copy = root / "snapshots" / label
        copy.mkdir(parents=True, mode=0o700)
        if assert_fenced(name, root) != original:
            raise Blocked("QA_DATABASE_CHANGED_DURING_FENCE")
        backup = copy / "history.sqlite3"
        with (
            closing(sqlite3.connect(root / "history.sqlite3")) as live,
            closing(sqlite3.connect(backup)) as destination,
        ):
            live.backup(destination)
        if db_identity(backup) != original:
            raise Blocked("QA_INDEPENDENT_DB_BACKUP_MISMATCH")
        with tarfile.open(copy / "source.tar.gz", "w:gz") as archive:
            archive.add(root / "source", arcname="source")
        with tarfile.open(copy / "source.tar.gz", "r:gz") as archive:
            marker = archive.extractfile("source/README.txt")
            if marker is None or marker.read(128) != SOURCE_MARKER.encode():
                raise Blocked("QA_INDEPENDENT_SOURCE_RESTORE_MISMATCH")
        if source_identity(root) != source_hash:
            raise Blocked("QA_SOURCE_CHANGED_DURING_BACKUP")
    if assert_fenced(name, root) != original:
        raise Blocked("QA_A_B_NOT_POINT_IN_TIME")
    return original[1]


def controlled_resume(name: str, root: Path, recorded: int) -> int:
    """Resume only QA Docker after recording the complete test fence."""
    if docker_state(name) != ("false", "no", "none", "1"):
        raise Blocked("QA_CONTROLLED_RESUME_UNSAFE")
    docker(name, "start")
    advanced = await_growth(root, recorded)
    docker(name, "stop", "-t", "3")
    return advanced


def cleanup(name: str) -> None:
    """Attempt all isolated cleanup operations, including on QA failure."""
    safe_name(name)
    for target in (
        unit(name, "restart") + ".timer",
        unit(name, "restart") + ".service",
        unit(name, "supervisor") + ".service",
    ):
        run(["systemctl", "stop", target], allow_failure=True)
        run(["systemctl", "reset-failed", target], allow_failure=True)
    run(["docker", "update", "--restart=no", name], allow_failure=True)
    run(["docker", "rm", "-f", name], allow_failure=True)


def verify_cleanup(name: str) -> None:
    """Fail if a disposable Docker object or active transient unit remains."""
    safe_name(name)
    output = run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"name=^{name}$",
        ]
    )
    if output:
        raise Blocked("QA_CONTAINER_LEFT_BEHIND")
    if timer_state(name) == "active" or supervisor_state(name) == "active":
        raise Blocked("QA_SYSTEMD_UNIT_LEFT_RUNNING")


def prerequisites() -> None:
    """Demand root and installed systemd/Docker tools for real QA only."""
    if os.geteuid() != 0:
        raise Blocked("QA_ROOT_REQUIRED")
    for binary in ("docker", "systemctl", "systemd-run"):
        if shutil.which(binary) is None:
            raise Blocked("QA_REQUIRED_BINARY_MISSING")


def exercise(name: str, root: Path) -> None:
    """Demonstrate a restart race, coordinated fence and safe A/B recovery."""
    start_supervisor(name)
    initial = await_growth(root, 120)
    docker(name, "stop", "-t", "3")
    await_supervisor_exit(name)
    arm_external_timer(name)
    first_restarted = await_timer_restart(name, root, initial)
    print(f"PASS unfenced timer restarted QA writer: {first_restarted}")
    stop_owners(name)
    frozen = backup_pair(name, root)
    print(f"PASS coordinated fence and exact A/B: {frozen}")
    resumed = controlled_resume(name, root, frozen)
    print(f"PASS controlled QA resume after A/B: {resumed}")


def execute() -> None:
    """Run one disposable integrated test and verify production identity."""
    prerequisites()
    original = production_identity()
    owners = production_owners()
    if original[2] != "true":
        raise Blocked("PRODUCTION_NOT_RUNNING")
    name = PREFIX + secrets.token_hex(6)
    failure: Exception | None = None
    with tempfile.TemporaryDirectory(
        prefix="svt-coordinated-fence-qa-", dir="/var/tmp"
    ) as directory:
        try:
            root = Path(directory)
            create_fixture(root)
            create_container(name, original[3], root)
            exercise(name, root)
        except (Blocked, sqlite3.Error, OSError, tarfile.TarError) as exc:
            failure = exc
        finally:
            cleanup(name)
            verify_cleanup(name)
    if production_identity() != original:
        raise Blocked("PRODUCTION_CONTAINER_CHANGED")
    if production_owners() != owners:
        raise Blocked("PRODUCTION_SYSTEMD_OWNER_CHANGED")
    if failure:
        raise Blocked(str(failure))
    print("PASS disposable multi-owner drill; production unchanged")


def main() -> int:
    """Dry-run by default; require exact opt-in for disposable integration."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    options = parser.parse_args()
    if not options.run_disposable:
        print("DRY-RUN PASS: no Docker or systemd commands executed")
        return 0
    if options.confirm != CONFIRM:
        print("FAIL: exact disposable-only confirmation required")
        return 2
    try:
        execute()
    except Blocked as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
