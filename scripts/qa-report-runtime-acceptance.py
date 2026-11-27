#!/usr/bin/env python3
"""Review three-phase readiness and history of an isolated recovery launch."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "qa_mode_tx", Path(__file__).with_name("qa-report-mode-transaction.py")
)
assert SPEC and SPEC.loader
TX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TX)

PHASES = ("L1", "L2", "L3")
FRESH = 10.0
MIN_SAMPLES = 3


def validate_ready(payload: dict) -> None:
    """Require explicit healthy, fresh readiness from all three phases."""
    phases = payload.get("phases")
    if (
        payload.get("ready") is not True
        or payload.get("unready_phases") != []
        or not isinstance(phases, dict)
        or set(phases) != set(PHASES)
    ):
        raise ValueError("three-phase readiness unavailable")
    for phase in PHASES:
        data = phases[phase]
        age = data.get("age")
        if (
            data.get("ready") is not True
            or data.get("connectivity") != "online"
            or type(age) not in (float, int)
            or not math.isfinite(age)
            or not 0 <= age <= FRESH
        ):
            raise ValueError("phase not ready or stale")


def fetch_ready(identity: str) -> dict:
    """Query the real app readiness handler inside the isolated container."""
    command = (
        "import urllib.request; "
        "r=urllib.request.urlopen('http://127.0.0.1:8080/ready',timeout=3); "
        "print(r.read(8193).decode())"
    )
    result = subprocess.run(
        ["docker", "exec", identity, "python", "-c", command],
        capture_output=True,
        text=True,
        timeout=8,
        check=True,
    )
    if len(result.stdout) > 8192:
        raise ValueError("oversized readiness reply")
    payload = json.loads(result.stdout)
    validate_ready(payload)
    return payload


def phase_evidence(
    db: sqlite3.Connection, baseline: int, now: float, started: float
) -> dict:
    """Check fresh post-start samples and plausible measurements per phase."""
    result = {}
    for phase in PHASES:
        rows = db.execute(
            "SELECT ts,voltage,frequency FROM measurements "
            "WHERE id>? AND phase=? ORDER BY id DESC LIMIT ?",
            (baseline, phase, MIN_SAMPLES),
        ).fetchall()
        if len(rows) != MIN_SAMPLES:
            raise ValueError("insufficient new phase samples")
        times = []
        for ts, voltage, frequency in rows:
            if not all(math.isfinite(v) for v in (ts, voltage, frequency)):
                raise ValueError("nonfinite sample")
            if not (
                max(started, now - FRESH) <= ts <= now
                and 207 <= voltage <= 253
                and 40 <= frequency <= 60
            ):
                raise ValueError("invalid or stale phase sample")
            times.append(ts)
        if any(a <= b for a, b in zip(times, times[1:])):
            raise ValueError("nonmonotonic phase samples")
        result[phase] = {
            "samples": len(rows),
            "latest_age": round(now - times[0], 3),
        }
    return result


def live_phase_evidence(root: Path, intent: dict, started_at: str) -> dict:
    """Read current phase rows from the same original database inode."""
    path = root / "data/qa.sqlite3"
    info = path.lstat()
    baseline = intent["baseline"]
    if (info.st_dev, info.st_ino) != (baseline["device"], baseline["inode"]):
        raise ValueError("database identity changed")
    started = datetime.fromisoformat(
        started_at.replace("Z", "+00:00")
    ).timestamp()
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    ) as db:
        db.execute("BEGIN")
        return phase_evidence(db, baseline["last_id"], time.time(), started)


def accept(root: Path, name: str) -> dict:
    """Bind a successful QA-only acceptance to an exact running launch."""
    observation = TX.observe_start(root, name)
    intent = TX.read_record(root / "start-intent.json")
    identity = observation["container_id"]
    started_at = observation["started_at"]
    fetch_ready(identity)
    phases = live_phase_evidence(root, intent, started_at)
    final = TX.start_binding(root, name, running=True)
    if final["Id"] != identity or final["State"]["StartedAt"] != started_at:
        raise ValueError("launch changed during acceptance")
    result = {
        "container_id": identity,
        "started_at": started_at,
        "history_verified": True,
        "qa_runtime_verified": True,
        "deployment_authorized": False,
        "phases": phases,
        "observed_at": time.time(),
    }
    path = root / "runtime-acceptance.json"
    if os.path.lexists(path):
        prior = TX.read_record(path)
        if any(
            prior.get(k) != result[k]
            for k in (
                "container_id",
                "started_at",
                "history_verified",
                "qa_runtime_verified",
                "deployment_authorized",
            )
        ):
            raise ValueError("acceptance identity changed")
        return prior
    TX.atomic_write(path, (json.dumps(result, sort_keys=True) + "\n").encode())
    return result


def accept_restart(root: Path, name: str) -> dict:
    """Recheck fresh three-phase telemetry after the one reviewed restart."""
    info = TX.observe_verified_restart(root, name)
    identity, started = info["Id"], info["State"]["StartedAt"]
    policy = TX.read_record(root / "verified-restart-policy.json")
    fetch_ready(identity)
    phases = live_phase_evidence(root, policy, started)
    final = TX.observe_verified_restart(root, name)
    if final["Id"] != identity or final["State"]["StartedAt"] != started:
        raise ValueError("restart launch changed during acceptance")
    result = {
        "container_id": identity,
        "started_at": started,
        "qa_restart_verified": True,
        "deployment_authorized": False,
        "phases": phases,
        "observed_at": time.time(),
    }
    path = root / "verified-restart-acceptance.json"
    if os.path.lexists(path):
        prior = TX.read_record(path)
        if any(
            prior.get(k) != result[k]
            for k in (
                "container_id",
                "started_at",
                "qa_restart_verified",
                "deployment_authorized",
            )
        ):
            raise ValueError("restart acceptance changed")
        return prior
    TX.atomic_write(path, (json.dumps(result, sort_keys=True) + "\n").encode())
    return result


def main() -> int:
    """Review a live isolated fixture without launching or changing it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--container", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--seal-restart", action="store_true")
    action.add_argument("--review-restart", action="store_true")
    args = parser.parse_args()
    try:
        TX.require_qa(args.root)
        with TX.exclusive(args.root, observation=True):
            if args.review_restart:
                result = accept_restart(args.root, args.container)
            else:
                result = accept(args.root, args.container)
                if args.seal_restart:
                    result = TX.seal_restart_policy(args.root, args.container)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        sqlite3.Error,
        TX.WriterFenceError,
        subprocess.SubprocessError,
    ) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": type(exc).__name__}))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
