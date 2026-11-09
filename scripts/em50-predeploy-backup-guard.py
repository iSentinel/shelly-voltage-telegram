"""Read-only predeployment SQLite backup parity gate for the EM-50 bot."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

LIMIT_BYTES = 128 * 1024 * 1024


def newest_backup(directory: Path) -> Path:
    """Select an existing regular backup without following symlinks."""

    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("backup directory missing or linked")
    files = list(directory.glob("voltage-*.sqlite3.gz"))
    if not files:
        raise ValueError("no existing SQLite backups")
    for item in files:
        info = item.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("backup or checksum is not an ordinary file")
    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    path = files[0]
    sidecar = Path(str(path) + ".sha256")
    info = sidecar.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("backup or checksum is not an ordinary file")
    return path


def verify_checksum(path: Path) -> None:
    """Validate a pinned manifest path and SHA256 without extraction."""

    sidecar = Path(str(path) + ".sha256")
    if sidecar.stat().st_size > 512:
        raise ValueError("checksum sidecar too large")
    values = sidecar.read_text(encoding="utf-8").strip().split()
    if len(values) != 2 or len(values[0]) != 64:
        raise ValueError("checksum manifest is malformed")
    if values[1].removeprefix("*") != str(path):
        raise ValueError("checksum manifest points at another file")
    with path.open("rb") as archive:
        checksum = hashlib.file_digest(archive, "sha256").hexdigest()
    if checksum != values[0].lower():
        raise ValueError("backup SHA256 mismatch")


def digest_rows(db: sqlite3.Connection, query: str) -> bytes:
    """Hash complete ordered SQLite rows without exposing their contents."""

    digest = hashlib.sha256()
    for row in db.execute(query):
        serialized = json.dumps(
            row, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        digest.update(len(serialized).to_bytes(8, "big"))
        digest.update(serialized)
    return digest.digest()


def whole_sqlite_digest(db: sqlite3.Connection) -> bytes:
    """Hash table state and persistent SQLite header metadata privately."""

    digest = hashlib.sha256()
    for statement in db.iterdump():
        raw = statement.encode("utf-8")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    # iterdump omits these persisted header fields used for migrations.
    for pragma in ("user_version", "application_id"):
        row = db.execute(f"PRAGMA {pragma}").fetchone()
        if row is None or not isinstance(row[0], int):
            raise ValueError("SQLite header pragma is unavailable")
        raw = f"{pragma}={row[0]}".encode("ascii")
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.digest()


def sqlite_state(db_path: Path) -> dict:
    """Read selected critical state without exposing Telegram IDs."""

    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity failed")
        rows = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        last = db.execute(
            "SELECT id,ts,voltage,frequency,phase FROM measurements "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        outage = db.execute(
            "SELECT id,started_at,ended_at,phase FROM outages "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        status = db.execute(
            "SELECT value FROM meta WHERE key='telegram_status_message_id'"
        ).fetchone()
        measurements_digest = digest_rows(
            db,
            "SELECT id,ts,voltage,frequency,phase "
            "FROM measurements ORDER BY id",
        )
        outages_digest = digest_rows(
            db,
            "SELECT id,started_at,ended_at,phase FROM outages ORDER BY id",
        )
        whole_digest = whole_sqlite_digest(db)
    return {
        "whole_digest": whole_digest,
        "measurements_digest": measurements_digest,
        "outages_digest": outages_digest,
        "rows": rows,
        "last": last,
        "outage": outage,
        "status": status[0] if status else None,
    }


def unpack_state(archive: Path) -> dict:
    """Bound gzip expansion in a disposable private QA directory."""

    with tempfile.TemporaryDirectory(prefix="shelly-backup-preflight-") as tmp:
        target = Path(tmp) / "recovered.sqlite3"
        expanded = 0
        with gzip.open(archive, "rb") as source, target.open("xb") as out:
            os.fchmod(out.fileno(), 0o600)
            while chunk := source.read(1024 * 1024):
                expanded += len(chunk)
                if expanded > LIMIT_BYTES:
                    raise ValueError("backup expands beyond QA size limit")
                out.write(chunk)
        return sqlite_state(target)


def parity_reasons(
    live: dict, saved: dict, age: float, max_age: float
) -> tuple[list[str], bool, bool]:
    """Compare complete histories and critical status in private memory."""

    measurements_equal = (
        live["measurements_digest"] == saved["measurements_digest"]
    )
    outages_equal = live["outages_digest"] == saved["outages_digest"]
    issues = []
    if not 0 <= age <= max_age:
        issues.append("backup_stale")
    if (
        live["rows"] != saved["rows"]
        or live["last"] != saved["last"]
        or not measurements_equal
    ):
        issues.append("measurements_not_equal")
    if saved["outage"] != live["outage"] or not outages_equal:
        issues.append("outage_state_not_equal")
    if not saved["status"] or saved["status"] != live["status"]:
        issues.append("status_message_metadata_not_equal")
    if saved["whole_digest"] != live["whole_digest"]:
        issues.append("sqlite_state_not_equal")
    return issues, measurements_equal, outages_equal


def inspect(args: argparse.Namespace, now: float) -> dict:
    """Check age and recovery-relevant parity without altering production."""

    if not 1 <= args.max_age_minutes <= 120:
        raise ValueError("maximum backup age outside safe bounds")
    archive = newest_backup(Path(args.backup_dir))
    verify_checksum(archive)
    age = (now - archive.stat().st_mtime) / 60
    live_path = Path(args.db)
    if not live_path.is_file() or live_path.is_symlink():
        raise ValueError("live SQLite missing or linked")
    live = sqlite_state(live_path)
    saved = unpack_state(archive)
    lag = live["rows"] - saved["rows"]
    issues, measurements_equal, outages_equal = parity_reasons(
        live, saved, age, args.max_age_minutes
    )
    return {
        "status": "FAIL" if issues else "PASS",
        "reason_codes": issues,
        "backup_age_minutes": round(age, 2),
        "backup_measurements": saved["rows"],
        "live_measurements": live["rows"],
        "record_gap": lag,
        "latest_measurement_matches": saved["last"] == live["last"],
        "all_measurements_match": measurements_equal,
        "outage_state_matches": saved["outage"] == live["outage"],
        "all_outages_match": outages_equal,
        "all_sqlite_state_matches": (
            saved["whole_digest"] == live["whole_digest"]
        ),
        "status_metadata_matches": bool(
            saved["status"] and saved["status"] == live["status"]
        ),
        "backup_sha256": "PASS",
        "release_authorized": False,
    }


def main() -> int:
    """Report non-secret backup readiness with stable exit codes."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--backup-dir", default="/var/backups/shelly-voltage")
    parser.add_argument("--max-age-minutes", type=float, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect(args, time.time())
    except (OSError, ValueError, sqlite3.DatabaseError, TypeError) as exc:
        print(f"FAIL backup_parity {type(exc).__name__}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"{result['status']} backup_parity "
            f"lag={result['record_gap']} "
            f"age_min={result['backup_age_minutes']}"
        )
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
