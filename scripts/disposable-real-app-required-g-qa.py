#!/usr/bin/env python3
"""Replay real app.py required-g admission and shutdown on offline QA."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

EXISTING = Path(__file__).with_name("disposable-real-app-sigterm-qa.py")
CONFIRM = "OFFLINE_REAL_APP_REQUIRED_G_ADMISSION_QA_ONLY"


def load_existing() -> tuple[object, object]:
    """Load only the sibling fixed-host QA application/SIGTERM harness."""
    spec = importlib.util.spec_from_file_location("qa_g_app_base", EXISTING)
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_APPLICATION_HARNESS_MISSING")
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app, app.load_qa()


def prepare(root: Path, app: object, qa: object) -> None:
    """Stage only actual source plus existing invented offline QA mounts."""
    app.prepare(root, qa)
    lock = root / "locks/G"
    lock.touch(mode=0o640, exist_ok=False)
    os.chown(lock, 0, 10001)
    os.chmod(lock, 0o640)


def app_args(root: Path, name: str, app: object, qa: object) -> list[str]:
    """Select explicit required-g without modifying legacy harness modes."""
    args = app.docker_args(root, name, qa)
    before = "SHELLY_WRITER_FENCE=required"
    if args.count(before) != 1:
        raise RuntimeError("QA_SOURCE_ENV_MODE_MISSING")
    args[args.index(before)] = "SHELLY_WRITER_FENCE=required-g"
    return args


def probe_exclusive(path: Path) -> bool:
    """Test a separate actual host file description against one inode."""
    with path.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def denied_under_g_ex(root: Path, app: object, qa: object) -> None:
    """Prove direct app stderr rejects host G EX before creating SQLite."""
    name = "svt-qa-fence-g-denied-" + root.name[-8:]
    command = app_args(root, name, app, qa)
    command.remove("-d")
    command.insert(2, "--rm")
    with (root / "locks/G").open("rb") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        denied = qa.command(command, timeout=25)
        reason = denied.stdout + denied.stderr
        if denied.returncode == 0:
            raise RuntimeError("QA_G_DENIAL_EXIT_ZERO")
        if "owner_fence_already_held" not in reason:
            raise RuntimeError("QA_G_DENIAL_REASON_WRONG")
        if (root / "synthetic/qa.sqlite3").exists():
            raise RuntimeError("QA_G_DENIAL_SQLITE_CREATED")


def admitted_app(root: Path, app: object, qa: object) -> dict:
    """Prove real app releases G after B/W then drains during SIGTERM."""
    name = "svt-qa-fence-g-running-" + root.name[-8:]
    try:
        started = qa.command(app_args(root, name, app, qa), timeout=25)
        if started.returncode:
            raise RuntimeError("QA_REAL_APP_START_FAILED")
        app.wait_state(qa, name, True)
        await_sqlite(root)
        if not probe_exclusive(root / "locks/G"):
            raise RuntimeError("QA_G_SH_NOT_RELEASED")
        if probe_exclusive(root / "locks/B"):
            raise RuntimeError("QA_B_SH_NOT_HELD")
        if probe_exclusive(root / "locks/W"):
            raise RuntimeError("QA_W_EX_NOT_HELD")
        result = app.signal_and_verify(root, qa, name)
        if not probe_exclusive(root / "locks/G"):
            raise RuntimeError("QA_G_LOCK_NOT_RELEASED")
        result.update(
            {
                "classification": "PASS_REAL_APP_REQUIRED_G_ADMISSION",
                "G_EX_denied_pre_sqlite": True,
                "G_EX_available_while_B_W_held": True,
                "real_telegram": False,
            }
        )
        return result
    finally:
        qa.remove_owned(name)


def await_sqlite(root: Path) -> None:
    """Wait only for a new invented SQLite path after app startup."""
    deadline = time.monotonic() + 15
    path = root / "synthetic/qa.sqlite3"
    while not path.is_file():
        if time.monotonic() > deadline:
            raise RuntimeError("QA_SQLITE_NOT_CREATED")
        time.sleep(0.1)


def run_suite(root: Path, app: object, qa: object) -> dict:
    """Check admission denial first, then admit the current app and stop."""
    prepare(root, app, qa)
    denied_under_g_ex(root, app, qa)
    return admitted_app(root, app, qa)


def main() -> int:
    """Default inert; exact offline consent and verified QA guest required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    flags = parser.parse_args()
    if not flags.run_disposable:
        print("DRY_RUN: no Docker, source, SQLite or network operations")
        return 0
    if flags.confirm != CONFIRM:
        print("BLOCKED: exact isolated QA confirmation required")
        return 2
    try:
        app, qa = load_existing()
        qa.guest_guard()
        with tempfile.TemporaryDirectory(
            prefix="svt-qa-realapp-required-g-", dir="/var/tmp"
        ) as temp:
            evidence = run_suite(Path(temp), app, qa)
    except (OSError, RuntimeError, ValueError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:200])
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
