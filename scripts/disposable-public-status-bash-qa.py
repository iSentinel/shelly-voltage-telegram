#!/usr/bin/env python3
"""Run isolated source-only Bash metadata transaction failure checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

ORIGINAL = Path("/root/svt-pr336-guard/apply-update.sh")
CANDIDATE = Path("/root/svt-issue356-qa/apply-update.sh")
BASE_SHA = "99bf0516f505d3c66949817c2f9a54f393377df41dc43a354c123e12f1e8e6ba"
CANDIDATE_SHA = (
    "b53f8436b3aef750fe08a83f8b5254e97b26faa784282350a95a996f975b8d6c"
)
CONFIRM = "ISOLATED_REAL_BASH_PUBLIC_STATUS_QA_ONLY"
DIGEST = "sha256:" + "b" * 64
OLD = {"version": "0.5.20-hw", "digest": "sha256:" + "a" * 64}
NEW = {"version": "0.5.21-hw", "digest": DIGEST}
EXIT = {
    "success": 0,
    "publish_fail": 71,
    "public_fsync_fail": 71,
    "pending_rm_fail": 72,
    "pending_fsync_fail": 73,
}

BASH = r"""
set -Eeuo pipefail
source "$SVT_ROOT/scripts/apply-update.sh"
requirements() { mkdir -p "$STATE/snapshots"; }
preflight() { :; }
storage_preflight() { :; }
verify_image() { :; }
verify_source_archive() { :; }
wait_healthy() { :; }
start_stack() { :; }
snapshot() {
    SNAPSHOT="$STATE/snapshots/20261115T024500Z-0.5.21-hw"
    mkdir -p "$SNAPSHOT"
}
docker() {
    [[ "$#" == 2 && "$1" == pull && "$2" == "$IMAGE@$QA_DIGEST" ]]
}
python3() {
    if [[ "$1" == "$ROOT/scripts/github_update.py" ]]; then
        case "$2" in
            guard) return 0 ;;
            digest) printf '%s\n' "$QA_DIGEST"; return 0 ;;
            *) return 90 ;;
        esac
    fi
    /usr/bin/python3 "$@"
}
install() {
    [[ "$QA_MODE" != publish_fail ]] || return 79
    /usr/bin/install "$@"
}
rm() {
    if [[ "$QA_MODE" == pending_rm_fail && "$#" == 2
       && "$1" == -f && "$2" == "$PENDING_FILE" ]]; then
        return 85
    fi
    /usr/bin/rm "$@"
}
QA_SYNC_STATE_COUNT=0
sync() {
    if [[ "$QA_MODE" == public_fsync_fail
       && "$#" == 2 && "$2" == "$PUBLIC_STATE" ]]; then
        return 86
    fi
    if [[ "$QA_MODE" == pending_fsync_fail
       && "$#" == 2 && "$2" == "$STATE" ]]; then
        ((QA_SYNC_STATE_COUNT += 1))
        [[ "$QA_SYNC_STATE_COUNT" -lt 2 ]] || return 87
    fi
    /usr/bin/sync "$@"
}
main --version "$QA_VERSION"
"""


def guard() -> None:
    """Require source pins and an exact offline stopped-writer QA guest."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise RuntimeError("DISPOSABLE_GUEST_ONLY")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("EXTERNAL_NETWORK_PRESENT")
    for path, digest in ((ORIGINAL, BASE_SHA), (CANDIDATE, CANDIDATE_SHA)):
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("PINNED_SOURCE_ABSENT")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError("SOURCE_SHA_MISMATCH")
    command = [
        "docker",
        "inspect",
        "--format",
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
        "svt-qa-signed-writer-114",
    ]
    proc = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=5
    )
    if proc.stdout.strip() != "false|no":
        raise RuntimeError("QA_WRITER_RUNNING")
    for unit in ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer"):
        proc = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.stdout.strip() != "disabled":
            raise RuntimeError("QA_OWNER_ENABLED")


def make_fixture(root: Path) -> Path:
    """Create only synthetic settings, SQLite and the candidate script."""
    for name in ("state", "public", "data", "scripts"):
        (root / name).mkdir(mode=0o700)
    shutil.copyfile(CANDIDATE, root / "scripts/apply-update.sh")
    (root / "compose.yaml").write_text("services: {}\n")
    (root / "state/deployed.json").write_text(json.dumps(OLD))
    (root / "public/deployed.json").write_text(json.dumps(OLD))
    (root / "public/deployed.json").chmod(0o644)
    database = root / "data/synthetic.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE readings(id INTEGER, voltage REAL)")
        db.execute("INSERT INTO readings VALUES(1, 220.5)")
        db.execute("INSERT INTO readings VALUES(2, 225.1)")
    return database


def environment(root: Path, mode: str, database: Path) -> dict:
    """Pass only temporary paths and invented image metadata."""
    env = os.environ.copy()
    env.update(
        {
            "SVT_ROOT": str(root),
            "SVT_UPDATE_STATE": str(root / "state"),
            "SVT_PUBLIC_STATE": str(root / "public"),
            "DATABASE_PATH": str(database),
            "QA_DIGEST": DIGEST,
            "QA_VERSION": NEW["version"],
            "QA_MODE": mode,
        }
    )
    return env


def assert_outcome(
    root: Path, database: Path, mode: str, proc: object
) -> dict:
    """Check metadata ordering and unchanged synthetic SQLite history."""
    assert proc.returncode == EXIT[mode], (mode, proc.returncode, proc.stderr)
    pending = (root / "state/pending-update").exists()
    private = json.loads((root / "state/deployed.json").read_text())
    public_path = root / "public/deployed.json"
    public = json.loads(public_path.read_text())
    expected_public = OLD if mode == "publish_fail" else NEW
    if mode == "success":
        assert not pending and "UPDATE PASS:" in proc.stdout
    else:
        assert "UPDATE PASS:" not in proc.stdout
        assert pending == (mode != "pending_fsync_fail")
    assert private == NEW and public == expected_public
    assert stat.S_IMODE(public_path.stat().st_mode) == 0o644
    assert not list((root / "public").glob(".deployed-*"))
    with closing(sqlite3.connect(database)) as db, db:
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2
    return {
        "case": mode,
        "exit": proc.returncode,
        "public": public["version"],
        "private": private["version"],
        "pending": pending,
        "sqlite_rows": 2,
    }


def run_case(mode: str) -> dict:
    """Execute actual candidate main with all outside dependencies mocked."""
    with tempfile.TemporaryDirectory(
        prefix="svt-issue356-bash-qa-", dir="/var/tmp"
    ) as name:
        root = Path(name)
        root.chmod(0o700)
        database = make_fixture(root)
        proc = subprocess.run(
            ["/bin/bash", "-c", BASH],
            env=environment(root, mode, database),
            capture_output=True,
            text=True,
            check=False,
            timeout=35,
        )
        return assert_outcome(root, database, mode, proc)


def main() -> int:
    """Stay inert unless exact consent is given on the offline test guest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY_RUN: no databases, releases or containers touched")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: exact confirmation required")
        return 2
    try:
        guard()
        cases = [run_case(mode) for mode in EXIT]
    except (
        AssertionError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:360])
        return 3
    print(
        json.dumps(
            {
                "result": "PASS_REAL_CANDIDATE_BASH_METADATA_QA",
                "cases": cases,
                "actual_signature": "MOCKED",
                "real_docker_start": "MOCKED",
                "telegram": "NOT_USED",
                "production_modified": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
