#!/usr/bin/env python3
"""Observe release/SQLite schema parity without executing release code."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-hw\Z")
EXPECTED = {
    "measurements": {"id", "ts", "voltage", "frequency", "phase"},
    "outages": {"id", "started_at", "ended_at", "phase"},
    "meta": {"key", "value"},
    "settings_audit": {
        "id",
        "ts",
        "user_id",
        "setting",
        "old_value",
        "new_value",
    },
    "hourly_aggregates": {
        "phase",
        "hour_ts",
        "samples",
        "min_v",
        "max_v",
        "avg_v",
    },
}
INDICES = {
    "idx_measurements_ts",
    "idx_measurements_phase_ts",
    "idx_outages_started",
    "idx_outages_phase_started",
    "idx_settings_audit_ts",
}


class Blocked(Exception):
    """Source or SQLite evidence did not meet the read-only safety rules."""


def small_regular(path: Path, maximum: int = 1048576) -> str:
    """Require a bounded, regular, nonlinked source file."""
    if not path.is_absolute() or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise Blocked("SOURCE_PATH_LINKED_OR_RELATIVE")
    try:
        info = path.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size < 1
            or info.st_size > maximum
            or info.st_mode & 0o022
        ):
            raise Blocked("SOURCE_FILE_UNSAFE")
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise Blocked("SOURCE_FILE_UNAVAILABLE") from exc


def release_version(root: Path) -> str:
    """Require matching release version files without importing any code."""
    version = small_regular(root / "VERSION", 128).strip()
    released = small_regular(root / "RELEASE_VERSION", 128).strip()
    if not VERSION.fullmatch(version) or version != released:
        raise Blocked("RELEASE_VERSION_MARKERS_MISMATCH")
    return version


def source_schema_version(app: Path) -> int:
    """Parse a literal top-level SCHEMA_VERSION with Python AST only."""
    try:
        tree = ast.parse(small_regular(app), filename="<release-source>")
    except (SyntaxError, ValueError) as exc:
        raise Blocked("RELEASE_SOURCE_PARSE_FAILED") from exc
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION"
            for target in node.targets
        ):
            found.append(node.value)
    if len(found) != 1 or not isinstance(found[0], ast.Constant):
        raise Blocked("RELEASE_SCHEMA_MARKER_INVALID")
    value = found[0].value
    if type(value) is not int or value < 1 or value > 100:
        raise Blocked("RELEASE_SCHEMA_VERSION_UNSAFE")
    return value


def existing_schema(db: sqlite3.Connection) -> dict:
    """Read current SQLite version, required columns and indexes only."""
    db.execute("BEGIN")
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise Blocked("CURRENT_SQLITE_INTEGRITY_INVALID")
    row = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()
    if row is None or not isinstance(row[0], str) or not row[0].isdigit():
        raise Blocked("CURRENT_SCHEMA_MARKER_INVALID")
    columns = {}
    for table in EXPECTED:
        columns[table] = {
            cell[1]
            for cell in db.execute('PRAGMA table_info("' + table + '")')
        }
    indexes = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    samples = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    return {
        "schema_version": int(row[0]),
        "header_user_version": db.execute("PRAGMA user_version").fetchone()[0],
        "columns": columns,
        "indexes": indexes,
        "measurements": samples,
    }


def load_database(path: Path) -> dict:
    """Inspect a single existing SQLite file in read-only query mode."""
    if not path.is_absolute() or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise Blocked("CURRENT_DATABASE_PATH_UNSAFE")
    if not path.is_file() or path.stat().st_nlink != 1:
        raise Blocked("CURRENT_DATABASE_FILE_UNSAFE")
    try:
        with closing(
            sqlite3.connect(
                path.as_uri() + "?mode=ro",
                uri=True,
                timeout=5,
            )
        ) as db:
            db.execute("PRAGMA query_only=ON")
            return existing_schema(db)
    except sqlite3.Error as exc:
        raise Blocked("CURRENT_SQLITE_READ_FAILED") from exc


def decision(version: str, candidate: int, observed: dict) -> dict:
    """Require exact current-schema parity; always refuse boot authority."""
    reasons = []
    actual = observed["schema_version"]
    if actual > candidate:
        reasons.append("FUTURE_SCHEMA_DOWNGRADE_UNSAFE")
    elif actual < candidate:
        reasons.append("SCHEMA_MIGRATION_REQUIRED")
    for table, fields in EXPECTED.items():
        if not fields.issubset(observed["columns"][table]):
            reasons.append("REQUIRED_COLUMNS_MISSING_" + table.upper())
    if not INDICES.issubset(observed["indexes"]):
        reasons.append("REQUIRED_INDEXES_MISSING")
    if observed["measurements"] < 1:
        reasons.append("CURRENT_HISTORY_EMPTY")
    return {
        "status": "REVIEW_REQUIRED" if reasons else "SCHEMA_OBSERVED_MATCH",
        "reasons": reasons,
        "release_version": version,
        "candidate_schema_version": candidate,
        "current_schema_version": actual,
        "sqlite_user_version": observed["header_user_version"],
        "current_measurements": observed["measurements"],
        "schema_migration_executed": False,
        "signature_verified_here": False,
        "schema_runtime_compatibility_proven": False,
        "boot_start_authorized": False,
        "source_install_authorized": False,
        "telegram_photo_authorized": False,
        "production_modified": False,
    }


def denied(reason: str) -> dict:
    """Keep every blocked result explicitly non-authorizing."""
    return {
        "status": "BLOCKED",
        "reasons": [reason],
        "schema_migration_executed": False,
        "signature_verified_here": False,
        "schema_runtime_compatibility_proven": False,
        "boot_start_authorized": False,
        "source_install_authorized": False,
        "telegram_photo_authorized": False,
        "production_modified": False,
    }


def evaluate(source: Path, database: Path) -> dict:
    """Compare only observed schema contracts, never open a writable DB."""
    try:
        version = release_version(source)
        target = source_schema_version(source / "app.py")
        current = load_database(database)
        return decision(version, target, current)
    except (Blocked, OSError, ValueError) as exc:
        return denied(str(exc) if isinstance(exc, Blocked) else "READ_FAILED")


def main() -> int:
    """Default dry-run; explicit source and SQLite inputs are read-only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    if args.dry_run or (args.source is None and args.database is None):
        print("DRY-RUN PASS: no source, SQLite or Docker inspected")
        return 0
    if args.source is None or args.database is None:
        report = denied("SOURCE_AND_DATABASE_REQUIRED")
    else:
        report = evaluate(args.source, args.database)
    if args.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("SCHEMA PREFLIGHT:", report["status"])
        for reason in report.get("reasons", []):
            print(" -", reason)
        print("NO MIGRATIONS, BOOT START OR PRODUCTION ACTIONS AUTHORIZED")
    return 0 if report["status"] == "SCHEMA_OBSERVED_MATCH" else 2


if __name__ == "__main__":
    raise SystemExit(main())
