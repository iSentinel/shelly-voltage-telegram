#!/usr/bin/env python3
"""Refuse a Shelly cutover if a saved meter choice overrides the new env."""

from __future__ import annotations

import argparse
import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

MODELS = {
    "pro_em50",
    "em_gen3",
    "pm_mini_gen3",
    "plus_pm_mini",
    "plus_1pm",
    "1pm_gen3",
    "1pm_gen4",
    "1pm_mini_gen3",
    "1pm_mini_gen4",
    "pro3em",
}


class Blocked(ValueError):
    """Provide a stable failure code without exposing secret values."""


def require_regular(path: Path, max_bytes: int) -> None:
    """Deny linked, missing or unusually large configuration inputs."""

    if not path.is_absolute() or any(
        item.is_symlink() for item in (path, *path.parents)
    ):
        raise Blocked("INPUT_PATH_UNSAFE")
    try:
        info = path.stat()
    except OSError as exc:
        raise Blocked("INPUT_MISSING") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Blocked("INPUT_NOT_REGULAR")
    if not 0 < info.st_size <= max_bytes:
        raise Blocked("INPUT_SIZE_INVALID")


def normalized_type(value: str) -> str:
    """Accept only known, non-secret Shelly model identifiers."""

    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1]
    text = text.strip().lower()
    if text not in MODELS:
        raise Blocked("METER_TYPE_UNKNOWN")
    return text


def env_model(path: Path) -> str:
    """Read one meter setting, never logging the other env values."""

    require_regular(path, 1024 * 1024)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (UnicodeError, OSError) as exc:
        raise Blocked("ENV_UNREADABLE") from exc
    choices = [
        line.partition("=")[2]
        for line in lines
        if not line.lstrip().startswith("#")
        and line.partition("=")[0].strip() == "SHELLY_METER_TYPE"
        and "=" in line
    ]
    if len(choices) != 1:
        raise Blocked("ENV_METER_TYPE_NOT_UNIQUE")
    return normalized_type(choices[0])


def stored_model(path: Path) -> str | None:
    """Read persisted Telegram meter preference from SQLite read-only."""

    require_regular(path, 1024 * 1024 * 1024)
    url = path.as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(url, uri=True, timeout=5)) as db:
            db.execute("PRAGMA query_only=ON")
            row = db.execute(
                "SELECT value FROM meta WHERE key=?",
                ("telegram_setting_meter_type",),
            ).fetchone()
    except sqlite3.Error as exc:
        raise Blocked("SAVED_SETTING_DB_UNREADABLE") from exc
    return normalized_type(str(row[0])) if row is not None else None


def assess(env: Path, database: Path, intended: str) -> dict:
    """Prove all three meter settings agree before any service stop."""

    result = {
        "status": "BLOCKED",
        "reason": "UNVERIFIED",
        "expected_meter_type": intended,
        "no_writes": True,
        "service_modified": False,
        "release_authorized": False,
    }
    if intended not in MODELS:
        result["reason"] = "EXPECTED_METER_TYPE_UNKNOWN"
        return result
    try:
        selected = env_model(env)
        stored = stored_model(database)
    except Blocked as exc:
        result["reason"] = str(exc)
        return result
    result["env_meter_type"] = selected
    result["saved_meter_type"] = stored
    if selected != intended:
        result["reason"] = "ENV_METER_TYPE_MISMATCH"
    elif stored is not None and stored != intended:
        result["reason"] = "SAVED_METER_TYPE_OVERRIDES_ENV"
    else:
        result["status"] = "PASS_STATIC_METER_PARITY"
        result["reason"] = "MATCHED"
    return result


def main() -> int:
    """Print explicit PASS/BLOCKED and exit without touching production."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--expected-meter", required=True)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    result = assess(
        args.env_file, args.db, args.expected_meter.strip().lower()
    )
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    else:
        print("METER_CUTOVER_PREFLIGHT=" + result["status"])
        print("REASON=" + result["reason"])
    return 0 if result["status"] == "PASS_STATIC_METER_PARITY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
