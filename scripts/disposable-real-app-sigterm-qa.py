#!/usr/bin/env python3
"""Rehearse real candidate app.py SIGTERM under isolated Docker and SQLite."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

GUARD = Path(__file__).with_name("disposable-writer-fence-container-qa.py")
APP = Path(__file__).resolve().parents[1] / "app.py"
CONFIRM = "OFFLINE_REAL_APP_SHUTDOWN_SIGTERM_QA_ONLY"


def load_qa() -> object:
    """Import only our neighbouring fixed-host offline sandbox guard."""
    spec = importlib.util.spec_from_file_location(
        "qa_app_sigterm_guard", GUARD
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_GUARD_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(root: Path, qa: object) -> None:
    """Copy only candidate code into an invented private QA tree."""
    qa.prepare(root)
    app = root / "code" / "app.py"
    shutil.copyfile(APP, app)
    os.chown(app, 0, 10001)
    os.chmod(app, 0o640)
    version = qa.command(
        [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--entrypoint",
            "cat",
            qa.IMAGE,
            "/app/VERSION",
        ]
    )
    if version.returncode:
        raise RuntimeError("QA_CACHED_IMAGE_VERSION_UNAVAILABLE")
    path = root / "code" / "VERSION"
    path.write_text(version.stdout)
    os.chown(path, 0, 10001)
    os.chmod(path, 0o640)


def docker_args(root: Path, name: str, qa: object) -> list[str]:
    """Start only candidate source with no network or Telegram credentials."""
    flags = qa.base_flags(name)
    mounts = []
    for source, target in (
        ("code", "/qa-code"),
        ("locks", "/run/shelly-owner"),
        ("synthetic", "/synthetic"),
    ):
        mount = "type=bind,src=" + str(root / source) + ",dst=" + target
        mounts.extend(
            ["--mount", mount + ("" if source == "synthetic" else ",readonly")]
        )
    return (
        flags
        + mounts
        + docker_env()
        + [
            "--entrypoint",
            "python",
            qa.IMAGE,
            "/qa-code/app.py",
            "run",
        ]
    )


def docker_env() -> list[str]:
    """Use an invented meter, no internet and no Telegram secrets."""
    env = {
        "PYTHONPATH": "/app",
        "SHELLY_WRITER_FENCE": "required",
        "DATABASE_PATH": "/synthetic/qa.sqlite3",
        "SHELLY_HOST": "127.0.0.1",
        "SHELLY_METER_TYPE": "pro_em50",
        "SHELLY_TIMEOUT_SEC": "1",
        "POLL_INTERVAL_SEC": "1",
        "HEALTH_HOST": "127.0.0.1",
        "HEALTH_PORT": "8080",
        "TELEGRAM_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
    }
    result = []
    for name, value in env.items():
        result.extend(["--env", name + "=" + value])
    return result


def probe_exclusive(path: Path) -> bool:
    """Verify independent host B/W lock contention and release."""
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def wait_state(qa: object, name: str, running: bool) -> None:
    """Bound waiting on only the exact disposable container state."""
    until = time.monotonic() + (15 if running else 25)
    while time.monotonic() < until:
        status = qa.command(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
                name,
            ]
        )
        if status.returncode:
            raise RuntimeError("QA_DOCKER_INSPECT_FAILED")
        actual, policy = status.stdout.strip().split("|")
        if policy != "no":
            raise RuntimeError("QA_AUTORESTART_POLICY_CHANGED")
        if (actual == "true") is running:
            return
        time.sleep(0.1)
    raise RuntimeError("QA_WRITER_STATE_TIMEOUT")


def sql_integrity(root: Path) -> tuple[str, int]:
    """Validate only invented SQLite after its container is stopped."""
    path = root / "synthetic" / "qa.sqlite3"
    if not path.is_file():
        raise RuntimeError("QA_SQLITE_NOT_CREATED")
    with closing(sqlite3.connect(path)) as database:
        check = database.execute("PRAGMA quick_check").fetchone()[0]
        tables = database.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    if check != "ok" or tables < 2:
        raise RuntimeError("QA_SQLITE_QUICK_CHECK_FAILED")
    return check, tables


def start_and_check(root: Path, qa: object, name: str) -> None:
    """Start the actual candidate app with invented data and held B/W."""
    started = qa.command(docker_args(root, name, qa), timeout=25)
    if started.returncode:
        raise RuntimeError("QA_CONTAINER_START_FAILED")
    wait_state(qa, name, True)
    deadline = time.monotonic() + 15
    while not (root / "synthetic" / "qa.sqlite3").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("QA_DB_START_TIMEOUT")
        time.sleep(0.1)
    if probe_exclusive(root / "locks/B") or probe_exclusive(root / "locks/W"):
        raise RuntimeError("QA_FENCE_NOT_HELD_WHILE_RUNNING")


def signal_and_verify(root: Path, qa: object, name: str) -> dict:
    """Require graceful exit, both released locks and valid invented SQLite."""
    stop = qa.command(["docker", "kill", "--signal=SIGTERM", name])
    if stop.returncode:
        raise RuntimeError("QA_SIGTERM_FAILED")
    wait_state(qa, name, False)
    status = qa.command(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.ExitCode}}",
            name,
        ]
    )
    check, tables = sql_integrity(root)
    if status.stdout.strip() != "0":
        raise RuntimeError("QA_APP_EXIT_NONZERO")
    if not probe_exclusive(root / "locks/B") or not probe_exclusive(
        root / "locks/W"
    ):
        raise RuntimeError("QA_FENCE_STUCK_AFTER_APP_EXIT")
    return {
        "result": "PASS_REAL_APP_SIGTERM_QA",
        "sqlite_quick_check": check,
        "sqlite_tables": tables,
        "exit_code": 0,
        "B_W_held_during_run": True,
        "B_W_released_after_exit": True,
        "real_telegram": False,
        "production_changed": False,
    }


def run_case(root: Path, qa: object) -> dict:
    """Execute only an owner-labelled disposable Docker lifecycle."""
    name = "svt-qa-fence-sigterm-" + root.name[-8:]
    try:
        start_and_check(root, qa, name)
        return signal_and_verify(root, qa, name)
    finally:
        qa.remove_owned(name)


def main() -> int:
    """Default inert; exact offline consent required for real Docker."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no Docker, database or external service changes")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact isolated QA consent is required")
        return 2
    try:
        qa = load_qa()
        qa.guest_guard()
        with tempfile.TemporaryDirectory(
            prefix="svt-qa-fence-sigterm-", dir="/var/tmp"
        ) as name:
            root = Path(name)
            prepare(root, qa)
            evidence = run_case(root, qa)
    except (OSError, RuntimeError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:220])
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
