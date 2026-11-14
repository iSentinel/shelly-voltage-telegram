#!/usr/bin/env python3
"""Rehearse source-only recovery in a disposable sandbox; never deploy."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path

MARKER = ".source-only-qa"
MARKER_VALUE = "DISPOSABLE_SOURCE_RECOVERY_QA\n"
PRESERVE = {"secrets", "validation", ".work-state"}
VERSION_NAMES = {"VERSION", "RELEASE_VERSION"}


class Blocked(Exception):
    """A fail-closed QA refusal with no secret values."""


def safe_path(path: Path) -> Path:
    """Refuse links in any existing path component."""
    if not path.is_absolute():
        raise Blocked("PATH_NOT_ABSOLUTE")
    current = Path("/")
    for part in path.parts[1:]:
        if part == "..":
            raise Blocked("PATH_TRAVERSAL")
        current = current / part
        if current.is_symlink():
            raise Blocked("LINKED_PATH")
    return path


def validate_target(target: Path) -> None:
    """Restrict execution to a marked, private, disposable directory."""
    safe_path(target)
    parent = target.parent
    if parent not in target.parents or target == parent:
        raise Blocked("INVALID_TARGET")
    if target.parts[:2] != ("/", "tmp") and (
        target.parts[:3] != ("/", "var", "tmp")
    ):
        raise Blocked("PRODUCTION_TARGET_FORBIDDEN")
    if len(target.parts) < 4 or not target.is_dir():
        raise Blocked("TARGET_UNAVAILABLE")
    info = target.lstat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise Blocked("TARGET_NOT_PRIVATE")
    marker = target / MARKER
    if not marker.is_file() or marker.is_symlink():
        raise Blocked("QA_MARKER_MISSING")
    if marker.read_text(encoding="ascii") != MARKER_VALUE:
        raise Blocked("QA_MARKER_INVALID")


def sha256(path: Path) -> str:
    """Hash a regular file without changing its contents."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Blocked("UNSAFE_DATABASE_OR_ARCHIVE")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def protected_name(name: str) -> bool:
    """Keep production-specific local state out of a release archive."""
    return (
        name in PRESERVE
        or name in {MARKER, "ROADMAP.md"}
        or name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or name.startswith("ROADMAP.md.pre-")
        or name.endswith(".sqlite3")
        or name.endswith(".sqlite3-wal")
        or name.endswith(".sqlite3-shm")
    )


def archive_versions(archive: tarfile.TarFile) -> str:
    """Require matching bounded source/release markers."""
    values = {}
    for entry in archive:
        name = entry.name.removeprefix("./")
        if name not in VERSION_NAMES:
            continue
        if not entry.isfile() or not 1 <= entry.size <= 128:
            raise Blocked("VERSION_INVALID")
        handle = archive.extractfile(entry)
        if handle is None or name in values:
            raise Blocked("VERSION_INVALID")
        values[name] = handle.read(129).decode("ascii").strip()
    if set(values) != VERSION_NAMES or len(set(values.values())) != 1:
        raise Blocked("VERSION_MISMATCH")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", values["VERSION"]):
        raise Blocked("VERSION_MISMATCH")
    return values["VERSION"]


def validate_archive(path: Path) -> str:
    """Reuse the project's archive validator and deny local-state members."""
    safe_path(path)
    sha256(path)
    spec = importlib.util.spec_from_file_location(
        "source_archive_qa_validator",
        Path(__file__).with_name("validate-source-archive.py"),
    )
    if spec is None or spec.loader is None:
        raise Blocked("VALIDATOR_MISSING")
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    try:
        validator.check_members(path)
        validator.check_compressed_bytes(path)
        with tarfile.open(path, "r:gz") as archive:
            for entry in archive:
                if any(
                    protected_name(part) for part in Path(entry.name).parts
                ):
                    raise Blocked("ARCHIVE_CONTAINS_PRIVATE_STATE")
        with tarfile.open(path, "r:gz") as archive:
            return archive_versions(archive)
    except (
        OSError,
        EOFError,
        ValueError,
        UnicodeError,
        tarfile.TarError,
    ) as exc:
        raise Blocked("ARCHIVE_VALIDATION_FAILED") from exc


def check_local_tree(path: Path) -> None:
    """Refuse links and special files in preserved local directories."""
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            candidate = Path(parent) / name
            info = candidate.lstat()
            if candidate.is_symlink() or (
                not stat.S_ISDIR(info.st_mode)
                and not stat.S_ISREG(info.st_mode)
            ):
                raise Blocked("UNSAFE_LOCAL_STATE")
            if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise Blocked("LINKED_LOCAL_FILE")


def preserve_local(target: Path, stage: Path) -> None:
    """Copy local QA secrets and metadata into the staged source tree."""
    for item in target.iterdir():
        if not protected_name(item.name):
            continue
        if item.is_symlink():
            raise Blocked("LINKED_PROTECTED_LOCAL_STATE")
        destination = stage / item.name
        if destination.exists():
            raise Blocked("ARCHIVE_OVERLAPS_LOCAL_STATE")
        if item.is_dir():
            check_local_tree(item)
            shutil.copytree(item, destination, symlinks=False)
        elif item.is_file():
            shutil.copy2(item, destination)
        else:
            raise Blocked("INVALID_LOCAL_STATE")


def private_parent(target: Path) -> None:
    """Require a private scratch parent for journal and source backups."""
    safe_path(target.parent)
    info = target.parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise Blocked("QA_PARENT_NOT_PRIVATE")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,64}", target.name):
        raise Blocked("QA_TARGET_NAME_INVALID")


def journal_path(target: Path) -> Path:
    """Name one transaction journal per disposable source directory."""
    private_parent(target)
    return target.parent / f".source-recovery-{target.name}.json"


def journal_write(target: Path, stage: Path, old: Path) -> None:
    """Persist source swap paths before moving any installed source."""
    path = journal_path(target)
    data = {"version": 1, "stage": stage.name, "old": old.name}
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    sync_parent(target)


def sync_parent(target: Path) -> None:
    """Synchronize the private directory's operation metadata."""
    descriptor = os.open(
        target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def journal_read(target: Path) -> tuple[Path, Path]:
    """Validate a local journal without trusting arbitrary paths."""
    path = journal_path(target)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 512
        ):
            raise Blocked("QA_JOURNAL_UNSAFE")
        with os.fdopen(descriptor, "r") as stream:
            descriptor = -1
            try:
                record = json.load(stream)
            except (ValueError, UnicodeError) as exc:
                raise Blocked("QA_JOURNAL_INVALID") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(record, dict) or record.get("version") != 1:
        raise Blocked("QA_JOURNAL_UNSAFE")
    names = (record.get("stage"), record.get("old"))
    for name, prefix in zip(names, (".source-stage-", ".source-old-")):
        if not isinstance(name, str) or not re.fullmatch(
            re.escape(prefix) + r"[A-Za-z0-9_-]{8,32}", name
        ):
            raise Blocked("QA_JOURNAL_UNSAFE")
    return (target.parent / names[0], target.parent / names[1])


def cleanup_stage(stage: Path) -> None:
    """Only remove a private scratch directory, never an external path."""
    info = stage.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        raise Blocked("QA_STAGE_UNSAFE")
    shutil.rmtree(stage)


def recover_disposable(target: Path) -> None:
    """Restore original source after a crash between directory renames."""
    stage, old = journal_read(target)
    if target.exists() and old.exists():
        raise Blocked("QA_RECOVERY_REQUIRES_REVIEW")
    if not target.exists():
        if not old.is_dir() or old.is_symlink():
            raise Blocked("QA_OLD_SOURCE_MISSING")
        validate_target(old)
        old.rename(target)
        sync_parent(target)
    else:
        validate_target(target)
    if stage.exists() or stage.is_symlink():
        cleanup_stage(stage)
    journal_path(target).unlink()
    sync_parent(target)


def install_sandbox(archive_path: Path, target: Path) -> None:
    """Record an atomic rename sequence for explicit crash recovery."""
    private_parent(target)
    if journal_path(target).exists():
        raise Blocked("QA_RECOVERY_REQUIRED")
    stage = Path(tempfile.mkdtemp(prefix=".source-stage-", dir=target.parent))
    journal_created = False
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            archive.extractall(stage, filter="data")
        preserve_local(target, stage)
        if not (stage / MARKER).is_file():
            raise Blocked("STAGE_MARKER_MISSING")
        old = Path(tempfile.mkdtemp(prefix=".source-old-", dir=target.parent))
        old.rmdir()
        journal_write(target, stage, old)
        journal_created = True
        target.rename(old)
        sync_parent(target)
        stage.rename(target)
        sync_parent(target)
        cleanup_stage(old)
        journal_path(target).unlink()
        sync_parent(target)
    finally:
        if not journal_created and stage.exists():
            cleanup_stage(stage)


@contextmanager
def qa_lock(target: Path) -> Iterator[None]:
    """Serialize sandbox changes without any production-level lock."""
    lock_path = safe_path(target.parent / ".source-only-qa.lock")
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise Blocked("QA_LOCK_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked("QA_LOCK_BUSY") from exc
        yield
    finally:
        os.close(descriptor)


def run_check(args: argparse.Namespace) -> str:
    """Run under QA lock and preserve external SQLite bytes."""
    safe_path(args.target)
    private_parent(args.target)
    safe_path(args.database)
    if args.database.is_relative_to(args.target):
        raise Blocked("DATABASE_INSIDE_TARGET")
    if args.archive.is_relative_to(args.target):
        raise Blocked("ARCHIVE_INSIDE_TARGET")
    lock = (
        qa_lock(args.target)
        if args.apply_disposable or args.recover_disposable
        else nullcontext()
    )
    with lock:
        before = sha256(args.database)
        if args.recover_disposable:
            recover_disposable(args.target)
            version = (args.target / "VERSION").read_text().strip()
        else:
            validate_target(args.target)
            version = validate_archive(args.archive)
            if args.apply_disposable:
                install_sandbox(args.archive, args.target)
        if before != sha256(args.database):
            raise Blocked("DATABASE_CHANGED")
        if (
            args.apply_disposable
            and (args.target / "VERSION").read_text().strip() != version
        ):
            raise Blocked("SOURCE_VERSION_INCORRECT")
    return version


def main() -> int:
    """Validate or perform an explicitly disposable recovery rehearsal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply-disposable", action="store_true")
    mode.add_argument("--recover-disposable", action="store_true")
    args = parser.parse_args()
    try:
        version = run_check(args)
    except (Blocked, OSError) as exc:
        print(f"SOURCE-ONLY QA FAIL: {exc}")
        return 3
    print(f"SOURCE-ONLY QA PASS: version={version}")
    print("SQLite unchanged; Docker/Telegram untouched; production writes NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
