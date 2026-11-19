"""Fail-closed cross-process SQLite writer and update coordination."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


class WriterFenceError(RuntimeError):
    """An unsafe competing writer or update prevented database access."""


def _lock_file(path: Path) -> int:
    """Open only a regular, singly-linked lock inode."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise WriterFenceError("SVT_LOCK_PATH_UNSAFE")
    return fd


def _take_lock(fd: int, timeout: float) -> None:
    """Acquire an exclusive advisory lock with a bounded deadline."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError as exc:
            if time.monotonic() >= deadline:
                raise WriterFenceError("SVT_WRITER_LOCK_BUSY") from exc
            time.sleep(0.05)


def _check_pending(directory: Path) -> None:
    """Reject update markers, including linked or irregular files."""
    marker = directory / ".svt-update-pending"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WriterFenceError("SVT_PENDING_MARKER_UNSAFE")
    raise WriterFenceError("SVT_UPDATE_PENDING")


def _transaction_fd(path: Path, timeout: float = 5.0) -> int:
    """Check the update marker while holding the shared transaction gate."""
    fd = _lock_file(path.parent / ".svt-transaction.lock")
    try:
        _take_lock(fd, timeout)
        _check_pending(path.parent)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def transaction_gate(path: Path) -> Iterator[None]:
    """Hold the same transaction gate used by database connections."""
    fd = _transaction_fd(path)
    try:
        yield
    finally:
        os.close(fd)


@contextmanager
def writer_lease(path: Path) -> Iterator[None]:
    """Allow only one monitoring process to own a database at a time."""
    fd = _lock_file(path.parent / ".svt-owner.lock")
    try:
        _take_lock(fd, 0.0)
        with transaction_gate(path):
            pass
        yield
    finally:
        os.close(fd)


class FencedConnection(sqlite3.Connection):
    """Release the cross-process gate only after SQLite closes."""

    _svt_gate_fd: int | None = None

    def close(self) -> None:
        """Close the SQLite handle before releasing the transaction lock."""
        try:
            super().close()
        finally:
            fd = self._svt_gate_fd
            self._svt_gate_fd = None
            if fd is not None:
                os.close(fd)

    def __del__(self) -> None:
        """Release the gate if a caller forgot to close its connection."""
        with suppress(Exception):
            self.close()


def fenced_connect(path: Path, timeout: float = 10.0) -> FencedConnection:
    """Hold the update gate through the entire SQLite transaction lifetime."""
    fd = _transaction_fd(path)
    try:
        db = sqlite3.connect(
            path,
            timeout=timeout,
            factory=FencedConnection,
        )
        db._svt_gate_fd = fd
        return db
    except BaseException:
        os.close(fd)
        raise
