#!/usr/bin/env python3
"""Rehearse two fully independent synthetic current-history SQLite restores."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path

CONFIRM = "OFFLINE_SYNTHETIC_CURRENT_SQLITE_AB_QA_ONLY"
PREFIX = "svt-current-ab-qa-"
EXPECTED_ROWS = 258
SOURCE_FIXTURE = b"synthetic-source-archive\nversion=0.5.21-test\n"


class Blocked(RuntimeError):
    """The candidate backup is not independently restorable."""


def guest_guard() -> None:
    """Require exact isolated guest and inactive original writer."""
    if os.geteuid() != 0 or socket.gethostname() != "shelly-docker-reboot-qa":
        raise Blocked("OFFLINE_QA_ONLY")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise Blocked("EXTERNAL_NETWORK_INTERFACE")
    command = [
        "docker",
        "inspect",
        "--format",
        "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}",
        "svt-qa-signed-writer-114",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=6
    )
    if result.returncode or result.stdout.strip() != "false|no":
        raise Blocked("ORIGINAL_QA_WRITER_ACTIVE")
    for unit in ("svt-qa-compose.service", "svt-qa-guarded-watchdog.timer"):
        result = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
        if result.stdout.strip() != "disabled":
            raise Blocked("QA_OWNER_ENABLED")


def sqlite_schema(conn: sqlite3.Connection) -> None:
    """Create a synthetic, nontrivial history and metadata schema."""
    conn.execute("PRAGMA user_version=13")
    conn.execute("PRAGMA application_id=395105")
    conn.execute(
        "CREATE TABLE readings(id INTEGER PRIMARY KEY, ts INTEGER, "
        "voltage REAL, hz REAL, phase TEXT)"
    )
    conn.execute("CREATE INDEX idx_readings_ts ON readings(ts)")
    conn.execute(
        "CREATE TABLE outages(id INTEGER PRIMARY KEY, start INTEGER, "
        "end INTEGER, reason TEXT)"
    )
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TRIGGER reading_guard BEFORE INSERT ON readings "
        "WHEN NEW.voltage < 0 BEGIN SELECT RAISE(ABORT, 'negative'); END"
    )
    conn.commit()


def populate(conn: sqlite3.Connection) -> None:
    """Commit 256 measurements plus outage and metadata records."""
    records = [
        (i, 1_700_000_000 + i * 60, 214.0 + i % 11, 50.0, "L")
        for i in range(1, 257)
    ]
    conn.executemany(
        "INSERT INTO readings(id,ts,voltage,hz,phase) VALUES(?,?,?,?,?)",
        records,
    )
    conn.executemany(
        "INSERT INTO outages VALUES(?,?,?,?)",
        [(1, 100, 180, "scheduled"), (2, 200, None, "investigate")],
    )
    conn.executemany(
        "INSERT INTO meta VALUES(?,?)",
        [("group", "10.1"), ("origin", "invented"), ("display", "uk")],
    )
    conn.commit()


def append_wal(conn: sqlite3.Connection, ident: int) -> None:
    """Add independently committed current history while WAL is open."""
    conn.execute(
        "INSERT INTO readings VALUES(?,?,?,?,?)",
        (ident, 1_700_050_000 + ident, 221.7, 49.9, "L"),
    )
    conn.commit()


def canonical(path: Path) -> str:
    """Hash schema, all logical rows and SQLite metadata after integrity."""
    uri = "file:" + str(path) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("SQLITE_QUICK_CHECK_FAILED")
        schema = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        history = conn.execute(
            "SELECT id,ts,voltage,hz,phase FROM readings ORDER BY id"
        ).fetchall()
        outages = conn.execute(
            "SELECT id,start,end,reason FROM outages ORDER BY id"
        ).fetchall()
        meta = conn.execute(
            "SELECT key,value FROM meta ORDER BY key"
        ).fetchall()
        header = (
            conn.execute("PRAGMA user_version").fetchone()[0],
            conn.execute("PRAGMA application_id").fetchone()[0],
        )
    payload = (schema, history, outages, meta, header)
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def online_backup(source: sqlite3.Connection, target: Path) -> None:
    """Include committed WAL via SQLite backup API, not file-only copy."""
    with closing(sqlite3.connect(target)) as destination:
        source.backup(destination, pages=16, sleep=0.01)
    os.chmod(target, 0o600)
    descriptor = os.open(target, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def copy_source(root: Path, location: Path) -> str:
    """Stage a synthetic source blob with an exact expected SHA256."""
    path = location / "source.fixture"
    with path.open("xb") as stream:
        stream.write(SOURCE_FIXTURE)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(source: sqlite3.Connection, root: Path, name: str) -> Path:
    """Create one independent private SQLite backup and source fixture."""
    folder = root / name
    folder.mkdir(mode=0o700)
    online_backup(source, folder / "current.sqlite3")
    copy_source(root, folder)
    return folder


def verify_restore(root: Path, folder: Path, current_hash: str) -> bool:
    """Restore independently and compare all logical current records."""
    source = folder / "current.sqlite3"
    if canonical(source) != current_hash:
        return False
    restore = root / ("restore-" + folder.name + ".sqlite3")
    with (
        closing(sqlite3.connect(source)) as archived,
        closing(sqlite3.connect(restore)) as recovered,
    ):
        archived.backup(recovered)
    if canonical(restore) != current_hash:
        return False
    digest = hashlib.sha256((folder / "source.fixture").read_bytes()).digest()
    return digest == hashlib.sha256(SOURCE_FIXTURE).digest()


def clean_case(root: Path) -> dict:
    """Verify two independent A/B restores against newest committed WAL."""
    live = root / "live.sqlite3"
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        sqlite_schema(conn)
        populate(conn)
        append_wal(conn, 257)
        append_wal(conn, 258)
        latest = canonical(live)
        first = snapshot(conn, root, "A")
        second = snapshot(conn, root, "B")
        if not verify_restore(root, first, latest):
            raise Blocked("BACKUP_A_RESTORE_MISMATCH")
        if not verify_restore(root, second, latest):
            raise Blocked("BACKUP_B_RESTORE_MISMATCH")
        count = conn.execute("SELECT count(*) FROM readings").fetchone()[0]
    if count != EXPECTED_ROWS:
        raise Blocked("SYNTHETIC_HISTORY_COUNT_WRONG")
    return {
        "case": "clean_two_independent_restores",
        "A": "PASS",
        "B": "PASS",
        "current_rows": count,
    }


def stale_case(root: Path) -> dict:
    """Reject old backup when new WAL history is committed before B."""
    live = root / "stale-live.sqlite3"
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        sqlite_schema(conn)
        populate(conn)
        append_wal(conn, 257)
        first = snapshot(conn, root, "stale-A")
        append_wal(conn, 258)
        second = snapshot(conn, root, "fresh-B")
        current_hash = canonical(live)
        if verify_restore(root, first, current_hash):
            raise Blocked("STALE_A_FALSE_ACCEPT")
        if not verify_restore(root, second, current_hash):
            raise Blocked("CURRENT_B_FALSE_REJECT")
    return {
        "case": "wal_advanced_between_backups",
        "A": "BLOCKED_STALE",
        "B": "PASS",
    }


def corrupted_case(root: Path) -> dict:
    """Reject a damaged backup before any restore comparison succeeds."""
    live = root / "corrupt-live.sqlite3"
    with closing(sqlite3.connect(live)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        sqlite_schema(conn)
        populate(conn)
        archive = snapshot(conn, root, "corrupt-B")
        destination = archive / "current.sqlite3"
        with destination.open("r+b") as stream:
            stream.seek(0)
            stream.write(b"INVALID_HEADER___")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            accepted = verify_restore(root, archive, canonical(live))
        except (sqlite3.DatabaseError, Blocked):
            accepted = False
        if accepted:
            raise Blocked("CORRUPT_SQLITE_FALSE_ACCEPT")
    return {"case": "corrupted_backup_header", "B": "BLOCKED_CORRUPT"}


def run_suite() -> dict:
    """Execute the three synthetic cases under a private short-lived tree."""
    with tempfile.TemporaryDirectory(prefix=PREFIX, dir="/var/tmp") as name:
        root = Path(name)
        root.chmod(0o700)
        results = [clean_case(root), stale_case(root), corrupted_case(root)]
        return {
            "cases": results,
            "signature": "NOT_TESTED_SYNTHETIC",
            "production_read_or_modified": False,
        }


def main() -> int:
    """Do nothing by default; require exact isolated-guest consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    options = parser.parse_args()
    if not options.run_disposable:
        print("DRY_RUN: no SQLite files or backups created")
        return 0
    if options.confirm != CONFIRM:
        print("BLOCKED: exact QA-only confirmation required")
        return 2
    try:
        guest_guard()
        result = run_suite()
    except (
        Blocked,
        OSError,
        sqlite3.DatabaseError,
        subprocess.SubprocessError,
    ) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:250])
        return 3
    print(
        json.dumps(
            {"result": "PASS_SYNTHETIC_CURRENT_AB", "evidence": result},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
