#!/usr/bin/env python3
"""Verify local Shelly release-prep copies without WAL/SHM side effects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

FILES = {"current-A.sqlite3", "current-B.sqlite3", "manifest.json"}
EXPECTED_KIND = "single_nvme_online_release_preparation"


class Rejected(RuntimeError):
    """A local backup has not passed the strict read-only preflight."""


def private_file(path: Path, directory: bool = False) -> None:
    """Reject symlinks, wrong ownership, extra links or loose permissions."""
    info = path.lstat()
    correct = (
        stat.S_ISDIR(info.st_mode)
        if directory
        else (stat.S_ISREG(info.st_mode))
    )
    mode = 0o700 if directory else 0o600
    if not correct or info.st_uid != os.geteuid():
        raise Rejected("UNSAFE_FILE_TYPE_OR_OWNER")
    if stat.S_IMODE(info.st_mode) != mode:
        raise Rejected("UNSAFE_FILE_PERMISSIONS")
    if not directory and info.st_nlink != 1:
        raise Rejected("MULTIPLE_HARDLINKS")


def hash_file(path: Path) -> str:
    """Hash a fixed private snapshot without opening it for writing."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(path: Path) -> dict:
    """Read a small private JSON manifest and validate its safety flags."""
    private_file(path)
    if path.stat().st_size > 65536:
        raise Rejected("MANIFEST_TOO_LARGE")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise Rejected("INVALID_MANIFEST_SHAPE")
    if value.get("kind") != EXPECTED_KIND:
        raise Rejected("UNKNOWN_BACKUP_KIND")
    if value.get("physical_failure_domains") != 1:
        raise Rejected("BACKUP_DOMAIN_METADATA_MISMATCH")
    if value.get("last_cutover_snapshot") is not False:
        raise Rejected("INCORRECT_CUTOVER_CLAIM")
    if value.get("release_approval") is not False:
        raise Rejected("UNVERIFIED_OWNER_APPROVAL_CLAIM")
    if value.get("runtime_signature_reverified_in_this_run") is not False:
        raise Rejected("UNVERIFIED_SIGNATURE_CLAIM")
    return value


def sqlite_state(path: Path) -> dict:
    """Inspect a standalone SQLite immutable copy without sidecar writes."""
    uri = path.resolve(strict=True).as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Rejected("SQLITE_INTEGRITY_FAILED")
        rows, latest = db.execute(
            "SELECT count(*), max(ts) FROM measurements"
        ).fetchone()
        schema = db.execute(
            "SELECT type, name, tbl_name, sql "
            "FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    return {
        "rows": rows,
        "latest_ts": latest,
        "schema_sha256": hashlib.sha256(repr(schema).encode()).hexdigest(),
        "sqlite_quick_check": "ok",
    }


def verify_copy(folder: Path, item: dict, expected: str) -> int:
    """Check snapshot identity, SHA256 and immutable SQLite state."""
    if not isinstance(item, dict) or item.get("filename") != expected:
        raise Rejected("UNEXPECTED_SNAPSHOT_IDENTITY")
    path = folder / expected
    private_file(path)
    digest = item.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", digest
    ):
        raise Rejected("INVALID_SHA256_MANIFEST")
    if item.get("bytes") != path.stat().st_size:
        raise Rejected("SNAPSHOT_SIZE_MISMATCH")
    if hash_file(path) != digest:
        raise Rejected("SNAPSHOT_SHA256_MISMATCH")
    actual = sqlite_state(path)
    if actual != item.get("state") or actual["rows"] <= 0:
        raise Rejected("SNAPSHOT_STATE_MISMATCH")
    if hash_file(path) != digest:
        raise Rejected("SNAPSHOT_CHANGED_DURING_VERIFICATION")
    return actual["rows"]


def verify(folder: Path) -> dict:
    """Never infer physical independence or final migration readiness."""
    private_file(folder, directory=True)
    if {item.name for item in folder.iterdir()} != FILES:
        raise Rejected("EXTRA_OR_MISSING_BACKUP_FILES")
    manifest = read_manifest(folder / "manifest.json")
    items = manifest.get("snapshots")
    if not isinstance(items, list) or len(items) != 2:
        raise Rejected("EXPECTED_TWO_SNAPSHOTS")
    rows = [
        verify_copy(folder, item, name)
        for name, item in zip(
            ("current-A.sqlite3", "current-B.sqlite3"), items
        )
    ]
    return {
        "status": "PASS_LOCAL_COPY_READ_ONLY",
        "snapshot_A_rows": rows[0],
        "snapshot_B_rows": rows[1],
        "files": len(FILES),
        "sidecar_files_created": False,
        "physical_failure_domains": 1,
        "cutover_snapshot": False,
        "production_modified": False,
        "release_decision": "NO_GO",
    }


def main() -> int:
    """Default inert; a verification never approves release by itself."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if not args.verify:
        print("DRY_RUN: no files opened")
        return 0
    if args.backup_dir is None:
        print("BLOCKED: --backup-dir is required")
        return 2
    try:
        result = verify(args.backup_dir)
    except (Rejected, OSError, sqlite3.Error, ValueError, TypeError) as exc:
        print("FAIL_LOCAL_COPY", type(exc).__name__, str(exc)[:100])
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
