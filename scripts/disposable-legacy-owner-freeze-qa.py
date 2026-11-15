#!/usr/bin/env python3
"""Rehearse freezing an old disposable OCI before stopping its writer."""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import sqlite3
import tempfile
import time
from pathlib import Path

LEGACY = Path(__file__).with_name("disposable-legacy-oci-g-bypass-qa.py")
CONSENT = "OFFLINE_LEGACY_DOCKER_FREEZE_BEFORE_STOP_QA_ONLY"


def load_legacy() -> tuple[object, object]:
    """Reuse only the exact guarded offline legacy Docker QA module."""
    spec = importlib.util.spec_from_file_location("qa_old_freeze_base", LEGACY)
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_LEGACY_GUARD_NOT_FOUND")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.load_qa()


def inspect_owner(qa: object, name: str) -> tuple[bool, str, int]:
    """Read actual state, restart policy and count for owned QA Docker."""
    if not qa.owner_label(name):
        raise RuntimeError("QA_NOT_OUR_DISPOSABLE_CONTAINER")
    status = qa.command(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}|"
            "{{.RestartCount}}",
            name,
        ]
    )
    if status.returncode:
        raise RuntimeError("QA_DOCKER_INSPECT_FAILED")
    pieces = status.stdout.strip().split("|")
    if len(pieces) != 3 or pieces[0] not in {"true", "false"}:
        raise RuntimeError("QA_DOCKER_STATE_BAD_FORMAT")
    return pieces[0] == "true", pieces[1], int(pieces[2])


def restart_policy(qa: object, name: str, expected: str) -> None:
    """Set and verify only an exact labelled disposable restart policy."""
    if expected not in {"no", "unless-stopped"}:
        raise RuntimeError("QA_RESTART_POLICY_NOT_ALLOWED")
    if not qa.owner_label(name):
        raise RuntimeError("QA_NOT_OUR_DISPOSABLE_CONTAINER")
    update = qa.command(["docker", "update", "--restart=" + expected, name])
    if update.returncode:
        raise RuntimeError("QA_DOCKER_POLICY_UPDATE_FAILED")
    _, observed, _ = inspect_owner(qa, name)
    if observed != expected:
        raise RuntimeError("QA_POLICY_NOT_APPLIED")


def probe_exclusive(path: Path) -> bool:
    """Probe independent kernel exclusive lock without keeping it open."""
    with path.open("rb") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


def stop_owned(qa: object, name: str) -> None:
    """Signal only the exact QA container after verifying restart=no."""
    running, policy, _ = inspect_owner(qa, name)
    if not running or policy != "no":
        raise RuntimeError("QA_UNSAFE_STOP_WITH_RESTART")
    response = qa.command(["docker", "stop", "--time", "12", name], timeout=22)
    if response.returncode:
        raise RuntimeError("QA_LEGACY_DOCKER_STOP_FAILED")


def verify_stopped(qa: object, name: str, baseline: int) -> None:
    """Verify stopped state remains stable while restart policy is no."""
    for _ in range(10):
        running, policy, count = inspect_owner(qa, name)
        if running or policy != "no" or count != baseline:
            raise RuntimeError("QA_OLD_WRITER_RESTARTED")
        time.sleep(0.15)


def run_with_maintenance(
    root: Path, name: str, legacy: object, qa: object
) -> dict:
    """Freeze Docker restart first, then stop old writer under G EX."""
    started = qa.command(legacy.docker_args(root, name, qa), timeout=25)
    if started.returncode:
        raise RuntimeError("QA_OLD_OCI_START_FAILED")
    legacy.wait_for_schema(legacy.wait_for_database(root, name, qa))
    restart_policy(qa, name, "unless-stopped")
    with (root / "locks/G").open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not probe_exclusive(root / "locks/B"):
            raise RuntimeError("QA_LEGACY_UNEXPECTEDLY_OWNS_B")
        restart_policy(qa, name, "no")
        _, policy, before = inspect_owner(qa, name)
        if policy != "no":
            raise RuntimeError("QA_OLD_RESTART_FREEZE_NOT_CONFIRMED")
        stop_owned(qa, name)
        verify_stopped(qa, name, before)
        if not probe_exclusive(root / "locks/B"):
            raise RuntimeError("QA_B_EX_PROBE_FAILED")
        if not probe_exclusive(root / "locks/W"):
            raise RuntimeError("QA_W_EX_PROBE_FAILED")
    return {
        "classification": "PASS_ONE_DOCKER_OWNER_FREEZE_ONLY",
        "legacy_ignored_G_and_B": True,
        "old_policy_before_freeze": "unless-stopped",
        "policy_after_freeze": "no",
        "stop_after_freeze": True,
        "no_spontaneous_restart_after_stop": True,
        "all_other_owner_paths_frozen": False,
        "release_decision": "NO_GO",
        "network": "none",
        "telegram_enabled": False,
        "production_modified": False,
    }


def run(legacy: object, qa: object) -> dict:
    """Never keep one disposable legacy Docker object after this test."""
    with tempfile.TemporaryDirectory(
        prefix="svt-qa-old-owner-freeze-", dir="/var/tmp"
    ) as temp:
        root = Path(temp)
        legacy.prepare(root, qa)
        name = "svt-qa-fence-old-freeze-" + root.name[-8:]
        try:
            return run_with_maintenance(root, name, legacy, qa)
        finally:
            qa.remove_owned(name)


def main() -> int:
    """Default inert and exact loopback-only guest confirmation required."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    opts = parser.parse_args()
    if not opts.run_disposable:
        print("DRY_RUN: no Docker, source or SQLite mutations")
        return 0
    if opts.confirm != CONSENT:
        print("BLOCKED: exact offline LXC114 QA consent required")
        return 2
    try:
        legacy, qa = load_legacy()
        qa.guest_guard()
        result = run(legacy, qa)
    except (OSError, RuntimeError, sqlite3.Error) as error:
        print("QA_BLOCKED", type(error).__name__, str(error)[:230])
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
