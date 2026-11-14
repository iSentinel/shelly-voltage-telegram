#!/usr/bin/env python3
"""Private LXC114 B/A lock integration with candidate Bash recovery."""

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

ORIGINAL = Path("/var/lib/svt-qa-docker114/data/history.sqlite3")
CANDIDATE = Path("/root/svt-pr336-guard")
IMAGE = (
    "sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b"
)
TOKEN = "20261115T005600Z-0.5.21-hw"
CONFIRM = "ISOLATED_LXC114_BA_RECOVERY_ONLY"
EXPECTED = {
    "apply-update.sh": (
        "99bf0516f505d3c66949817c2f9a54f393377df41dc43a354c123e12f1e8e6ba"
    ),
    "pending-recovery-history-guard.py": (
        "8d6e97a975d26affcd4136b3846a018e44e2f002579c945fa62f9ff4d66d6f09"
    ),
}

BASH = r"""
set -Eeuo pipefail
exec 8>"$SVT_ROOT/qa-updater.lock"
flock -w 3 8 || exit 14
source "$SVT_ROOT/scripts/apply-update.sh"
docker() {
    [[ "$#" == 5 && "$1" == compose && "$2" == -f &&
        "$3" == "$ROOT/compose.yaml" && "$4" == stop &&
        "$5" == shelly-voltage ]] || return 92
    printf 'mocked_stop_only\n' > "$ROOT/stop-reached"
    for count in $(seq 1 120); do
        if [[ -f "$ROOT/release-stop" ]]; then break; fi
        sleep .1
    done
    [[ -f "$ROOT/release-stop" ]] || return 93
    if [[ "$QA_APPEND" == 1 ]]; then
        local code
        code='import app,time;from pathlib import Path;'
        code+='s=app.Storage(Path("/data/current.sqlite3"),"Europe/Kyiv");'
        code+='s.initialize();s.add(time.time(),221.7,50.0,"L1");'
        code+='print("SIGNED_OCI_QA_SAMPLE_COMMITTED")'
        /usr/bin/docker run --rm --pull never --network none --restart=no \
            --read-only --user 10001:10001 --cap-drop ALL \
            --security-opt no-new-privileges --memory 256m \
            --cpus 0.5 --pids-limit 64 \
            --mount "type=bind,src=$ROOT/data,dst=/data" \
            --entrypoint python "$QA_IMAGE" -c "$code"
    fi
}
recover_pending
"""


def check_guest() -> None:
    """Allow only the offline disposable host and immutable QA source."""
    if os.geteuid() != 0 or os.uname().nodename != "shelly-docker-reboot-qa":
        raise RuntimeError("NOT_DISPOSABLE_ROOT_GUEST")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("QA_GUEST_HAS_EXTERNAL_NETWORK")
    if ORIGINAL.is_symlink() or not ORIGINAL.is_file():
        raise RuntimeError("QA_HISTORY_MISSING_OR_LINKED")
    for name, digest in EXPECTED.items():
        path = CANDIDATE / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("QA_CANDIDATE_MISSING_OR_LINKED")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("QA_CANDIDATE_SHA_MISMATCH")
    for format_, expected in (
        ("{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}", "false|no"),
        ("{{.HostConfig.NetworkMode}}", "none"),
    ):
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                format_,
                "svt-qa-signed-writer-114",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        if result.stdout.strip() != expected:
            raise RuntimeError("QA_WRITER_NOT_SAFELY_STOPPED")


def make_fixture(root: Path) -> int:
    """Snapshot a test-only SQLite and stage isolated pending metadata."""
    snapshot = root / "state/snapshots" / TOKEN
    for folder in (root / "data", root / "scripts", snapshot, root / "public"):
        folder.mkdir(parents=True, exist_ok=True)
    source = root / "data/current.sqlite3"
    with (
        closing(
            sqlite3.connect(ORIGINAL.as_uri() + "?mode=ro", uri=True)
        ) as old,
        closing(sqlite3.connect(source)) as current,
    ):
        old.backup(current)
    with closing(
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    ) as db:
        rows = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    archive = snapshot / "voltage-qa.sqlite3.gz"
    with source.open("rb") as original, gzip.open(archive, "wb") as compressed:
        shutil.copyfileobj(original, compressed)
    (Path(str(archive) + ".sha256")).write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest()
        + "  "
        + str(archive)
        + "\n"
    )
    shutil.copyfile(
        CANDIDATE / "apply-update.sh", root / "scripts/apply-update.sh"
    )
    shutil.copyfile(
        CANDIDATE / "pending-recovery-history-guard.py",
        root / "scripts/pending-recovery-history-guard.py",
    )
    stage_fixture_metadata(root, snapshot, source)
    return rows


def stage_fixture_metadata(root: Path, snapshot: Path, source: Path) -> None:
    """Write private recovery markers and permit only QA Storage UID."""
    (root / "compose.yaml").write_text("name: synthetic-only\n")
    (root / "state/pending-update").write_text(TOKEN + "\n")
    (snapshot / "override.yaml").write_text("qa: previous\n")
    (root / "state/override.yaml").write_text("qa: candidate\n")
    (snapshot / "deployed.json").write_text('{"version":"qa-old"}\n')
    (root / "state/deployed.json").write_text('{"version":"qa-new"}\n')
    os.chmod(root, 0o711)
    os.chown(root / "data", 10001, 10001)
    os.chown(source, 10001, 10001)


def lock_probe(path: Path, flag: int) -> str:
    """Try an independent nonblocking advisory lock in another process."""
    code = (
        "import os,fcntl,sys;"
        "fd=os.open(sys.argv[1],os.O_RDONLY|os.O_CLOEXEC);"
        "mode=int(sys.argv[2]);"
        "\ntry: fcntl.flock(fd,mode|fcntl.LOCK_NB); print('ACQUIRED')"
        "\nexcept BlockingIOError: print('BLOCKED')"
    )
    run = subprocess.run(
        [sys.executable, "-c", code, str(path), str(flag)],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return run.stdout.strip()


def await_stop(root: Path, process: subprocess.Popen) -> None:
    """Wait for exact intercepted Compose stop or fail boundedly."""
    for _ in range(110):
        if (root / "stop-reached").exists():
            return
        if process.poll() is not None:
            raise RuntimeError("BASH_RECOVERY_EXITED_BEFORE_STOP")
        time.sleep(0.06)
    raise RuntimeError("BASH_RECOVERY_STOP_TIMEOUT")


def check_result(root: Path, before: int, mode: str, exit_code: int) -> dict:
    """Assert complete logical history and pending result for each case."""
    with closing(
        sqlite3.connect(
            (root / "data/current.sqlite3").as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        rows = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
        latest = db.execute(
            "SELECT voltage FROM measurements ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    pending = (root / "state/pending-update").exists()
    assert rows == before + (mode == "append")
    assert pending == (mode == "append")
    assert exit_code == (1 if mode == "append" else 0), exit_code
    if mode == "append":
        assert abs(latest - 221.7) < 0.0001
        assert (root / "state/override.yaml").read_text() == "qa: candidate\n"
    else:
        assert (root / "state/override.yaml").read_text() == "qa: previous\n"
        assert (root / "public/deployed.json").exists()
    return {
        "case": mode,
        "rows_before": before,
        "rows_after": rows,
        "pending": pending,
        "recover_exit": exit_code,
    }


def recovery_env(root: Path, mode: str) -> dict:
    """Build environment containing only synthetic recovery target paths."""
    env = os.environ.copy()
    env.update(
        {
            "SVT_ROOT": str(root),
            "SVT_UPDATE_STATE": str(root / "state"),
            "DATABASE_PATH": str(root / "data/current.sqlite3"),
            "SVT_PUBLIC_STATE": str(root / "public"),
            "QA_APPEND": "1" if mode == "append" else "0",
            "QA_IMAGE": IMAGE,
        }
    )
    return env


def run_locked_recovery(root: Path, before: int, mode: str) -> dict:
    """Hold B EX across candidate Bash recovery holding A EX."""
    b = root / "qa-maintenance.lock"
    a = root / "qa-updater.lock"
    b.touch(mode=0o600, exist_ok=False)
    descriptor = os.open(b, os.O_RDONLY | os.O_CLOEXEC)
    process = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        process = subprocess.Popen(
            ["/bin/bash", "-c", BASH],
            env=recovery_env(root, mode),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        await_stop(root, process)
        assert lock_probe(b, fcntl.LOCK_SH) == "BLOCKED"
        assert lock_probe(a, fcntl.LOCK_EX) == "BLOCKED"
        (root / "release-stop").write_text("continue\\n")
        stdout, stderr = process.communicate(timeout=25)
        evidence = check_result(root, before, mode, process.returncode)
        evidence.update(
            {
                "updater_maintenance_exclusive": True,
                "updater_mutex_exclusive": True,
                "supervisor_shared_blocked_during_recovery": True,
            }
        )
        print("QA_BASH_RECOVERY_EVENT", mode, stdout[-230:], stderr[-180:])
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(descriptor)
    assert lock_probe(b, fcntl.LOCK_SH) == "ACQUIRED"
    assert lock_probe(a, fcntl.LOCK_EX) == "ACQUIRED"
    return evidence


def exercise(mode: str) -> dict:
    """Run one complete private fixture and clean it on return."""
    if mode not in {"clean", "append"}:
        raise RuntimeError("INVALID_QA_SCENARIO")
    with tempfile.TemporaryDirectory(
        prefix="svt-ba-recovery-qa-", dir="/var/tmp"
    ) as name:
        root = Path(name)
        root.chmod(0o700)
        before = make_fixture(root)
        return run_locked_recovery(root, before, mode)


def main() -> int:
    """Require explicit fixed LXC114 consent; otherwise remain inert."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY-RUN PASS: no Docker, SQLite, or lock actions")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact isolated LXC114 confirmation required")
        return 2
    try:
        check_guest()
        reports = [exercise(mode) for mode in ("clean", "append")]
    except (
        AssertionError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as error:
        print("QA_RECOVERY_BLOCKED", type(error).__name__, str(error))
        return 3
    result = {
        "status": "QA_TWO_LOCK_BASH_RECOVERY_PASS",
        "cases": reports,
        "production_modified": False,
        "real_docker_writer_restarted": False,
        "telegram_contacted": False,
        "qa_scratch_deleted": True,
        "owner_maintenance_authorized": False,
    }
    print(json.dumps(result) if args.jsonl else result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
