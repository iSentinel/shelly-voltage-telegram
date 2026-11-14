#!/usr/bin/env python3
"""Exercise a stopped-writer A/B source+SQLite snapshot only in private QA."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

MARKER = ".writer-fence-qa"
MAGIC = "DISPOSABLE_WRITER_FENCE_SNAPSHOT_QA\n"
SOURCE_ARCHIVER = Path(__file__).with_name("source-tar.sh")
SOURCE_VALIDATOR = Path(__file__).with_name("validate-source-archive.py")


class Blocked(Exception):
    """Fail-closed QA error with no private state exposed."""


def safe_root(root: Path) -> None:
    """Refuse production paths, aliases and unmarked scratch directories."""
    if not root.is_absolute() or ".." in root.parts:
        raise Blocked("QA_ROOT_UNSAFE")
    if root.parts[:2] != ("/", "tmp") and (
        root.parts[:3] != ("/", "var", "tmp")
    ):
        raise Blocked("PRODUCTION_PATH_FORBIDDEN")
    minimum = 3 if root.parts[:2] == ("/", "tmp") else 4
    if len(root.parts) < minimum:
        raise Blocked("QA_ROOT_UNSAFE")
    path = Path("/")
    for part in root.parts[1:]:
        path /= part
        if path.is_symlink():
            raise Blocked("QA_LINKED_ROOT")
    if not root.is_dir():
        raise Blocked("QA_ROOT_MISSING")
    info = root.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Blocked("QA_ROOT_NOT_PRIVATE")
    marker = root / MARKER
    if marker.is_symlink() or not marker.is_file():
        raise Blocked("QA_MARKER_MISSING")
    if marker.read_text(encoding="ascii") != MAGIC:
        raise Blocked("QA_MARKER_INVALID")


def source_version(source: Path) -> str:
    """Read matching safe source and release version markers."""
    if not source.is_dir() or source.is_symlink():
        raise Blocked("QA_SOURCE_UNSAFE")
    markers = []
    for name in ("VERSION", "RELEASE_VERSION"):
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise Blocked("QA_SOURCE_MARKER_UNSAFE")
        if path.stat().st_nlink != 1 or path.stat().st_size > 128:
            raise Blocked("QA_SOURCE_MARKER_UNSAFE")
        markers.append(path.read_text(encoding="ascii").strip())
    if markers[0] != markers[1] or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", markers[0]
    ):
        raise Blocked("QA_SOURCE_VERSION_MISMATCH")
    return markers[0]


def db_digest(path: Path) -> str:
    """Hash all SQL rows and persistent header fields without writing."""
    if path.is_symlink() or not path.is_file():
        raise Blocked("QA_SQLITE_UNSAFE")
    if path.stat().st_nlink != 1:
        raise Blocked("QA_SQLITE_LINKED")
    uri = f"file:{path}?mode=ro"
    digest = hashlib.sha256()
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("QA_SQLITE_INTEGRITY")
        for line in db.iterdump():
            data = line.encode("utf-8")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        for pragma in ("user_version", "application_id"):
            value = db.execute(f"PRAGMA {pragma}").fetchone()[0]
            digest.update(f"{pragma}={value}\n".encode("ascii"))
    return digest.hexdigest()


def sync_directory(path: Path) -> None:
    """Sync a QA directory after writing or renaming snapshot metadata."""
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sync_file(path: Path) -> None:
    """Flush a completed private snapshot before its metadata is published."""
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def sha256(path: Path) -> str:
    """Hash a complete private archive."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


@contextmanager
def fence(root: Path) -> Iterator[None]:
    """Model a cooperative writer fence with an exclusive POSIX lock."""
    path = root / ".writer.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or (
            info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise Blocked("QA_FENCE_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked("QA_WRITER_STILL_ACTIVE") from exc
        yield
    finally:
        os.close(descriptor)


def archive_source(source: Path, destination: Path) -> None:
    """Compress a filtered release tar using a bounded scratch stream."""
    with tempfile.TemporaryFile() as tar:
        result = subprocess.run(
            ["bash", str(SOURCE_ARCHIVER), str(source)],
            stdout=tar,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise Blocked("QA_SOURCE_ARCHIVE_FAILED")
        tar.seek(0)
        with (
            destination.open("xb") as output,
            gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as gz,
        ):
            shutil.copyfileobj(tar, gz, length=1024 * 1024)
    destination.chmod(0o600)
    sync_file(destination)


def verify_source(archive: Path) -> None:
    """Reuse the project's strict source archive verifier."""
    spec = importlib.util.spec_from_file_location(
        "source_archive_qa", SOURCE_VALIDATOR
    )
    if spec is None or spec.loader is None:
        raise Blocked("QA_SOURCE_VALIDATOR_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_members(archive)
    module.check_compressed_bytes(archive)


def copy_sqlite(source: Path, destination: Path) -> None:
    """Take a transactionally consistent backup from a fenced QA DB."""
    plain = destination.with_suffix("")
    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(plain)) as saved,
    ):
        src.backup(saved)
    if db_digest(plain) != db_digest(source):
        raise Blocked("QA_DATABASE_CHANGED_DURING_BACKUP")
    with (
        plain.open("rb") as inp,
        destination.open("xb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as gz,
    ):
        shutil.copyfileobj(inp, gz, length=1024 * 1024)
    plain.unlink()
    destination.chmod(0o600)
    sync_file(destination)


def copy_pair(root: Path, stage: Path, version: str) -> None:
    """Capture identical source and database state twice under one fence."""
    source = root / "source"
    database = root / "history.sqlite3"
    before = db_digest(database)
    source_hashes, db_hashes = [], []
    archive_hashes = {}
    for label in ("A", "B"):
        dest = stage / label
        dest.mkdir(mode=0o700)
        archive = dest / "source.tar.gz"
        backup = dest / "history.sqlite3.gz"
        archive_source(source, archive)
        verify_source(archive)
        copy_sqlite(database, backup)
        source_hashes.append(sha256(archive))
        archive_hashes[label] = sha256(backup)
        sync_directory(dest)
        with tempfile.TemporaryDirectory(dir=dest) as scratch:
            restored = Path(scratch) / "history.sqlite3"
            with gzip.open(backup, "rb") as src:
                restored.write_bytes(src.read())
            db_hashes.append(db_digest(restored))
    if (
        source_hashes[0] != source_hashes[1]
        or db_hashes != [before, before]
        or db_digest(database) != before
        or source_version(source) != version
    ):
        raise Blocked("QA_EXACT_PARITY_FAILED")
    write_manifest(stage, version, source_hashes[0], before, archive_hashes)


def write_manifest(
    stage: Path,
    version: str,
    source_hash: str,
    db_hash: str,
    archives: dict[str, str],
) -> None:
    """Persist private parity evidence after both copies have validated."""
    manifest = {
        "source_version": version,
        "source_sha256": source_hash,
        "whole_sqlite_digest": db_hash,
        "archive_sha256": archives,
        "fence_proof": "COOPERATIVE_QA_LOCK_ONLY",
        "production_release_authorized": False,
    }
    path = stage / "manifest.json"
    with path.open("x") as handle:
        json.dump(manifest, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    path.chmod(0o600)
    sync_directory(stage)


def do_capture(root: Path) -> None:
    """Publish a private QA A/B pair only after both copies validate."""
    output = root / "snapshots"
    if output.exists() or output.is_symlink():
        raise Blocked("QA_SNAPSHOT_EXISTS")
    version = source_version(root / "source")
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=root) as temp:
        stage = Path(temp)
        copy_pair(root, stage, version)
        stage.rename(output)
        sync_directory(root)
    print("QA A/B SNAPSHOT PASS; source_version=", version)


def main() -> int:
    """Run read-only dry run or a disposable A/B capture."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--capture-disposable", action="store_true")
    args = parser.parse_args()
    try:
        safe_root(args.root)
        with fence(args.root):
            version = source_version(args.root / "source")
            db_digest(args.root / "history.sqlite3")
            if args.capture_disposable:
                do_capture(args.root)
            else:
                print(f"QA SNAPSHOT DRY-RUN PASS version={version}")
    except (Blocked, OSError, sqlite3.Error, ValueError, tarfile.TarError):
        print("QA A/B SNAPSHOT: BLOCKED", file=sys.stderr)
        return 3
    print("Production writer not inspected; no release authorization.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
