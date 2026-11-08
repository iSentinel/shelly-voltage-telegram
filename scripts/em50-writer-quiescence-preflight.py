"""Read-only predeployment writer-quiescence prerequisite for EM-50."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_parity_guard():
    """Reuse the complete SQLite history checker from release preflight."""

    path = ROOT / "scripts/em50-predeploy-backup-guard.py"
    spec = importlib.util.spec_from_file_location("em50_backup_guard", path)
    if spec is None or spec.loader is None:
        raise ValueError("SQLite parity checker not available")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def container_running(name: str) -> bool:
    """Inspect Docker state without environment variables or secrets."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("container name invalid")
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", name],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Docker unavailable") from None
    if result.returncode != 0 or len(result.stdout) > 32768:
        raise ValueError("Docker state unavailable")
    state = json.loads(result.stdout)
    if not isinstance(state, dict) or type(state.get("Running")) is not bool:
        raise ValueError("Docker state malformed")
    if state.get("Restarting") or state.get("Dead"):
        raise ValueError("Docker state unstable")
    return state["Running"]


def snapshot_state(path: Path) -> dict:
    """Read all measurements/outages and private status only in memory."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("SQLite source missing or linked")
    guard = load_parity_guard()
    return guard.sqlite_state(path)


def unchanged(before: dict, after: dict) -> bool:
    """Reject even a historical edit that does not change row count."""

    return all(
        (
            before["measurements_digest"] == after["measurements_digest"],
            before["outages_digest"] == after["outages_digest"],
            before["rows"] == after["rows"],
            before["last"] == after["last"],
            before["outage"] == after["outage"],
            bool(before["status"]),
            before["status"] == after["status"],
        )
    )


def check_window(path: Path, container: str, seconds: int) -> str:
    """Require a stopped writer and unchanged SQLite across a time window."""

    if not 1 <= seconds <= 60:
        raise ValueError("observation period must be 1 to 60 seconds")
    if container_running(container):
        raise ValueError("Docker producer is still running")
    before = snapshot_state(path)
    time.sleep(seconds)
    if container_running(container):
        raise ValueError("Docker producer restarted during observation")
    after = snapshot_state(path)
    if not unchanged(before, after):
        raise ValueError("live SQLite changed during observation")
    return "producer_stopped=true history_stable=true release_authorized=false"


def main() -> int:
    """Report a prerequisite; never start, stop or approve a deployment."""

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--container", default="shelly-voltage-bot")
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--observe-seconds", type=int, default=15)
    args = parser.parse_args()
    if args.dry_run:
        print("PASS DRY_RUN no_docker_actions=true no_writes=true")
        return 0
    try:
        print(
            "PASS WRITER_QUIESCENCE",
            check_window(Path(args.db), args.container, args.observe_seconds),
        )
        return 0
    except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
        print("FAIL WRITER_QUIESCENCE prerequisite_not_met", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
