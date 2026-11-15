#!/usr/bin/env python3
"""Validate two local WAL-complete SQLite software rollback copies offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

CONSENT = "OFFLINE_LOCAL_WAL_TWO_RESTORES_QA_ONLY"
QA_IMAGE = (
    "sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b"
)


class BackupRejected(RuntimeError):
    """Fail closed when backup metadata, integrity or identity differs."""


def guest_guard() -> None:
    """Allow explicit disposable backup integration only on offline LXC114."""
    if os.geteuid() != 0:
        raise BackupRejected("QA_ROOT_REQUIRED")
    if socket.gethostname() != "shelly-docker-reboot-qa":
        raise BackupRejected("QA_WRONG_GUEST")
    if {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise BackupRejected("QA_EXTERNAL_NETWORK_PRESENT")


def sha256_file(path: Path) -> str:
    """Hash an invented regular file without trusting its display name."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def sqlite_state(conn: sqlite3.Connection) -> tuple[str, int, int, int]:
    """Check integrity and exact synthetic row identity."""
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    count, total, last = conn.execute(
        "SELECT count(*), coalesce(sum(id), 0), "
        "coalesce(max(id), 0) FROM synthetic_samples"
    ).fetchone()
    return quick, count, total, last


def make_source(root: Path) -> sqlite3.Connection:
    """Keep active WAL data uncheckpointed while backing up live SQLite."""
    path = root / "running.sqlite3"
    conn = sqlite3.connect(path)
    if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
        raise BackupRejected("WAL_MODE_NOT_AVAILABLE")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute(
        "CREATE TABLE synthetic_samples "
        "(id INTEGER PRIMARY KEY, volts REAL NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO synthetic_samples VALUES (?, ?)",
        ((i, 210.0 + i / 1000) for i in range(1, 421)),
    )
    conn.commit()
    wal = root / "running.sqlite3-wal"
    if not wal.is_file() or wal.stat().st_size == 0:
        raise BackupRejected("LIVE_WAL_NOT_PRESENT")
    if sqlite_state(conn)[1] != 420:
        raise BackupRejected("QA_SOURCE_WRONG_ROWS")
    return conn


def create_online_backup(conn: sqlite3.Connection, destination: Path) -> None:
    """Create atomic 0600 offline-local backup via SQLite online API."""
    partial = destination.with_suffix(".partial")
    if destination.exists() or partial.exists():
        raise BackupRejected("QA_BACKUP_PATH_ALREADY_EXISTS")
    with closing(sqlite3.connect(partial)) as target:
        conn.backup(target)
    os.chmod(partial, 0o600)
    with partial.open("rb") as file:
        os.fsync(file.fileno())
    os.replace(partial, destination)


def fixture_manifest(
    root: Path, conn: sqlite3.Connection, copies: tuple[Path, Path]
) -> dict:
    """Bind both source-copy hashes to one explicit unsigned QA identity."""
    code = Path(__file__).resolve().parents[1] / "app.py"
    if not code.is_file():
        raise BackupRejected("QA_APP_SOURCE_MISSING")
    return {
        "kind": "synthetic_qa_only",
        "signature_verified": False,
        "image_id": QA_IMAGE,
        "qa_app_source_sha256": sha256_file(code),
        "source_sqlite_state": list(sqlite_state(conn)),
        "local_snapshot_sha256": {
            copy.name: sha256_file(copy) for copy in copies
        },
        "backup_medium": "same_physical_nvme",
        "physical_independence": False,
        "owner_approval": False,
    }


def write_manifest(path: Path, manifest: dict) -> None:
    """Write only test-only identity fields with private permissions."""
    tmp = path.with_suffix(".partial")
    if path.exists() or tmp.exists():
        raise BackupRejected("QA_MANIFEST_ALREADY_EXISTS")
    with tmp.open("x", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def verify_snapshot(
    snapshot: Path, restore: Path, manifest: dict
) -> tuple[str, int, int, int]:
    """Reject tampered or stale copy before independently restoring it."""
    expected = manifest["local_snapshot_sha256"].get(snapshot.name)
    if not expected or sha256_file(snapshot) != expected:
        raise BackupRejected("QA_SNAPSHOT_CHECKSUM_MISMATCH")
    if restore.exists():
        raise BackupRejected("QA_RESTORE_PATH_EXISTS")
    shutil.copyfile(snapshot, restore)
    os.chmod(restore, 0o600)
    if sha256_file(restore) != expected:
        raise BackupRejected("QA_RESTORE_COPY_HASH_MISMATCH")
    uri = f"file:{restore}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as database:
        state = sqlite_state(database)
    if state != tuple(manifest["source_sqlite_state"]):
        raise BackupRejected("QA_SNAPSHOT_STATE_STALE_OR_INVALID")
    return state


def assert_corrupt_rejected(
    root: Path, snapshot: Path, manifest: dict
) -> None:
    """A checksum mismatch may never be accepted as a good rollback."""
    damaged = root / "local-A-damaged.sqlite3"
    shutil.copyfile(snapshot, damaged)
    with damaged.open("r+b") as stream:
        stream.seek(-20, os.SEEK_END)
        original = stream.read(1)
        stream.seek(-20, os.SEEK_END)
        stream.write(bytes([original[0] ^ 0x80]))
    altered = dict(manifest)
    hashes = dict(manifest["local_snapshot_sha256"])
    hashes[damaged.name] = hashes[snapshot.name]
    altered["local_snapshot_sha256"] = hashes
    try:
        verify_snapshot(damaged, root / "rejected-restore.sqlite3", altered)
    except BackupRejected as exc:
        if str(exc) != "QA_SNAPSHOT_CHECKSUM_MISMATCH":
            raise
    else:
        raise BackupRejected("QA_DAMAGED_COPY_FALSE_ACCEPT")


def assert_stale_rejected(root: Path, snapshot: Path, manifest: dict) -> None:
    """A signed-looking but older expected row count is not current."""
    modified = dict(manifest)
    old = list(manifest["source_sqlite_state"])
    old[1] -= 1
    modified["source_sqlite_state"] = old
    try:
        verify_snapshot(snapshot, root / "stale-restore.sqlite3", modified)
    except BackupRejected as exc:
        if str(exc) != "QA_SNAPSHOT_STATE_STALE_OR_INVALID":
            raise
    else:
        raise BackupRejected("QA_STALE_COPY_FALSE_ACCEPT")


def run_once(root: Path) -> dict:
    """Back up active WAL twice and restore both to separate test paths."""
    source = make_source(root)
    try:
        copies = (root / "local-A.sqlite3", root / "local-B.sqlite3")
        for copy in copies:
            create_online_backup(source, copy)
        manifest = fixture_manifest(root, source, copies)
        write_manifest(root / "backup-manifest.json", manifest)
        states = [
            verify_snapshot(copy, root / f"restored-{i}.sqlite3", manifest)
            for i, copy in enumerate(copies, 1)
        ]
        assert_corrupt_rejected(root, copies[0], manifest)
        assert_stale_rejected(root, copies[1], manifest)
        return {
            "result": "PASS_LOCAL_ONLY_WAL_TWO_RESTORES",
            "online_wal": True,
            "two_separate_restores": len(states),
            "state": list(states[0]),
            "both_equal": states[0] == states[1],
            "tampered_copy_rejected": True,
            "stale_state_rejected": True,
            "qa_image_signature_verified": False,
            "physically_independent": False,
            "production_sqlite_accessed": False,
            "release_decision": "NO_GO",
        }
    finally:
        source.close()


def main() -> int:
    """Remain inert unless exact offline guest confirmation is supplied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no SQLite or backup files created")
        return 0
    if args.confirm != CONSENT:
        print("BLOCKED: exact isolated QA consent required")
        return 2
    try:
        guest_guard()
        with tempfile.TemporaryDirectory(
            prefix="svt-qa-local-wal-twin-", dir="/var/tmp"
        ) as folder:
            output = run_once(Path(folder))
    except (BackupRejected, sqlite3.Error, OSError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:180])
        return 3
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
