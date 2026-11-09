"""Export verified YASNO cache to a least-privilege, read-only bot handoff."""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from yasno_cache import (
    CACHE_LIMIT,
    build_cache,
    canonical_data,
    read_verified_cache,
)
from yasno_schedule import parse_schedule

HANDOFF_DIR_MODE = 0o750
HANDOFF_FILE_MODE = 0o640


def validate_target(path: Path, recipient_gid: int) -> None:
    """Require writer-owned directory with read-only recipient group."""

    if recipient_gid < 1 or recipient_gid > 65535:
        raise ValueError("recipient gid is outside the supported range")
    if any(p.is_symlink() for p in (path, path.parent, *path.parent.parents)):
        raise ValueError("handoff path contains a symlink")
    if not path.parent.is_dir():
        raise ValueError("handoff directory does not exist")
    info = path.parent.stat()
    expected = (os.geteuid(), recipient_gid, HANDOFF_DIR_MODE)
    actual = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
    if actual != expected:
        raise ValueError("handoff directory ownership or mode invalid")
    if path.exists():
        verify_file(path, recipient_gid)


def verify_file(path: Path, recipient_gid: int) -> None:
    """Forbid linked, wrong-owner or writable consumer cache files."""

    info = path.lstat()
    identity = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
    expected = (os.geteuid(), recipient_gid, HANDOFF_FILE_MODE)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("handoff file must be an unlinked regular file")
    if identity != expected or info.st_size > CACHE_LIMIT:
        raise ValueError("handoff file ownership, mode or size invalid")


def verified_envelope(
    source: str, group: str, now: datetime
) -> tuple[bytes, datetime]:
    """Retain captured time; never refresh or invent provenance on copy."""

    data, captured = read_verified_cache(source)
    parse_schedule(data, group, now, captured)
    return canonical_data(build_cache(data, captured), CACHE_LIMIT), captured


@contextmanager
def exclusive_lock(path: Path):
    """Prevent two writers from overwriting each other's cache export."""

    lock = path.with_name(path.name + ".lock")
    if lock.is_symlink():
        raise ValueError("handoff lock may not be a symlink")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(lock, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("handoff lock must be a single regular file")
        if (info.st_uid, stat.S_IMODE(info.st_mode)) != (os.geteuid(), 0o600):
            raise ValueError("handoff lock ownership or mode invalid")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another handoff is running") from exc
        yield
    finally:
        os.close(fd)


def atomic_export(
    path: Path, raw: bytes, recipient_gid: int, captured: datetime
) -> str:
    """Replace only after a full fsync using a private temp file."""

    if path.exists():
        try:
            _, previous = read_verified_cache(str(path))
            if previous > captured:
                raise ValueError("refusing to replace newer recipient cache")
        except (OSError, TypeError, KeyError):
            pass
        if path.read_bytes() == raw:
            return "SKIPPED identical handoff"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=".yasno-export-", delete=False
        ) as writer:
            temporary = Path(writer.name)
            os.fchmod(writer.fileno(), HANDOFF_FILE_MODE)
            if os.fstat(writer.fileno()).st_gid != recipient_gid:
                os.fchown(writer.fileno(), os.geteuid(), recipient_gid)
            writer.write(raw)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    verify_file(path, recipient_gid)
    return "UPDATED recipient read-only handoff"


def handoff(source: str, target: str, group: str, gid: int, mode: str) -> str:
    """Verify all provenance before either previewing or exporting."""

    path = Path(target)
    if Path(source).resolve() == path.resolve():
        raise ValueError("source and recipient paths must differ")
    validate_target(path, gid)
    raw, captured = verified_envelope(
        source, group, datetime.now(timezone.utc)
    )
    if mode == "dry-run":
        return f"DRY_RUN valid source bytes={len(raw)} no_writes=True"
    if mode == "status":
        cache, captured = read_verified_cache(target)
        parse_schedule(cache, group, datetime.now(timezone.utc), captured)
        return "STATUS valid recipient cache"
    with exclusive_lock(path):
        validate_target(path, gid)
        return atomic_export(path, raw, gid, captured)


def main() -> int:
    """Provide explicit no-write validation and single atomic export."""

    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--status", action="store_true")
    parser.add_argument("--source", required=True, help="Sealed writer cache")
    parser.add_argument(
        "--output", required=True, help="Existing 0750 directory"
    )
    parser.add_argument("--recipient-gid", required=True, type=int)
    parser.add_argument("--group", default="10.1")
    args = parser.parse_args()
    operation = (
        "install" if args.install else "status" if args.status else "dry-run"
    )
    try:
        print(
            "PASS",
            handoff(
                args.source,
                args.output,
                args.group,
                args.recipient_gid,
                operation,
            ),
        )
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"FAIL handoff: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
