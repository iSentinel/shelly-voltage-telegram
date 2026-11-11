#!/usr/bin/env python3
"""Capture full stopped-writer source+SQLite checkpoints A and B."""

from __future__ import annotations

import argparse
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
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED_VERSION = "0.5.15-hw"


class SnapshotBlocked(Exception):
    """A safe failure code that cannot reveal Telegram secrets."""


def module(filename: str, alias: str):
    """Load reviewed guards while keeping their CLI inactive."""

    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise SnapshotBlocked("GUARD_UNAVAILABLE")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def args() -> argparse.Namespace:
    """Require a named phase and explicit opt-in to create an archive."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("A", "B"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--owner-approval-file", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def safe_path(path: Path) -> bool:
    """Reject noncanonical, linked and relative sensitive paths."""

    if not path.is_absolute() or ".." in path.parts or "." in path.parts:
        return False
    return not any(part.is_symlink() for part in (path, *path.parents))


def paths_overlap(one: Path, other: Path) -> bool:
    """Disallow destinations enclosing or nested inside protected trees."""

    return (
        one == other or one.is_relative_to(other) or other.is_relative_to(one)
    )


def regular_file(path: Path) -> bool:
    """Require one independent file without symlink traversal."""

    if not safe_path(path):
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_nlink == 1


def assert_quiescent(confirmation, before: bool = False) -> None:
    """Require stopped writer and disabled automatic service restarts."""

    if not confirmation.writer_stopped():
        label = "WRITER_NOT_STOPPED" if before else "WRITER_RESTARTED"
        raise SnapshotBlocked(label)
    if not confirmation.restart_fenced():
        label = "RESTART_FENCE_MISSING" if before else "RESTART_FENCE_LOST"
        raise SnapshotBlocked(label)


def preconditions(options) -> tuple[int, str]:
    """Require protected approval, exact release and stopped writer."""

    if not all(
        safe_path(p)
        for p in (options.source, options.database, options.output_root)
    ):
        raise SnapshotBlocked("PATH_UNSAFE")
    if paths_overlap(options.output_root, options.source) or paths_overlap(
        options.output_root, options.database.parent
    ):
        raise SnapshotBlocked("BACKUP_OVERLAPS_LIVE_DATA")
    guard = module("graphic-rollout-readiness.py", "snapshot_owner_gate")
    if not guard.owner_approval_verified(options.owner_approval_file):
        raise SnapshotBlocked("OWNER_APPROVAL_INVALID")
    version = options.source / "VERSION"
    if not regular_file(version):
        raise SnapshotBlocked("SOURCE_VERSION_UNSAFE")
    if version.read_text().strip() != EXPECTED_VERSION:
        raise SnapshotBlocked("WRONG_INSTALLED_VERSION")
    if not regular_file(options.database):
        raise SnapshotBlocked("DATABASE_UNSAFE")
    confirmation = module(
        "graphic-confirm-legacy-text.py", "snapshot_writer_gate"
    )
    assert_quiescent(confirmation, before=True)
    return status_identity(options.database, options.phase)


def status_identity(database: Path, phase: str) -> tuple[int, str]:
    """Verify the expected pre/post Telegram text state without IDs."""

    try:
        with closing(
            sqlite3.connect(
                database.as_uri() + "?mode=ro", uri=True, timeout=5
            )
        ) as db:
            if db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise SnapshotBlocked("DATABASE_CORRUPT")
            data = dict(db.execute("select key,value from meta").fetchall())
            count = db.execute("select count(*) from measurements").fetchone()[
                0
            ]
    except sqlite3.Error:
        raise SnapshotBlocked("DATABASE_UNAVAILABLE") from None
    if any(
        key in data
        for key in (
            "telegram_graphic_publish_pending",
            "telegram_legacy_text_confirm_pending",
        )
    ):
        raise SnapshotBlocked("UNRESOLVED_TELEGRAM_STATUS")
    if count <= 0 or not re.fullmatch(
        r"[1-9][0-9]*", data.get("telegram_status_message_id", "")
    ):
        raise SnapshotBlocked("STATUS_HISTORY_INVALID")
    marker = data.get("telegram_status_media_identity")
    if phase == "A" and marker is not None:
        raise SnapshotBlocked("PRE_CONFIRMATION_MARKER_ALREADY_PRESENT")
    if phase == "B" and not valid_text_marker(
        marker, data["telegram_status_message_id"]
    ):
        raise SnapshotBlocked("CONFIRMED_TEXT_IDENTITY_REQUIRED")
    return int(count), phase


def valid_text_marker(value: str | None, message_id: str) -> bool:
    """Require exact chat and previously persisted Telegram message ID."""

    if not isinstance(value, str):
        return False
    parts = value.split(":")
    chat = os.environ.get("TELEGRAM_STATUS_CHAT_ID", "")
    return (
        len(parts) == 3
        and parts[0] == "text"
        and parts[1] == chat
        and parts[2] == message_id
        and bool(re.fullmatch(r"-?[0-9]{1,20}", chat))
    )


def gzip_source(source: Path, output: Path) -> None:
    """Stream filtered source to gzip without capturing private bytes."""

    with (
        output.open("xb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
    ):
        proc = subprocess.Popen(
            ["bash", str(ROOT / "source-tar.sh"), str(source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        shutil.copyfileobj(proc.stdout, gz)
        proc.stdout.close()
        if proc.wait(timeout=90) != 0:
            raise SnapshotBlocked("SOURCE_ARCHIVE_FAILED")


def tar_digest(stream) -> str:
    """Hash bounded raw tar bytes without extraction or modification."""

    digest = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(block)
        if size > 600 * 1024 * 1024:
            raise SnapshotBlocked("SOURCE_PARITY_UNAVAILABLE")
        digest.update(block)
    return digest.hexdigest()


def fresh_source_digest(current: Path) -> str:
    """Reap a filtered-tar child even when reading its pipe fails."""

    proc = subprocess.Popen(
        ["bash", str(ROOT / "source-tar.sh"), str(current)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        if proc.stdout is None:
            raise SnapshotBlocked("SOURCE_PARITY_UNAVAILABLE")
        with proc.stdout:
            digest = tar_digest(proc.stdout)
        if proc.wait(timeout=90) != 0:
            raise SnapshotBlocked("SOURCE_ARCHIVE_MISMATCH")
        return digest
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=6)


def source_parity(archive: Path, current: Path) -> None:
    """Compare archived and current filtered source without lingering jobs."""

    try:
        with gzip.open(archive, "rb") as stored:
            archived = tar_digest(stored)
        live = fresh_source_digest(current)
        if archived != live:
            raise SnapshotBlocked("SOURCE_ARCHIVE_MISMATCH")
    except (OSError, EOFError, subprocess.TimeoutExpired):
        raise SnapshotBlocked("SOURCE_PARITY_UNAVAILABLE") from None


def gzip_sqlite(source: Path, output: Path, folder: Path) -> None:
    """Capture a consistent complete SQLite backup and compress it."""

    plain = folder / ".complete.sqlite3"
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db,
        closing(sqlite3.connect(plain)) as copied,
    ):
        db.backup(copied)
    with (
        plain.open("rb") as original,
        gzip.open(output, "xb", compresslevel=6) as compressed,
    ):
        shutil.copyfileobj(original, compressed)
    plain.unlink()


def checksum(path: Path, final_path: Path) -> None:
    """Use an exact-archive-path SHA256 manifest compatible with rollback."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    sidecar = Path(str(path) + ".sha256")
    sidecar.write_text(
        f"{digest.hexdigest()}  {final_path}\n", encoding="ascii"
    )
    sidecar.chmod(0o600)


def validate(output: Path, source: Path, database: Path) -> None:
    """Verify both validators and complete SQLite parity before commit."""

    cmds = (
        ["python3", str(ROOT / "validate-source-archive.py"), str(source)],
        [
            "python3",
            str(ROOT / "validate-sqlite-archive.py"),
            "--archive",
            str(output),
            "--compare-db",
            str(database),
        ],
    )
    for command in cmds:
        result = subprocess.run(
            command, capture_output=True, timeout=120, check=False
        )
        if result.returncode != 0:
            raise SnapshotBlocked("SNAPSHOT_VALIDATION_FAILED")


def build_archives(
    options, staging: Path, final: Path, count: int, version: str
) -> None:
    """Write private source, SQLite, checksums and the release manifest."""

    source = staging / "source.tar.gz"
    sqlite = staging / "voltage-cutover.sqlite3.gz"
    gzip_source(options.source, source)
    gzip_sqlite(options.database, sqlite, staging)
    for path in (source, sqlite):
        path.chmod(0o600)
        checksum(path, final / path.name)
    confirmation = module(
        "graphic-confirm-legacy-text.py", "snapshot_final_writer_gate"
    )
    assert_quiescent(confirmation)
    after, _ = status_identity(options.database, options.phase)
    if after != count:
        raise SnapshotBlocked("DATABASE_CHANGED_DURING_CAPTURE")
    if (options.source / "VERSION").read_text().strip() != version:
        raise SnapshotBlocked("SOURCE_CHANGED_DURING_CAPTURE")
    validate(sqlite, source, options.database)
    source_parity(source, options.source)
    manifest = staging / "manifest.env"
    manifest.write_text(
        f"from_version={version}\nto_version=0.5.16-hw\n"
        f"cutover_phase={options.phase}\n",
        encoding="ascii",
    )
    manifest.chmod(0o600)


def final_publication_preflight(
    options, staging: Path, count: int, version: str
) -> None:
    """Refuse a stale archive even after initial validation succeeded."""

    confirmation = module(
        "graphic-confirm-legacy-text.py", "snapshot_publish_writer_gate"
    )
    assert_quiescent(confirmation)
    after, _ = status_identity(options.database, options.phase)
    if after != count:
        raise SnapshotBlocked("DATABASE_CHANGED_BEFORE_PUBLICATION")
    if (options.source / "VERSION").read_text().strip() != version:
        raise SnapshotBlocked("SOURCE_CHANGED_BEFORE_PUBLICATION")
    validate(
        staging / "voltage-cutover.sqlite3.gz",
        staging / "source.tar.gz",
        options.database,
    )
    source_parity(staging / "source.tar.gz", options.source)
    assert_quiescent(confirmation)


def capture(options, count: int) -> dict:
    """Atomically publish one private, fully validated rollback snapshot."""

    base = options.output_root
    if base.exists() and (
        not base.is_dir()
        or base.stat().st_uid != os.geteuid()
        or stat.S_IMODE(base.stat().st_mode) != 0o700
    ):
        raise SnapshotBlocked("OUTPUT_ROOT_UNSAFE")
    base.mkdir(parents=True, mode=0o700, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".graphic-stage-", dir=base))
    try:
        version = (options.source / "VERSION").read_text().strip()
        final = base / (
            datetime.now().strftime("%Y%m%d-%H%M%S")
            + f"-{version}-PR300-{options.phase}-{staging.name[-8:]}"
        )
        if final.exists():
            raise SnapshotBlocked("SNAPSHOT_TARGET_ALREADY_EXISTS")
        build_archives(options, staging, final, count, version)
        final_publication_preflight(options, staging, count, version)
        staging.rename(final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "status": "PASS",
        "phase": options.phase,
        "archive": str(final),
        "measurement_rows": count,
        "source_and_sqlite_verified": True,
    }


def run(options) -> dict:
    """Default to inspection; only --execute may create any file."""

    count, phase = preconditions(options)
    if not options.execute:
        return {
            "status": "DRY_RUN_PASS",
            "phase": phase,
            "measurement_rows": count,
            "archive_created": False,
        }
    return capture(options, count)


def main() -> int:
    """Emit redacted and auditable PASS/BLOCKED JSONL results."""

    options = args()
    try:
        result = run(options)
    except SnapshotBlocked as error:
        result = {
            "status": "BLOCKED",
            "reason": str(error),
            "archive_created": False,
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        result = {
            "status": "BLOCKED",
            "reason": "SNAPSHOT_FAILED",
            "archive_created": False,
        }
    if options.jsonl:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print("GRAPHIC SNAPSHOT: " + result["status"])
    return 0 if result["status"] in {"PASS", "DRY_RUN_PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
