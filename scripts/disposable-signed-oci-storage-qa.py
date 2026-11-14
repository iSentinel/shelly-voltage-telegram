#!/usr/bin/env python3
"""Run real OCI Storage crash QA with no network, ports or production data."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIRM = "DISPOSABLE_SIGNED_OCI_STORAGE_QA_ONLY"
IMAGE = re.compile(
    r"ghcr\.io/isentinel/shelly-voltage-telegram@sha256:[0-9a-f]{64}\Z"
)
NAME = re.compile(r"svt-qa-oci-[0-9a-f]{12}-(seed|crash|verify)\Z")
PRODUCTION = "shelly-voltage-bot"
ROWS = 65000

SEED = """
import app,sqlite3
from pathlib import Path
from contextlib import closing
assert app.SCHEMA_VERSION == 3
path = Path('/data/history.sqlite3')
storage = app.Storage(path,'Europe/Kyiv')
storage.initialize()
with closing(sqlite3.connect(path)) as db:
    db.executemany(
        'INSERT INTO measurements(ts,voltage,frequency,phase) VALUES(?,?,?,?)',
        ((1700000000.0+i*30,211+(i%25)*.4,50.0,'L1') for i in range(65000)),
    )
    db.execute(
        "INSERT INTO outages(started_at,ended_at,phase) VALUES(?,?,?)",
        (1700000150.0,1700000300.0,'L1')
    )
    db.commit()
with closing(sqlite3.connect(path)) as db:
    assert db.execute('SELECT count(*) FROM measurements').fetchone()[0]==65000
    assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
print('SEED_PASS_65000')
"""

CRASH = """
import app,sqlite3,time
from pathlib import Path
path=Path('/data/history.sqlite3')
storage=app.Storage(path,'Europe/Kyiv')
storage.initialize()
storage.add(1701950000.0,225.4,50,'L1')
db=sqlite3.connect(path)
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute(
  "INSERT INTO measurements(ts,voltage,frequency,phase)"
  " VALUES(1701950030,201.1,50,'L1')"
)
Path('/data/qa-ready').write_text('committed_and_uncommitted')
time.sleep(90)
"""

VERIFY = """
import app,json,sqlite3
from pathlib import Path
from contextlib import closing
path=Path('/data/history.sqlite3')
storage=app.Storage(path,'Europe/Kyiv')
storage.initialize()
with closing(sqlite3.connect(path)) as db:
    count=db.execute('SELECT count(*) FROM measurements').fetchone()[0]
    commit=db.execute(
      'SELECT voltage FROM measurements WHERE ts=1701950000'
    ).fetchone()
    partial=db.execute(
      'SELECT voltage FROM measurements WHERE ts=1701950030'
    ).fetchone()
    check=db.execute('PRAGMA quick_check').fetchone()[0]
assert count==65001 and commit==(225.4,) and partial is None
assert check=='ok'
storage.add(1701950060,223.7,50,'L1')
with closing(sqlite3.connect(path)) as db:
    rows=db.execute('SELECT count(*) FROM measurements').fetchone()[0]
    recent=db.execute(
      'SELECT voltage FROM measurements WHERE ts>=1701950000 ORDER BY ts'
    ).fetchall()
    check=db.execute('PRAGMA quick_check').fetchone()[0]
assert rows==65002 and recent==[(225.4,),(223.7,)] and check=='ok'
print(json.dumps({'status':'PASS','rows':rows,'quick_check':check,
 'last_committed':225.4,'last_appended':223.7,'uncommitted_absent':True}))
"""


class Blocked(Exception):
    """Refuse any untrusted QA execution or unsupported host state."""


def command(arguments: list[str], timeout: int = 30) -> str:
    """Call only bounded explicit argv; never invoke a shell or network."""
    try:
        result = subprocess.run(
            arguments,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_COMMAND_UNAVAILABLE") from exc
    if result.returncode:
        raise Blocked("QA_COMMAND_FAILED")
    if len(result.stdout) > 8192:
        raise Blocked("QA_OUTPUT_TOO_LARGE")
    return result.stdout.strip()


def inspect_production() -> str:
    """Observe a single immutable production container identity."""
    return command(
        [
            "docker",
            "inspect",
            "--format",
            (
                "{{.Id}}|{{.RestartCount}}|"
                "{{.State.Health.Status}}|{{.Config.Image}}"
            ),
            PRODUCTION,
        ]
    )


def common(image: str, directory: Path, name: str) -> list[str]:
    """Build strict resource-constrained, no-network Docker QA argv."""
    if not NAME.fullmatch(name) or not IMAGE.fullmatch(image):
        raise Blocked("QA_IMAGE_OR_NAME_INVALID")
    if not directory.is_dir() or not directory.is_absolute():
        raise Blocked("QA_SCRATCH_INVALID")
    return [
        "--name",
        name,
        "--pull",
        "never",
        "--network",
        "none",
        "--read-only",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--pids-limit",
        "64",
        "--memory",
        "256m",
        "--cpus",
        "0.5",
        "--user",
        "10001:10001",
        "--restart",
        "no",
        "--mount",
        f"type=bind,src={directory},dst=/data",
        "--tmpfs",
        "/tmp:rw,nosuid,noexec,size=16m",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--entrypoint",
        "python",
        image,
    ]


def remove_qa(name: str) -> None:
    """Remove exactly a generated QA name, never a production container."""
    if not NAME.fullmatch(name):
        raise Blocked("QA_CLEANUP_TARGET_INVALID")
    try:
        result = subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_CLEANUP_UNAVAILABLE") from exc
    if result.returncode and "No such container:" not in result.stderr:
        raise Blocked("QA_CLEANUP_FAILED")


def start_and_crash(args: list[str], name: str, marker: Path) -> None:
    """SIGKILL only a disposable writer after a known committed record."""
    command(["docker", "create", *args, "-c", CRASH])
    command(["docker", "start", name])
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not marker.is_file():
        if (
            command(
                ["docker", "inspect", "--format", "{{.State.Running}}", name]
            )
            != "true"
        ):
            raise Blocked("QA_WRITER_EXITED_BEFORE_COMMIT")
        time.sleep(0.1)
    if not marker.is_file():
        raise Blocked("QA_WRITER_COMMIT_MARKER_MISSING")
    if (
        command(
            [
                "docker",
                "inspect",
                "--format",
                (
                    "{{.HostConfig.NetworkMode}}|"
                    "{{.HostConfig.RestartPolicy.Name}}"
                ),
                name,
            ]
        )
        != "none|no"
    ):
        raise Blocked("QA_WRITER_ISOLATION_CHANGED")
    command(["docker", "kill", "--signal", "SIGKILL", name])
    if command(["docker", "wait", name]) != "137":
        raise Blocked("QA_WRITER_KILL_RESULT_INVALID")
    if (
        command(["docker", "inspect", "--format", "{{.RestartCount}}", name])
        != "0"
    ):
        raise Blocked("QA_WRITER_RESTARTED_UNEXPECTEDLY")


def verify_output(output: str) -> dict:
    """Require strict positive QA evidence from the image itself."""
    try:
        value = json.loads(output)
    except (ValueError, TypeError) as exc:
        raise Blocked("QA_VERIFY_OUTPUT_INVALID") from exc
    if value != {
        "status": "PASS",
        "rows": 65002,
        "quick_check": "ok",
        "last_committed": 225.4,
        "last_appended": 223.7,
        "uncommitted_absent": True,
    }:
        raise Blocked("QA_RECOVERY_VERIFICATION_FAILED")
    return value


def run_isolated(image: str) -> dict:
    """Run a signed-image storage test on disposable fake data only."""
    before = inspect_production()
    command(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    prefix = "svt-qa-oci-" + secrets.token_hex(6)
    names = [prefix + "-" + suffix for suffix in ("seed", "crash", "verify")]
    try:
        with tempfile.TemporaryDirectory(
            prefix="svt-signed-oci-storage-", dir="/var/tmp"
        ) as temp:
            data = Path(temp) / "data"
            data.mkdir(mode=0o700)
            os.chown(data, 10001, 10001)
            seed_args = common(image, data, names[0])
            crash_args = common(image, data, names[1])
            verify_args = common(image, data, names[2])
            if (
                command(["docker", "run", "--rm", *seed_args, "-c", SEED])
                != "SEED_PASS_65000"
            ):
                raise Blocked("QA_SEED_FAILED")
            start_and_crash(crash_args, names[1], data / "qa-ready")
            value = verify_output(
                command(["docker", "run", "--rm", *verify_args, "-c", VERIFY])
            )
    finally:
        cleanup_errors = 0
        for name in names:
            try:
                remove_qa(name)
            except Blocked:
                cleanup_errors += 1
        if cleanup_errors:
            raise Blocked("QA_CLEANUP_INCOMPLETE")
    if inspect_production() != before:
        raise Blocked("PRODUCTION_IDENTITY_CHANGED")
    return value


def main() -> int:
    """Default inert dry-run; execute only under explicit QA consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--image", default="")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY-RUN PASS: Docker, SQLite and systemd untouched")
        return 0
    if args.confirm != CONFIRM or not IMAGE.fullmatch(args.image):
        print("FAIL: exact QA confirmation and immutable GHCR digest required")
        return 2
    try:
        value = run_isolated(args.image)
    except (Blocked, OSError) as exc:
        print(f"FAIL: {type(exc).__name__}", file=sys.stderr)
        return 3
    if args.jsonl:
        print(json.dumps(value, separators=(",", ":")))
    else:
        print("PASS: isolated signed OCI Storage WAL recovery", value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
