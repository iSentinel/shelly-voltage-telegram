"""Rehearse online EM-50 SQLite backup parity in a disposable QA sandbox."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

GUARD_PATH = Path(__file__).with_name("em50-predeploy-backup-guard.py")


def backup_guard():
    """Load the exact release parity validator, not a second implementation."""

    spec = importlib.util.spec_from_file_location(
        "em50_rehearsal_guard", GUARD_PATH
    )
    if spec is None or spec.loader is None:
        raise ValueError("backup parity guard is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_source(path: Path) -> sqlite3.Connection:
    """Open an existing single-linked SQLite source strictly read-only."""

    if not path.is_file() or path.is_symlink():
        raise ValueError("source SQLite missing or symbolic link")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("source SQLite is not an ordinary single file")
    uri = "file:" + quote(str(path.resolve())) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def online_snapshot(source: Path, destination: Path) -> None:
    """Use SQLite online backup without writing to the original database."""

    previous = os.umask(0o077)
    try:
        with (
            closing(open_source(source)) as db,
            closing(sqlite3.connect(str(destination))) as copy,
        ):
            db.backup(copy, pages=256, sleep=0.01)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("QA SQLite snapshot integrity failed")
    finally:
        os.umask(previous)


def compress_snapshot(source: Path, folder: Path) -> None:
    """Write a 0600 gzip archive with a pinned-path SHA256 sidecar."""

    archive = folder / "voltage-qa-online.sqlite3.gz"
    with source.open("rb") as reading, gzip.open(archive, "wb") as writing:
        while chunk := reading.read(1024 * 1024):
            writing.write(chunk)
    archive.chmod(0o600)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = Path(str(archive) + ".sha256")
    manifest.write_text(f"{digest}  {archive}\n", encoding="utf-8")
    manifest.chmod(0o600)


def rehearsal(database: Path, after_snapshot=None) -> dict:
    """Validate temporary backup parity and always remove sensitive copies."""

    guard = backup_guard()
    with tempfile.TemporaryDirectory(prefix="shelly-em50-rehearsal-") as temp:
        folder = Path(temp)
        folder.chmod(0o700)
        snapshot = folder / "snapshot.sqlite3"
        online_snapshot(database, snapshot)
        compress_snapshot(snapshot, folder)
        snapshot.unlink()
        if after_snapshot is not None:
            after_snapshot()
        report = guard.inspect(
            SimpleNamespace(
                db=str(database), backup_dir=str(folder), max_age_minutes=30
            ),
            time.time(),
        )
    return {
        "status": report["status"],
        "reason_codes": report["reason_codes"],
        "record_gap": report["record_gap"],
        "measurements_match": report["all_measurements_match"],
        "outages_match": report["all_outages_match"],
        "status_metadata_matches": report["status_metadata_matches"],
        "temporary_copies_removed": not folder.exists(),
        "wrote_to_production": False,
        "release_authorized": False,
    }


def main() -> int:
    """Keep a clean dry-run and report only sanitized QA statuses."""

    cli = argparse.ArgumentParser(description=__doc__)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    cli.add_argument(
        "--db",
        type=Path,
        default=Path("/var/lib/shelly-voltage/voltage.sqlite3"),
    )
    cli.add_argument("--json", action="store_true")
    options = cli.parse_args()
    if options.dry_run:
        print("PASS DRY_RUN source_unread=true no_files_created=true")
        return 0
    try:
        report = rehearsal(options.db)
        if options.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(f"{report['status']} isolated_snapshot_parity")
        return 0 if report["status"] == "PASS" else 2
    except (OSError, sqlite3.DatabaseError, ValueError, TypeError):
        print("FAIL snapshot_rehearsal validation_error", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
