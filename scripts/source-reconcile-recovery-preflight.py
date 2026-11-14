#!/usr/bin/env python3
"""Read-only guard for signed-source roll-forward recovery."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

GUARD = Path(__file__).with_name("installer-signed-image-guard.py")
AUDIT = Path(__file__).with_name("deployment-state-audit.py")
HEALTH = "http://127.0.0.1:8080/health"
READY = "http://127.0.0.1:8080/ready"


class Blocked(Exception):
    """Report a safe reason code, never a private value."""


def load_module(path: Path, name: str):
    """Load established signed-state checks without duplicating trust rules."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise Blocked("SIGNED_CHECK_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def signed_identity(source: Path, state: Path) -> str:
    """Require matching signed source, private receipt and actual OCI image."""
    guard = load_module(GUARD, "recovery_signed_guard")
    try:
        report = guard.inspect(source, state, dry_run=True)
    except (guard.GuardBlocked, OSError, ValueError) as exc:
        raise Blocked("SIGNED_IDENTITY_NOT_VERIFIED") from exc
    if report["status"] != "PASS":
        raise Blocked("SIGNED_IDENTITY_NOT_VERIFIED")
    return report["source_version"]


def installed_version(root: Path) -> str:
    """Read installed source markers using the existing secure validator."""
    audit = load_module(AUDIT, "recovery_installed_audit")
    try:
        return audit.version_marker(root)
    except (audit.AuditBlocked, OSError) as exc:
        raise Blocked("INSTALLED_SOURCE_UNSAFE") from exc


def database_state(path: Path) -> dict:
    """Read SQLite integrity and outage counts without any writes."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Blocked("SQLITE_PATH_UNSAFE")
        with closing(
            sqlite3.connect(
                f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3
            )
        ) as db:
            db.execute("PRAGMA query_only=ON")
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            samples = db.execute(
                "SELECT count(*) FROM measurements"
            ).fetchone()[0]
            open_outages = db.execute(
                "SELECT count(*) FROM outages WHERE ended_at IS NULL"
            ).fetchone()[0]
    except (OSError, sqlite3.Error) as exc:
        raise Blocked("SQLITE_READ_FAILED") from exc
    return {
        "integrity": integrity,
        "samples": samples,
        "open_outages": open_outages,
    }


def live_state() -> dict:
    """Probe only the fixed localhost endpoints and never contact Shelly."""
    try:
        with urlopen(HEALTH, timeout=4) as response:
            payload = response.read(16385)
            if len(payload) > 16384:
                raise Blocked("HEALTH_RESPONSE_TOO_LARGE")
            health = json.loads(payload)
        try:
            with urlopen(READY, timeout=4) as response:
                ready = response.status == 200
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code != 503:
                raise Blocked("READY_UNKNOWN") from exc
            ready = False
    except (OSError, URLError, ValueError, TypeError) as exc:
        raise Blocked("HEALTH_UNAVAILABLE") from exc
    if not isinstance(health, dict):
        raise Blocked("HEALTH_RESPONSE_INVALID")
    return {
        "connectivity": health.get("connectivity"),
        "stale": health.get("sample_is_stale"),
        "ready": ready,
    }


def decision(version: str, installed: str, db: dict, live: dict) -> dict:
    """Assess forward readiness while never authorizing live writes."""
    reasons = []
    if db["integrity"] != "ok" or db["samples"] < 1:
        reasons.append("SQLITE_INTEGRITY_OR_HISTORY")
    if db["open_outages"] != 0:
        reasons.append("OPEN_OUTAGE")
    if live["connectivity"] != "online" or live["stale"] is not False:
        reasons.append("SHELLY_OFFLINE_OR_STALE")
    if not live["ready"]:
        reasons.append("SHELLY_NOT_READY")
    return {
        "status": "BLOCKED" if reasons else "PREFLIGHT_PASS",
        "reasons": reasons,
        "candidate_version": version,
        "installed_version": installed,
        "strategy": "FORWARD_REAPPLY_MATCHING_SIGNED_SOURCE",
        "live_sqlite_preserved": True,
        "docker_build_allowed": False,
        "source_install_authorized": False,
        "writer_fenced_snapshot_required": True,
        "public_photo_authorized": False,
        "production_modified": False,
    }


def preflight(source: Path, installed: Path, state: Path, db: Path) -> dict:
    """Produce a bounded plan from signed checks and live read-only probes."""
    try:
        version = signed_identity(source, state)
        old = installed_version(installed)
        database = database_state(db)
        live = live_state()
        return decision(version, old, database, live)
    except Blocked as exc:
        return {
            "status": "BLOCKED",
            "reasons": [str(exc)],
            "production_modified": False,
            "source_install_authorized": False,
            "public_photo_authorized": False,
        }


def main() -> int:
    """Print a machine-readable or concise human-readable preflight."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument(
        "--root", type=Path, default=Path("/opt/shelly-voltage")
    )
    parser.add_argument(
        "--state", type=Path, default=Path("/var/lib/shelly-voltage-updater")
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("/var/lib/shelly-voltage/voltage.sqlite3"),
    )
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    result = preflight(args.source, args.root, args.state, args.db)
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("SOURCE RECOVERY PREFLIGHT:", result["status"])
        for reason in result.get("reasons", []):
            print(" -", reason)
        print("NO PRODUCTION WRITES; MAINTENANCE NOT AUTHORIZED")
    return 0 if result["status"] == "PREFLIGHT_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
