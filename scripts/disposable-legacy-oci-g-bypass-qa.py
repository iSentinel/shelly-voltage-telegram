#!/usr/bin/env python3
"""Show that an old unfenced OCI can write under host maintenance G EX."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

GUARD = Path(__file__).with_name("disposable-writer-fence-container-qa.py")
CONFIRM = "OFFLINE_LEGACY_OCI_G_BYPASS_QA_ONLY"


def load_qa() -> object:
    """Reuse the exact fixed-host LXC114 original-writer safety guard."""
    spec = importlib.util.spec_from_file_location(
        "legacy_G_bypass_qa_guard", GUARD
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("LEGACY_QA_GUARD_NOT_AVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(root: Path, qa: object) -> None:
    """Create root-owned G next to invented B/W outside production paths."""
    qa.prepare(root)
    gate = root / "locks/G"
    gate.touch(mode=0o640, exist_ok=False)
    os.chown(gate, 0, 10001)
    os.chmod(gate, 0o640)


def legacy_env() -> dict[str, str]:
    """Disable real network and Telegram and use only a fake SQLite path."""
    return {
        "DATABASE_PATH": "/synthetic/legacy.sqlite3",
        "SHELLY_HOST": "127.0.0.1",
        "SHELLY_METER_TYPE": "pro_em50",
        "SHELLY_TIMEOUT_SEC": "1",
        "POLL_INTERVAL_SEC": "1",
        "HEALTH_HOST": "127.0.0.1",
        "HEALTH_PORT": "8080",
        "TELEGRAM_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "SHELLY_WRITER_FENCE": "",
    }


def docker_args(root: Path, name: str, qa: object) -> list[str]:
    """Run exact cached old OCI with nonroot offline disposable flags."""
    args = qa.base_flags(name) + qa.sandbox_mounts(root)
    for key, value in legacy_env().items():
        args.extend(["--env", key + "=" + value])
    return args + [
        qa.IMAGE,
        "python",
        "/app/app.py",
        "run",
    ]


def wait_for_database(root: Path, name: str, qa: object) -> Path:
    """Wait only for an invented SQLite file while maintenance G is held."""
    db = root / "synthetic/legacy.sqlite3"
    deadline = time.monotonic() + 13
    while time.monotonic() < deadline:
        if db.is_file():
            return db
        time.sleep(0.15)
    state = qa.command(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}|{{.State.ExitCode}}",
            name,
        ]
    )
    raise RuntimeError(
        "LEGACY_SQLITE_NOT_OBSERVED_" + state.stdout.strip()[:50]
    )


def read_database(db: Path) -> int:
    """Validate only invented SQLite created while maintenance G EX held."""
    with closing(sqlite3.connect(db)) as connection:
        status = connection.execute("PRAGMA quick_check").fetchone()[0]
        count = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    if status != "ok" or count < 2:
        raise RuntimeError("LEGACY_QA_SQLITE_NOT_VALID")
    return count


def wait_for_schema(db: Path) -> int:
    """Wait for committed invented SQLite schema, not just an empty file."""
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        try:
            return read_database(db)
        except (sqlite3.Error, RuntimeError):
            time.sleep(0.15)
    raise RuntimeError("LEGACY_QA_COMMITTED_SCHEMA_NOT_OBSERVED")


def proof(root: Path, name: str, qa: object) -> dict:
    """Classify old OCI bypass as NO_GO, not as a successful release."""
    with (root / "locks/G").open("rb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            started = qa.command(docker_args(root, name, qa), timeout=25)
            if started.returncode:
                raise RuntimeError("LEGACY_QA_DOCKER_START_FAILED")
            db = wait_for_database(root, name, qa)
            tables = wait_for_schema(db)
        finally:
            qa.remove_owned(name)
    return {
        "classification": "NEGATIVE_LEGACY_OCI_WRITES_DURING_G_EX",
        "host_G_EX_held_during_old_app_SQLite_create": True,
        "sqlite_tables": tables,
        "sqlite_quick_check": "ok",
        "network": "none",
        "telegram_enabled": False,
        "production_modified": False,
        "release_decision": "NO_GO",
    }


def run_suite(qa: object) -> dict:
    """Isolate all invented data and exact owner-labelled Docker objects."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-legacy-g-bypass-", dir="/var/tmp"
    ) as temporary:
        root = Path(temporary)
        prepare(root, qa)
        name = "svt-qa-fence-legacy-g-" + root.name[-8:]
        return proof(root, name, qa)


def main() -> int:
    """Inert by default; require explicit isolated LXC114 QA consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no Docker, SQLite or lock changes")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact offline QA consent is required")
        return 2
    try:
        qa = load_qa()
        qa.guest_guard()
        result = run_suite(qa)
    except (OSError, RuntimeError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:220])
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
