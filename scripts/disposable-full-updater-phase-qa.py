#!/usr/bin/env python3
"""LXC114-only full candidate Bash updater-phase and WAL race QA."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

SOURCE = Path("/root/svt-pr336-guard")
ORIGINAL = Path("/var/lib/svt-qa-docker114/data/history.sqlite3")
CONFIRM = "ISOLATED_FULL_UPDATER_PHASE_QA_ONLY"
VERSION = "0.5.21-hw"
DIGEST = (
    "sha256:f880ffc9748ad97a34a5d1bcf5c0ec25fb33793240de70501fd9c46651a00639"
)
PINNED = {
    "apply-update.sh": (
        "99bf0516f505d3c66949817c2f9a54f393377df41dc43a354c123e12f1e8e6ba"
    ),
    "pending-recovery-history-guard.py": (
        "8d6e97a975d26affcd4136b3846a018e44e2f002579c945fa62f9ff4d66d6f09"
    ),
    "qa-backup-only.sh": (
        "505addfc3bf64e3d0780e7bc89af4211c62908d21e01c3ef8433124be0c78adc"
    ),
}

# The exact candidate main(), snapshot(), mark_pending() and activate()
# execute. External internet, signature, Docker and readiness are MOCKED.
BASH = r"""
set -Eeuo pipefail
source "$ROOT/scripts/apply-update.sh"
requirements() {
    mkdir -p "$STATE/snapshots"
    chmod 0700 "$STATE" "$STATE/snapshots"
    exec 9>"$ROOT/updater-A.lock"
    flock -w 2 9 || return 4
    echo requirements_a_held >> "$ROOT/events"
}
preflight() { echo preflight >> "$ROOT/events"; }
storage_preflight() { echo storage_preflight >> "$ROOT/events"; }
verify_image() { echo SIGNATURE_MOCKED_OFFLINE >> "$ROOT/events"; }
verify_source_archive() { echo SOURCE_FETCH_MOCKED_OFFLINE >> "$ROOT/events"; }
wait_healthy() { echo HEALTH_MOCKED_OFFLINE >> "$ROOT/events"; }
docker() {
    if [[ "$#" == 2 && "$1" == pull && "$2" == "$IMAGE@$QA_DIGEST" ]]; then
        echo DOCKER_PULL_MOCKED_OFFLINE >> "$ROOT/events"
        return 0
    fi
    echo "FAIL: UNSAFE_DOCKER_CALL $*" >&2
    return 91
}
python3() {
    if [[ "$#" -gt 0 && "$1" == "$ROOT/scripts/github_update.py" ]]; then
        case "$2" in
            guard) echo github_guard_mocked >> "$ROOT/events"; return 0;;
            digest) printf '%s\n' "$QA_DIGEST";return 0;;
            *) echo "FAIL: UNKNOWN_GITHUB_OP" >&2;return 92;;
        esac
    fi
    /usr/bin/python3 "$@"
}
main --version "$QA_VERSION"
"""
START_STUB = r"""#!/bin/bash
set -euo pipefail
[[ -n "$SVT_ROOT" && -n "$QA_EVENT_MODE" ]] || exit 80
ROOT="$SVT_ROOT"
[[ -f "$ROOT/state/pending-update" ]] || exit 81
printf 'start_stack_pending_seen\n' >> "$ROOT/events"
touch "$ROOT/start-stage"
for n in $(seq 1 120); do
    [[ -f "$ROOT/release-stage" ]] && break
    sleep .06
done
[[ -f "$ROOT/release-stage" ]] || exit 82
if [[ "$QA_EVENT_MODE" == stale ]]; then
    /usr/bin/python3 - "$ROOT/data/current.sqlite3" <<'PYSQL'
import sqlite3,sys,time
with sqlite3.connect(sys.argv[1]) as db:
 db.execute(
  "INSERT INTO measurements(ts,voltage,frequency,phase) "
  "VALUES(?,?,?,?)", (time.time(),221.7,50.0,'L1'))
PYSQL
    printf 'synthetic_wal_committed\n' >> "$ROOT/events"
    exit 77
fi
printf 'start_stack_mocked_success\n' >> "$ROOT/events"
"""


def guest_only() -> None:
    """Require offline test guest, pinned script bytes, stopped writer."""
    if os.geteuid() != 0 or os.uname().nodename != "shelly-docker-reboot-qa":
        raise RuntimeError("QA_GUEST_ONLY")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("EXTERNAL_NETWORK_PRESENT")
    for name, digest in PINNED.items():
        path = SOURCE / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("STAGED_SOURCE_INVALID")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("STAGED_SOURCE_SHA_MISMATCH")
    if ORIGINAL.is_symlink() or not ORIGINAL.is_file():
        raise RuntimeError("QA_HISTORY_INVALID")
    result = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}"
            "|{{.HostConfig.NetworkMode}}",
            "svt-qa-signed-writer-114",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    if result.stdout.strip() != "false|no|none":
        raise RuntimeError("QA_WRITER_NOT_STOPPED")
    for unit in ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer"):
        result = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.stdout.strip() != "disabled":
            raise RuntimeError("QA_SERVICE_ENABLED")


def prepare(root: Path) -> int:
    """Stage dummy credentials, exact QA code and SQLite backup."""
    for name in ("state", "data", "scripts", "secrets", "public"):
        (root / name).mkdir(mode=0o700)
    (root / ".env").write_text("QA_NOT_A_REAL_SECRET=1\n")
    (root / "secrets/placeholder").write_text("qa-only\n")
    (root / "state/override.yaml").write_text("qa: previous\n")
    (root / "state/deployed.json").write_text('{"version":"qa-prev"}\n')
    (root / "compose.yaml").write_text("services: {}\n")
    for name in ("apply-update.sh", "pending-recovery-history-guard.py"):
        shutil.copyfile(SOURCE / name, root / "scripts" / name)
    shutil.copyfile(SOURCE / "qa-backup-only.sh", root / "scripts/backup.sh")
    with (
        closing(
            sqlite3.connect(ORIGINAL.as_uri() + "?mode=ro", uri=True)
        ) as db,
        closing(sqlite3.connect(root / "data/current.sqlite3")) as target,
    ):
        db.backup(target)
    with closing(sqlite3.connect(root / "data/current.sqlite3")) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        count = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    (root / "scripts/start-compose.sh").write_text(START_STUB)
    (root / "scripts/start-compose.sh").chmod(0o700)
    return count


def probe(path: Path, mode: int) -> str:
    """Use a separate process to attempt a nonblocking private lock."""
    code = (
        "import fcntl,os,sys;"
        "fd=os.open(sys.argv[1],os.O_RDONLY);"
        "mode=int(sys.argv[2]);"
        "\ntry: fcntl.flock(fd,mode|fcntl.LOCK_NB); print('ACQUIRED')"
        "\nexcept BlockingIOError: print('BLOCKED')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), str(mode)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return result.stdout.strip()


def wait_for_start(root: Path, child: subprocess.Popen) -> None:
    """Wait until real snapshot and pending creation reach stack stub."""
    for _ in range(140):
        if (root / "start-stage").exists():
            return
        if child.poll() is not None:
            raise RuntimeError("UPDATER_EXITED_EARLY")
        time.sleep(0.05)
    raise RuntimeError("QA_START_STAGE_TIMEOUT")


def archived_rows(root: Path) -> int:
    """Check private snapshot checksum and archive SQLite integrity."""
    matches = list((root / "state/snapshots").glob("*/voltage-*.sqlite3.gz"))
    if len(matches) != 1:
        raise RuntimeError("SNAPSHOT_COUNT_INVALID")
    path = matches[0]
    checksum = Path(str(path) + ".sha256").read_text().split()[0]
    if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
        raise RuntimeError("SNAPSHOT_SHA_MISMATCH")
    output = root / "archive.sqlite3"
    with gzip.open(path, "rb") as source, output.open("wb") as target:
        shutil.copyfileobj(source, target)
    with closing(sqlite3.connect(output)) as db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        return db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]


def outcome(root: Path, mode: str, before: int, code: int) -> dict:
    """Reject lost rows, unexpected marker changes or real Docker calls."""
    with closing(sqlite3.connect(root / "data/current.sqlite3")) as db:
        after = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert after == before + (mode == "stale"), (before, after)
    pending = (root / "state/pending-update").exists()
    assert pending == (mode == "stale")
    events = (root / "events").read_text().splitlines()
    for stage in (
        "requirements_a_held",
        "SIGNATURE_MOCKED_OFFLINE",
        "SOURCE_FETCH_MOCKED_OFFLINE",
        "DOCKER_PULL_MOCKED_OFFLINE",
        "start_stack_pending_seen",
    ):
        assert stage in events, (stage, events)
    if mode == "clean":
        assert code == 0 and not pending
        assert (root / "state/deployed.json").is_file()
    else:
        assert code != 0 and pending
        assert (
            (root / "state/override.yaml").read_text().startswith("services:")
        )
    return {
        "case": mode,
        "before": before,
        "after": after,
        "snapshot_rows": archived_rows(root),
        "pending": pending,
        "exit_code": code,
        "events": events,
    }


def build_environment(root: Path, mode: str) -> dict:
    """Set only synthetic data, source and release paths for child Bash."""
    env = os.environ.copy()
    env.update(
        {
            "SVT_ROOT": str(root),
            "ROOT": str(root),
            "SVT_UPDATE_STATE": str(root / "state"),
            "DATABASE_PATH": str(root / "data/current.sqlite3"),
            "SVT_PUBLIC_STATE": str(root / "public"),
            "QA_DIGEST": DIGEST,
            "QA_VERSION": VERSION,
            "QA_EVENT_MODE": mode,
        }
    )
    return env


def execute_child(root: Path, mode: str, before: int) -> dict:
    """Hold maintenance B EX across the Bash child holding updater A EX."""
    lock = root / "maintenance-B.lock"
    lock.touch(mode=0o600, exist_ok=False)
    with lock.open("rb") as owner:
        fcntl.flock(owner, fcntl.LOCK_EX)
        child = subprocess.Popen(
            ["/bin/bash", "-c", BASH],
            env=build_environment(root, mode),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            wait_for_start(root, child)
            assert probe(lock, fcntl.LOCK_SH) == "BLOCKED"
            assert probe(root / "updater-A.lock", fcntl.LOCK_EX) == "BLOCKED"
            assert archived_rows(root) == before
            assert (root / "state/pending-update").exists()
            (root / "release-stage").write_text("continue\\n")
            stdout, stderr = child.communicate(timeout=30)
            print(
                "QA_PROCESS_EXIT",
                mode,
                child.returncode,
                stdout[-260:],
                stderr[-400:],
            )
            result = outcome(root, mode, before, child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    assert probe(lock, fcntl.LOCK_SH) == "ACQUIRED"
    assert probe(root / "updater-A.lock", fcntl.LOCK_EX) == "ACQUIRED"
    return result


def run_one(mode: str) -> dict:
    """Run one entire transaction against a disposable full SQLite copy."""
    if mode not in ("clean", "stale"):
        raise RuntimeError("INVALID_QA_SCENARIO")
    with tempfile.TemporaryDirectory(
        prefix="svt-full-updater-qa-",
        dir="/var/tmp",
    ) as folder:
        root = Path(folder)
        root.chmod(0o700)
        before = prepare(root)
        return execute_child(root, mode, before)


def main() -> int:
    """Inert unless explicitly invoked on verified offline LXC114."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY-RUN: no database, Docker, systemd or updater action")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact QA confirmation required")
        return 2
    try:
        guest_only()
        results = [run_one(mode) for mode in ("clean", "stale")]
    except (
        AssertionError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        print("QA_FULL_UPDATER_BLOCKED", type(exc).__name__, str(exc))
        return 3
    print(
        json.dumps(
            {
                "status": "PASS_ISOLATED_FULL_CANDIDATE_MAIN_PHASES",
                "cases": results,
                "real_signature_verification": "MOCKED_OFFLINE",
                "real_source_fetch": "MOCKED_OFFLINE",
                "real_compose_start": "MOCKED_OFFLINE",
                "production_modified": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
