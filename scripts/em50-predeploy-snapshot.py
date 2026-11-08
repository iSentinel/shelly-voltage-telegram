"""Prepare a private EM-50 SQLite snapshot, without stopping production."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import os
import secrets
import shutil
import sqlite3
import stat
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
CONFIRM = "EM50_PRIVATE_SNAPSHOT_ONLY"


def load_parity_guard():
    """Reuse the same entire-history parity checker as release preflight."""

    source = ROOT / "scripts/em50-predeploy-backup-guard.py"
    spec = importlib.util.spec_from_file_location("em50_backup_guard", source)
    if spec is None or spec.loader is None:
        raise ValueError("backup parity guard is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require_source(path: Path) -> None:
    """Reject linked or missing live databases before opening SQLite."""

    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("source path contains a symlink")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("source is not an ordinary SQLite file")


def require_directory(path: Path) -> None:
    """Only accept an explicitly provisioned owner-only destination."""

    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("destination contains a symlink")
    info = path.stat()
    if (
        not path.is_dir()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("snapshot destination must be owner-only 0700")


def online_copy(source: Path, destination: Path) -> None:
    """Create a mode-0600 backup from a read-only SQLite source."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(destination, flags, 0o600)
    os.close(descriptor)
    source_url = "file:" + quote(str(source.resolve())) + "?mode=ro"
    target_url = "file:" + quote(str(destination.resolve())) + "?mode=rw"
    try:
        with (
            closing(
                sqlite3.connect(source_url, uri=True, timeout=5)
            ) as current,
            closing(sqlite3.connect(target_url, uri=True)) as snapshot,
        ):
            current.backup(snapshot, pages=128, sleep=0.03)
            if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("online copy failed SQLite integrity")
    except (OSError, ValueError, sqlite3.Error):
        destination.unlink(missing_ok=True)
        raise


def equivalent(current: dict, saved: dict) -> bool:
    """Compare complete histories, outage records and Telegram status."""

    return all(
        (
            current["whole_digest"] == saved["whole_digest"],
            current["measurements_digest"] == saved["measurements_digest"],
            current["outages_digest"] == saved["outages_digest"],
            current["rows"] == saved["rows"],
            current["last"] == saved["last"],
            current["outage"] == saved["outage"],
            bool(current["status"]),
            current["status"] == saved["status"],
        )
    )


def gzip_copy(source: Path, destination: Path) -> str:
    """Compress a private snapshot and return its SHA256 digest."""

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(destination, flags, 0o600)
    with source.open("rb") as inp, os.fdopen(fd, "wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb") as compressed:
            shutil.copyfileobj(inp, compressed, length=1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    with destination.open("rb") as blob:
        return hashlib.file_digest(blob, "sha256").hexdigest()


def publish(archive: Path, destination: Path, checksum: str) -> None:
    """Publish uniquely named archive and manifest without overwriting."""

    manifest = Path(str(destination) + ".sha256")
    if destination.exists() or manifest.exists():
        raise ValueError("snapshot destination already exists")
    os.link(archive, destination, follow_symlinks=False)
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        descriptor = os.open(manifest, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(f"{checksum}  {destination}" + chr(10))
            out.flush()
            os.fsync(out.fileno())
    except (OSError, ValueError):
        destination.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise


def create_snapshot(source: Path, directory: Path) -> str:
    """Make one verified snapshot; reject a source that advanced meanwhile."""

    require_source(source)
    require_directory(directory)
    guard = load_parity_guard()
    with tempfile.TemporaryDirectory(prefix=".em50-qa-", dir=directory) as tmp:
        staging = Path(tmp)
        database = staging / "snapshot.sqlite3"
        archive = staging / "snapshot.sqlite3.gz"
        online_copy(source, database)
        os.chmod(database, 0o600)
        saved = guard.sqlite_state(database)
        if not equivalent(guard.sqlite_state(source), saved):
            raise ValueError("live SQLite advanced during capture")
        checksum = gzip_copy(database, archive)
        if not equivalent(guard.sqlite_state(source), saved):
            raise ValueError("live SQLite advanced during compression")
        name = (
            datetime.now(timezone.utc).strftime("voltage-%Y%m%d-%H%M%S-")
            + secrets.token_hex(3)
            + ".sqlite3.gz"
        )
        publish(archive, directory / name, checksum)
    return "snapshot_written=true release_authorized=false"


def main() -> int:
    """Run a no-write dry-run or a separately approved private capture."""

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--capture", action="store_true")
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        source, directory = Path(args.db), Path(args.output_dir)
        require_source(source)
        require_directory(directory)
        if args.dry_run:
            print("PASS DRY_RUN no_writes=true release_authorized=false")
            return 0
        if args.confirm != CONFIRM:
            raise ValueError("snapshot capture confirmation is required")
        print("PASS", create_snapshot(source, directory))
        return 0
    except (OSError, ValueError, sqlite3.Error, TypeError) as exc:
        print(f"FAIL predeploy_snapshot {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
