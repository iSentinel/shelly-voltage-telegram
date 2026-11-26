#!/usr/bin/env python3
"""Rehearse a signed rollback failure using only generated SQLite fixtures."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

PAIR = Path(__file__).with_name("signed-rollback-pair-preflight.py")
FAULTS = ("after_rescue", "after_old_stage", "after_old_db_swap")


class Blocked(ValueError):
    """No recovery claim is permitted after a failed fixture check."""


class SimulatedFailure(Exception):
    """Represent an injected interruption of the disposable transaction."""


def load_pair():
    """Load the read-only signed source/OCI pair validator."""

    spec = importlib.util.spec_from_file_location(
        "rollback_pair_rehearsal", PAIR
    )
    if spec is None or spec.loader is None:
        raise Blocked("SIGNED_PAIR_GUARD_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_fixture(path: Path, rows: int) -> None:
    """Create distinct newer and historical SQLite data without secrets."""

    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY, voltage REAL);"
            "CREATE TABLE telegram_status (id INTEGER PRIMARY KEY, "
            "media_kind TEXT, message_id INTEGER);"
        )
        connection.executemany(
            "INSERT INTO measurements VALUES (?,?)",
            ((i, 220.0 + i) for i in range(1, rows + 1)),
        )
        connection.execute(
            "INSERT INTO telegram_status VALUES (1, 'photo', 1000)"
        )
        connection.commit()


def fixture_digest(path: Path) -> tuple[str, int]:
    """Hash every logical table and setting, including phase metadata."""

    digest = hashlib.sha256()
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Blocked("FIXTURE_SQLITE_CORRUPT")
        rows = db.execute("SELECT count(*) FROM measurements").fetchone()[0]
        for statement in db.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest(), rows


def sqlite_backup(source: Path, destination: Path) -> None:
    """Copy the current fixture as a WAL-consistent private SQLite rescue."""

    with (
        closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as live,
        closing(sqlite3.connect(destination)) as saved,
    ):
        live.backup(saved)
    os.chmod(destination, 0o600)


def require_equal(first: Path, second: Path) -> None:
    """Require all logical rows and PHOTO status to survive recovery."""

    if fixture_digest(first) != fixture_digest(second):
        raise Blocked("NEWER_HISTORY_PARITY_LOST")


def staged_identity(folder: Path) -> tuple[str, str]:
    """Read only synthetic staged source and protected receipt identity."""

    old = (folder / "VERSION").read_text(encoding="ascii").strip()
    release = (folder / "RELEASE_VERSION").read_text(encoding="ascii").strip()
    receipt = json.loads((folder / "deployed.json").read_text())
    override = (folder / "override.yaml").read_text()
    if release != old or receipt.get("version") != old:
        raise Blocked("STAGED_VERSION_MISMATCH")
    digest = receipt.get("digest", "")
    image = "ghcr.io/isentinel/shelly-voltage-telegram@" + digest
    if override.count("image: " + image) != 1:
        raise Blocked("STAGED_DIGEST_MISMATCH")
    return old, digest


def stage_old_pair(folder: Path, signed: Path, old_version: str) -> None:
    """Stage only verified version markers and immutable OCI metadata."""

    for name in ("VERSION", "RELEASE_VERSION"):
        (folder / name).write_text(old_version + "\n", encoding="ascii")
    for name in ("deployed.json", "override.yaml"):
        shutil.copyfile(signed / name, folder / name)
        os.chmod(folder / name, 0o600)


def sync_directory(folder: Path) -> None:
    """Persist private fixture directory entries for crash-point testing."""

    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def journal(folder: Path, phase: str) -> None:
    """Atomically fsync a synthetic phase marker and its directory."""

    temporary = folder / ".journal.new"
    flags = os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        json.dump({"phase": phase}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, folder / "journal.json")
    sync_directory(folder)


def recovery_checkpoint(folder: Path, phase: str) -> None:
    """Persist one recovery checkpoint; isolated tests may inject a kill."""

    journal(folder, phase)


def restored_candidate(folder: Path, rescued: Path) -> Path:
    """Build and verify a temporary SQLite before replacing active state."""

    candidate = folder / ".recovery-candidate.sqlite3"
    if candidate.is_symlink() or (
        candidate.exists() and not candidate.is_file()
    ):
        raise Blocked("RECOVERY_CANDIDATE_PATH_UNSAFE")
    candidate.unlink(missing_ok=True)
    sqlite_backup(rescued, candidate)
    require_equal(rescued, candidate)
    fd = os.open(candidate, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    sync_directory(folder)
    return candidate


def fail_at(folder: Path, current: str, chosen: str) -> None:
    """Stop a fixture transaction at one explicit failure boundary."""

    journal(folder, current)
    if current == chosen:
        raise SimulatedFailure(current)


def require_closed_wal_state(active: Path) -> None:
    """Refuse a fixture swap while WAL sidecars may remain in use."""

    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(active) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            raise Blocked("ACTIVE_SQLITE_WAL_SIDECAR_UNFENCED")


def recover_newer(folder: Path, newer_digest: str, version: str) -> None:
    """Restore all synthetic current data without starting a bot."""

    rescued = folder / "rescue.sqlite3"
    active = folder / "active.sqlite3"
    if not rescued.is_file() or fixture_digest(rescued)[0] != newer_digest:
        raise Blocked("RESCUE_HISTORY_MISMATCH")
    require_closed_wal_state(active)
    candidate = restored_candidate(folder, rescued)
    recovery_checkpoint(folder, "RESTORE_PREPARED")
    os.replace(candidate, active)
    sync_directory(folder)
    recovery_checkpoint(folder, "RESTORE_SWAPPED")
    require_equal(rescued, active)
    write_current_pair(folder, version)
    journal(folder, "RECOVERED_NO_BOT_START")


def recover_fenced(folder: Path, newer_digest: str, version: str) -> None:
    """Acquire a new fixture writer lease before post-SIGKILL recovery."""

    with writer_fence(folder):
        recover_newer(folder, newer_digest, version)


def write_current_pair(folder: Path, version: str) -> None:
    """Restore the fictional current version, receipt and Compose pin."""

    digest = "sha256:" + "b" * 64
    for name in ("VERSION", "RELEASE_VERSION"):
        (folder / name).write_text(version + "\n", encoding="ascii")
    (folder / "deployed.json").write_text(
        json.dumps({"version": version, "digest": digest})
    )
    (folder / "override.yaml").write_text(
        "services:\n  shelly-voltage:\n"
        "    image: ghcr.io/isentinel/shelly-voltage-telegram@" + digest + "\n"
    )
    for name in ("deployed.json", "override.yaml"):
        os.chmod(folder / name, 0o600)


@contextmanager
def writer_fence(folder: Path):
    """Hold a nonblocking POSIX lock on a private fixture writer token."""

    path = folder / "synthetic-writer.lock"
    if path.is_symlink():
        raise Blocked("WRITER_FENCE_PATH_UNSAFE")
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Blocked("WRITER_FENCE_PATH_UNSAFE")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked("WRITER_NOT_FENCED") from exc
        yield
    finally:
        os.close(descriptor)


def run_case(
    root: Path, signed: Path, old: str, target: str, fault: str
) -> dict:
    """Build synthetic databases and hold the fixture writer fence."""

    active = root / "active.sqlite3"
    historical = root / "historical.sqlite3"
    create_fixture(active, 10)
    create_fixture(historical, 6)
    initial_digest, initial_rows = fixture_digest(active)
    if initial_rows <= fixture_digest(historical)[1]:
        raise Blocked("FIXTURE_HISTORY_ORDER_INVALID")
    write_current_pair(root, target)
    if os.getenv("SVT_DISPOSABLE_WRITER_FENCED", "true") != "true":
        raise Blocked("WRITER_NOT_FENCED")
    with writer_fence(root):
        return execute_fenced_case(root, signed, old, target, fault)


def execute_fenced_case(
    root: Path, signed: Path, old: str, target: str, fault: str
) -> dict:
    """Inject an abort while keeping sole-writer mock fencing active."""

    active = root / "active.sqlite3"
    historical = root / "historical.sqlite3"
    initial_digest = fixture_digest(active)[0]
    try:
        sqlite_backup(active, root / "rescue.sqlite3")
        require_equal(active, root / "rescue.sqlite3")
        fail_at(root, "after_rescue", fault)
        stage_old_pair(root, signed, old)
        if staged_identity(root)[0] != old:
            raise Blocked("STAGED_VERSION_MISMATCH")
        fail_at(root, "after_old_stage", fault)
        active.unlink()
        sqlite_backup(historical, active)
        fail_at(root, "after_old_db_swap", fault)
        raise Blocked("INJECTION_POINT_NOT_REACHED")
    except SimulatedFailure:
        recover_newer(root, initial_digest, target)
        return {
            "fault": fault,
            "recovered": True,
            "preserved_measurements": fixture_digest(active)[1],
            "photo_identity_preserved": True,
            "bot_started": False,
        }


def rehearse(
    source: Path,
    signed: Path,
    verify_signature: bool,
    expected_commit: str = "",
) -> dict:
    """Bind the old image to an immutable Git SHA before fault fixtures."""
    if verify_signature and not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise Blocked("OLD_IMAGE_CERT_COMMIT_REQUIRED")
    pair = load_pair().check_pair(
        source, signed, verify_signature, expected_commit=expected_commit
    )
    stages = []
    with tempfile.TemporaryDirectory(prefix="svt-disposable-rollback-") as tmp:
        root = Path(tmp)
        for index, fault in enumerate(FAULTS):
            folder = root / f"case-{index}"
            folder.mkdir(mode=0o700)
            stages.append(
                run_case(
                    folder,
                    signed,
                    pair["source_version"],
                    pair["target_version"],
                    fault,
                )
            )
    return {
        "status": "PASS_DISPOSABLE_FAILURE_REHEARSAL_ONLY",
        "old_version": pair["source_version"],
        "signed_identity_verified": pair["signature_verified"],
        "cases": stages,
        "no_telegram_api_calls": True,
        "no_real_bot_started": True,
        "production_modified": False,
        "rollback_authorized": False,
        "release_authorized": False,
        "real_power_loss_tested": False,
    }


def main() -> int:
    """Show simple JSON or PASS/FAIL without live production actions."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-snapshot", type=Path, required=True)
    parser.add_argument("--signed-snapshot", type=Path, required=True)
    parser.add_argument("--verify-signature", action="store_true")
    parser.add_argument(
        "--expected-commit",
        default="",
        help="Exact old source Git SHA required for --verify-signature.",
    )
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        report = rehearse(
            args.source_snapshot,
            args.signed_snapshot,
            args.verify_signature,
            args.expected_commit,
        )
    except (Blocked, OSError, ValueError, sqlite3.Error) as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc)
            if isinstance(exc, Blocked)
            else "FIXTURE_FAILED",
            "production_modified": False,
            "rollback_authorized": False,
        }
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("DISPOSABLE SIGNED ROLLBACK REHEARSAL:", report["status"])
    return 0 if report["status"].startswith("PASS_") else 2


if __name__ == "__main__":
    raise SystemExit(main())
