#!/usr/bin/env python3
"""Rehearse real app required-g using isolated systemd and Docker."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

APP_QA = Path(__file__).with_name("disposable-real-app-required-g-qa.py")
CONFIRM = "OFFLINE_SYSTEMD_REAL_APP_REQUIRED_G_QA_ONLY"


def load_qa() -> tuple[object, object, object]:
    """Load only the sibling fixed offline LXC114 real app QA helpers."""
    spec = importlib.util.spec_from_file_location("systemd_g_app_base", APP_QA)
    if spec is None or spec.loader is None:
        raise RuntimeError("SYSTEMD_G_QA_SOURCE_MISSING")
    test = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(test)
    app, qa = test.load_existing()
    return test, app, qa


def unit_names(root: Path) -> dict[str, str]:
    """Give each transient unit an exact private unique owner prefix."""
    stem = str(os.getpid()) + "-" + root.name[-8:]
    return {
        role: "svt-qa-systemd-g-" + stem + "-" + role
        for role in ("deny", "allow")
    }


def docker_names(root: Path) -> dict[str, str]:
    """Name only disposal-labelled offline containers."""
    return {
        role: "svt-qa-fence-sys-g-" + role + "-" + root.name[-8:]
        for role in ("deny", "allow")
    }


def launch(
    test: object,
    app: object,
    qa: object,
    root: Path,
    unit: str,
    container: str,
) -> subprocess.CompletedProcess:
    """Invoke a restricted real transient systemd G SH Docker start."""
    args = test.app_args(root, container, app, qa)
    command = [
        "systemd-run",
        "--unit=" + unit,
        "--wait",
        "--pipe",
        "--collect",
        "-p",
        "Type=oneshot",
        "-p",
        "PrivateNetwork=yes",
        "-p",
        "ProtectSystem=strict",
        "-p",
        "ReadWritePaths=" + str(root),
        "/usr/bin/flock",
        "--shared",
        "--nonblock",
        str(root / "locks/G"),
        "/usr/bin/docker",
        *args[1:],
    ]
    return subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=45
    )


def negative_case(
    test: object,
    app: object,
    qa: object,
    root: Path,
    unit: str,
    container: str,
) -> None:
    """A host G EX must prevent systemd from creating any Docker writer."""
    import fcntl

    with (root / "locks/G").open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        denied = launch(test, app, qa, root, unit, container)
        if denied.returncode == 0:
            raise RuntimeError("QA_SYSTEMD_G_EX_START_NOT_DENIED")
        found = qa.command(["docker", "inspect", container])
        if found.returncode == 0:
            raise RuntimeError("QA_CONTAINER_CREATED_DURING_G_EX")
        if (root / "synthetic/qa.sqlite3").exists():
            raise RuntimeError("QA_SQLITE_CREATED_DURING_G_EX")


def ensure_admission(test: object, root: Path) -> None:
    """Verify the actual running app released G while retaining B/W."""
    if not test.probe_exclusive(root / "locks/G"):
        raise RuntimeError("QA_G_NOT_RELEASED_AFTER_START")
    for role in ("B", "W"):
        if test.probe_exclusive(root / "locks" / role):
            raise RuntimeError("QA_WRITER_LIFETIME_LOCK_NOT_HELD")


def terminate(app: object, qa: object, container: str) -> None:
    """Stop only the exact labelled disposable app with real SIGTERM."""
    if not qa.owner_label(container):
        raise RuntimeError("QA_SYSTEMD_CONTAINER_NOT_OWNER_LABELLED")
    stopped = qa.command(
        [
            "docker",
            "kill",
            "--signal=SIGTERM",
            container,
        ]
    )
    if stopped.returncode:
        raise RuntimeError("QA_SYSTEMD_WRITER_SIGTERM_FAILED")
    app.wait_state(qa, container, False)
    status = qa.command(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.ExitCode}}",
            container,
        ]
    )
    if status.returncode or status.stdout.strip() != "0":
        raise RuntimeError("QA_SYSTEMD_WRITER_EXIT_NOT_ZERO")


def validate_final(test: object, root: Path) -> str:
    """All G/B/W must be free after app exit and SQLite must be sound."""
    if any(
        not test.probe_exclusive(root / "locks" / name)
        for name in ("G", "B", "W")
    ):
        raise RuntimeError("QA_SYSTEMD_OWNER_LOCK_NOT_RELEASED")
    with closing(sqlite3.connect(root / "synthetic/qa.sqlite3")) as db:
        result = db.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError("QA_SYSTEMD_SYNTHETIC_SQLITE_BAD")
    return result


def positive_case(
    test: object,
    app: object,
    qa: object,
    root: Path,
    unit: str,
    container: str,
) -> dict:
    """Real systemd may start a cooperating app only after G is free."""
    allowed = launch(test, app, qa, root, unit, container)
    if allowed.returncode:
        raise RuntimeError("QA_SYSTEMD_G_FREE_START_FAILED")
    app.wait_state(qa, container, True)
    test.await_sqlite(root)
    ensure_admission(test, root)
    terminate(app, qa, container)
    quick = validate_final(test, root)
    return {
        "result": "PASS_REAL_SYSTEMD_REQUIRED_G_APP",
        "G_EX_denied_systemd_before_Docker": True,
        "G_free_admitted_real_app": True,
        "app_G_released_while_BW_held": True,
        "real_docker_SIGTERM_exit": 0,
        "synthetic_sqlite_quick_check": quick,
        "production_changed": False,
    }


def run_suite(test: object, app: object, qa: object) -> dict:
    """Execute both cases under only one unique invented QA root."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-systemd-required-g-", dir="/var/tmp"
    ) as folder:
        root = Path(folder)
        test.prepare(root, app, qa)
        units = unit_names(root)
        containers = docker_names(root)
        try:
            negative_case(
                test, app, qa, root, units["deny"], containers["deny"]
            )
            return positive_case(
                test, app, qa, root, units["allow"], containers["allow"]
            )
        finally:
            for name in containers.values():
                qa.remove_owned(name)


def main() -> int:
    """Default inert and exact private LXC114 consent are required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no systemd unit, Docker or SQLite changed")
        return 0
    if args.confirm != CONFIRM:
        print("BLOCKED: exact offline QA consent required")
        return 2
    try:
        test, app, qa = load_qa()
        qa.guest_guard()
        evidence = run_suite(test, app, qa)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:230])
        return 3
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
