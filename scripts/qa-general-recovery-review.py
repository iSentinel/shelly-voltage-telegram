#!/usr/bin/env python3
"""Review synthetic recovery evidence without restoring or starting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

PHASES = (
    "preboot",
    "previous-pair-restored",
    "candidate-may-have-run",
    "verified",
)
FAULTS = ("report-only", "general-runtime", "unknown")


def decision(phase, fault, pending, parity, rescue_matches):
    """Return a conservative review route, never an executable permission."""
    if (
        phase not in PHASES
        or fault not in FAULTS
        or any(type(v) is not bool for v in (pending, parity, rescue_matches))
    ):
        raise ValueError("invalid recovery evidence")
    if phase in ("preboot", "previous-pair-restored"):
        route = "REVIEW_PREBOOT_PREVIOUS_PAIR"
        if pending:
            route = "REVIEW_PREVIOUS_PAIR_UNRESOLVED_DELIVERY"
    elif fault == "report-only":
        route = "REVIEW_SAME_RUNTIME_PAUSED"
    else:
        route = "REVIEW_FORWARD_REPAIR_OR_FENCED_HOLD"
    return {
        "route": route,
        "pending_report_intent": pending,
        "monitor_inputs_match": parity,
        "rescue_matches_live": rescue_matches,
        "strict_parity_or_pending_blocker": pending or not parity,
        "rollback_authorized": False,
        "restore_database_authorized": False,
        "startup_authorized": False,
        "deployment_authorized": False,
    }


def identity(path):
    """Require an ordinary single-link file, not a symlink or device."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("unsafe evidence file")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def snapshot(path):
    """Hash a stopped disposable SQLite database including every table."""
    before = identity(path)
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.lexists(str(path) + suffix):
            raise ValueError("require standalone stopped copies")
    digest = hashlib.sha256()
    with closing(
        sqlite3.connect(
            path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=3
        )
    ) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise ValueError("SQLite integrity failed")
        for pragma in ("user_version", "application_id"):
            digest.update(
                str(db.execute(f"PRAGMA {pragma}").fetchone()).encode()
            )
        for line in db.iterdump():
            digest.update(line.encode() + b"\n")
        pending = db.execute(
            "SELECT count(*) FROM meta WHERE key GLOB "
            "'telegram_report_pending:*'"
        ).fetchone()[0]
        rows = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
    if identity(path) != before or any(
        os.path.lexists(str(path) + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    ):
        raise ValueError("evidence file changed")
    return {
        "logical_sha256": digest.hexdigest(),
        "pending": pending,
        "rows": rows,
        "device": before[0],
        "inode": before[1],
    }


def require_qa(root):
    """Reject live paths and require a protected offline fixture."""
    if (
        os.geteuid() != 0
        or root.parent != Path("/var/lib")
        or not re.fullmatch(
            r"svt-general-recovery-qa\.[A-Za-z0-9]{6}", root.name
        )
        or socket.gethostname() != "shelly-docker-reboot-qa"
        or sorted(name for _, name in socket.if_nameindex()) != ["lo"]
    ):
        raise ValueError("dedicated offline QA fixture required")
    for path in (root, *root.parents):
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
        ):
            raise ValueError("unprotected QA fixture")
    for name in ("live.sqlite3", "rescue.sqlite3"):
        path = root / name
        info = path.lstat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("unprotected QA database")
        for suffix in ("-wal", "-shm", "-journal"):
            if os.path.lexists(str(path) + suffix):
                raise ValueError("require standalone stopped copies")


def review(root, phase, fault, parity):
    """Compare only designated synthetic stopped fixture files."""
    require_qa(root)
    live, rescue = (
        snapshot(root / name) for name in ("live.sqlite3", "rescue.sqlite3")
    )
    if (live["device"], live["inode"]) == (rescue["device"], rescue["inode"]):
        raise ValueError("rescue must be distinct")
    return {
        "status": "PASS_QA_RECOVERY_REVIEW_ONLY",
        "phase": phase,
        "fault": fault,
        "phase_fault_parity_operator_supplied": True,
        "live": live,
        "rescue": rescue,
        **decision(
            phase,
            fault,
            bool(live["pending"]),
            parity,
            live["logical_sha256"] == rescue["logical_sha256"],
        ),
    }


def main():
    """Use only stopped synthetic copies; no live service or Docker calls."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qa-only", action="store_true", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--fault", choices=FAULTS, required=True)
    parser.add_argument("--parity", choices=("match", "differ"), required=True)
    args = parser.parse_args()
    try:
        result = review(
            args.root, args.phase, args.fault, args.parity == "match"
        )
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
