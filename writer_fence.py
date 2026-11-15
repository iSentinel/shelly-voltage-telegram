"""Optional fail-closed B/W lifetime fence for the Shelly writer.

Default inert. Explicit required-g also uses short G SH admission.
No lock file or directory is ever created or replaced by the writer.
"""

from __future__ import annotations

import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LOCK_DIR = Path("/run/shelly-owner")
_TRUSTED_UID = 0  # Production requires root-owned B/W mount inodes.
_VALID_MODES = {"", "off", "required", "required-g"}


class WriterFenceError(RuntimeError):
    """A required single-writer or maintenance lock is unsafe."""


@dataclass(frozen=True)
class WriterLease:
    """Hold both lock file descriptions until the monitored writer exits."""

    directory: Path
    directory_fd: int
    b_fd: int
    w_fd: int
    g_fd: int | None = None
    mode: str = "required"

    def validate(self) -> None:
        """Fail if any pathname now refers to a different lock inode."""
        _compare_inode(self.directory, self.directory_fd)
        _compare_inode("B", self.b_fd, self.directory_fd)
        _compare_inode("W", self.w_fd, self.directory_fd)
        if self.g_fd is not None:
            _compare_inode("G", self.g_fd, self.directory_fd)


_ACTIVE: WriterLease | None = None


def _check_metadata(info: os.stat_result, kind: str, mode: int) -> None:
    """Require root ownership and the exact non-writable group mode."""
    actual_kind = (
        stat.S_ISDIR(info.st_mode)
        if kind == "directory"
        else stat.S_ISREG(info.st_mode)
    )
    if not actual_kind:
        raise WriterFenceError("owner_fence_wrong_file_type")
    if info.st_uid != _TRUSTED_UID or info.st_gid != os.getegid():
        raise WriterFenceError("owner_fence_wrong_owner_or_group")
    if stat.S_IMODE(info.st_mode) != mode:
        raise WriterFenceError("owner_fence_unsafe_permissions")
    if kind == "file" and info.st_nlink != 1:
        raise WriterFenceError("owner_fence_link_count_not_one")


def _compare_inode(
    name: Path | str, fd: int, parent_fd: int | None = None
) -> None:
    """Reject pathname replacements and symlinks after lock acquisition."""
    try:
        actual = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(fd)
    except OSError as exc:
        raise WriterFenceError("owner_fence_path_unavailable") from exc
    if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
        raise WriterFenceError("owner_fence_inode_replaced")
    kind = "directory" if parent_fd is None else "file"
    _check_metadata(actual, kind, 0o750 if parent_fd is None else 0o640)


def _open_directory(path: Path) -> int:
    """Open the fixed root-managed directory, refusing a symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        _check_metadata(os.fstat(fd), "directory", 0o750)
        _compare_inode(path, fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _open_lock(parent_fd: int, name: str) -> int:
    """Open one existing root-managed lock without following links."""
    fd = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=parent_fd,
    )
    try:
        _check_metadata(os.fstat(fd), "file", 0o640)
        _compare_inode(name, fd, parent_fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _acquire_lock(fd: int, exclusive: bool) -> None:
    """Acquire B SH or W EX without blocking startup or polling."""
    mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise WriterFenceError("owner_fence_already_held") from exc


def _validate_mode(mode: str) -> None:
    """Reject unexpected mode values instead of silently running open."""
    if mode not in _VALID_MODES:
        raise WriterFenceError("owner_fence_invalid_mode")


def _acquire_owner_lease(
    directory: Path, folder: int, mode: str, stack: ExitStack
) -> WriterLease:
    """Acquire G admission when requested, then lifetime B/W owner locks."""
    g_fd = None
    if mode == "required-g":
        g_fd = _open_lock(folder, "G")
        stack.callback(os.close, g_fd)
        _acquire_lock(g_fd, exclusive=False)
    b_fd = _open_lock(folder, "B")
    stack.callback(os.close, b_fd)
    w_fd = _open_lock(folder, "W")
    stack.callback(os.close, w_fd)
    opened = [b_fd, w_fd]
    if g_fd is not None:
        opened.append(g_fd)
    inodes = {
        (os.fstat(handle).st_dev, os.fstat(handle).st_ino) for handle in opened
    }
    if len(inodes) != len(opened):
        raise WriterFenceError("owner_fence_same_inode")
    _acquire_lock(b_fd, exclusive=False)
    _acquire_lock(w_fd, exclusive=True)
    if g_fd is not None:
        fcntl.flock(g_fd, fcntl.LOCK_UN)
    lease = WriterLease(directory, folder, b_fd, w_fd, g_fd, mode)
    lease.validate()
    return lease


@contextmanager
def writer_fence(
    mode: str, directory: Path = DEFAULT_LOCK_DIR
) -> Iterator[WriterLease | None]:
    """Retain stable B SH and W EX until the outer writer scope exits."""
    global _ACTIVE
    _validate_mode(mode)
    if mode in ("", "off"):
        yield None
        return
    if _ACTIVE is not None:
        raise WriterFenceError("owner_fence_duplicate_scope")
    with ExitStack() as stack:
        folder = _open_directory(directory)
        stack.callback(os.close, folder)
        lease = _acquire_owner_lease(directory, folder, mode, stack)
        _ACTIVE = lease
        try:
            yield lease
        finally:
            _ACTIVE = None


def assert_sqlite_writer_fenced() -> None:
    """Before each SQLite connect, verify the required lease still exists."""
    mode = os.getenv("SHELLY_WRITER_FENCE", "")
    _validate_mode(mode)
    if _ACTIVE is not None:
        if mode != _ACTIVE.mode:
            raise WriterFenceError("owner_fence_mode_changed_while_active")
        _ACTIVE.validate()
        return
    if mode in {"required", "required-g"}:
        raise WriterFenceError("owner_fence_missing_at_database_open")
