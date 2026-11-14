#!/usr/bin/env python3
"""Guarded restart of only a disposable networkless QA Storage writer."""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

ROOT = Path("/var/lib/svt-qa-docker114")
NAME = "svt-qa-signed-writer-114"
UNIT = "svt-qa-compose.service"
IMAGE = (
    "sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b"
)
CONFIRM = "DISPOSABLE_QA114_GUARDED_RESTART_ONLY"


class Blocked(Exception):
    """The disposable target failed a critical restart safety check."""


def command(args: list[str]) -> str:
    """Run a fixed argument-only process with bounded output."""
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Blocked("QA_COMMAND_FAILED") from exc
    if result.returncode or len(result.stdout) > 65536:
        raise Blocked("QA_COMMAND_REJECTED")
    return result.stdout.strip()


def inspect(format_: str) -> str:
    """Read the explicitly named synthetic writer, not any live bot."""
    return command(["docker", "inspect", "--format", format_, NAME])


def check_sqlite() -> int:
    """Verify the existing synthetic database in read-only mode."""
    path = ROOT / "data/history.sqlite3"
    if path.is_symlink() or not path.is_file():
        raise Blocked("SYNTHETIC_SQLITE_MISSING")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("SYNTHETIC_SQLITE_INVALID")
        row = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        if row != ("3",):
            raise Blocked("SYNTHETIC_SCHEMA_MISMATCH")
        return db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]


def check_qa_systemd_owner() -> None:
    """Require the exact QA recovery guard and Compose restart unit."""
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise Blocked("QA_GUEST_NETWORK_NOT_ISOLATED")
    if command(["systemctl", "is-enabled", UNIT]) != "enabled":
        raise Blocked("QA_SYSTEMD_OWNER_NOT_ENABLED")
    data = command(
        [
            "systemctl",
            "show",
            UNIT,
            "-p",
            "ExecStartPre",
            "-p",
            "ExecStart",
            "--no-pager",
        ]
    )
    if (
        str(ROOT / "prestart.py") not in data
        or str(ROOT / "compose.yaml") not in data
        or "/usr/bin/docker" not in data
    ):
        raise Blocked("QA_SYSTEMD_GUARD_OR_COMPOSE_MISMATCH")


def check_qa_compose_hardening(item: dict) -> None:
    """Reject network, secrets, mounts or a different test process."""
    if (
        item.get("image") != "svt-qa-verified-local:0.5.21-hw"
        or item.get("pull_policy") != "never"
        or item.get("read_only") is not True
        or item.get("user") != "10001:10001"
        or "ALL" not in (item.get("cap_drop") or [])
        or "no-new-privileges:true" not in (item.get("security_opt") or [])
        or item.get("privileged")
        or item.get("ports")
        or item.get("secrets")
        or item.get("devices")
    ):
        raise Blocked("QA_COMPOSE_SECURITY_CHANGED")
    if item.get("volumes") != [
        {"type": "bind", "source": str(ROOT / "data"), "target": "/data"}
    ]:
        raise Blocked("QA_COMPOSE_WRITABLE_MOUNTS_CHANGED")
    if item.get("environment") != {"PYTHONDONTWRITEBYTECODE": "1"}:
        raise Blocked("QA_COMPOSE_ENVIRONMENT_CHANGED")
    cmd = item.get("command")
    if (
        not isinstance(cmd, list)
        or len(cmd) != 3
        or cmd[:2] != ["python", "-c"]
        or "app.Storage" not in cmd[2]
        or "qa_writer_starts" not in cmd[2]
    ):
        raise Blocked("QA_COMPOSE_COMMAND_NOT_SYNTHETIC")


def check_target() -> dict:
    """Require fixed local host, image, policy and network ownership."""
    if socket.gethostname() != "shelly-docker-reboot-qa":
        raise Blocked("WRONG_QA_GUEST")
    check_qa_systemd_owner()
    if inspect("{{.Image}}") != IMAGE:
        raise Blocked("QA_IMAGE_ID_MISMATCH")
    if inspect("{{.HostConfig.RestartPolicy.Name}}") != "no":
        raise Blocked("INDEPENDENT_DOCKER_RESTART_UNFENCED")
    if inspect("{{.HostConfig.NetworkMode}}") != "none":
        raise Blocked("QA_WRITER_NETWORK_EXPOSED")
    compose = json.loads(
        command(
            [
                "docker",
                "compose",
                "-f",
                str(ROOT / "compose.yaml"),
                "config",
                "--format",
                "json",
            ]
        )
    )
    services = compose.get("services", {})
    if len(services) != 1:
        raise Blocked("QA_COMPOSE_SERVICE_COUNT_INVALID")
    item = next(iter(services.values()))
    if item.get("container_name") != NAME or item.get("restart") != "no":
        raise Blocked("QA_COMPOSE_RESTART_NOT_FENCED")
    if item.get("network_mode") != "none" or item.get("ports"):
        raise Blocked("QA_COMPOSE_NETWORK_EXPOSED")
    check_qa_compose_hardening(item)
    return {"running": inspect("{{.State.Running}}") == "true"}


def evaluate() -> dict:
    """Classify one candidate without granting permission elsewhere."""
    target = check_target()
    samples = check_sqlite()
    pending = ROOT / "state/pending-update"
    if pending.is_symlink():
        raise Blocked("QA_PENDING_SYMLINK")
    if pending.exists():
        return {"status": "SKIP_PENDING", "samples": samples}
    if target["running"]:
        return {"status": "SKIP_ALREADY_RUNNING", "samples": samples}
    return {"status": "QA_RESTART_CANDIDATE", "samples": samples}


def restart_if_safe() -> dict:
    """Delegate a checked writer restart to its one systemd owner."""
    state = evaluate()
    if state["status"] != "QA_RESTART_CANDIDATE":
        return state
    if (ROOT / "state/pending-update").exists():
        return {"status": "SKIP_PENDING_RACE"}
    command(["systemctl", "restart", UNIT])
    for _ in range(35):
        if inspect("{{.State.Running}}") == "true":
            return {"status": "QA_SYSTEMD_RESTARTED"}
        time.sleep(0.2)
    raise Blocked("QA_RESTART_DID_NOT_RUN")


def main() -> int:
    """Be inert without an explicit, fully scoped disposable consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.dry_run or not args.run_disposable:
        print("DRY-RUN PASS: no services inspected or restarted")
        return 0
    if args.confirm != CONFIRM:
        print("FAIL: scoped QA-only consent required")
        return 2
    try:
        result = restart_if_safe()
    except (Blocked, ValueError, sqlite3.Error) as exc:
        print("BLOCKED", str(exc))
        return 3
    result.update(
        {
            "scope": "DISPOSABLE_QA_LXC114_ONLY",
            "production_modified": False,
            "boot_start_authorized": False,
            "source_install_authorized": False,
            "telegram_photo_authorized": False,
            "all_restart_owners_tested": False,
        }
    )
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
