#!/usr/bin/env python3
"""Audit saved Telegram status identity without network or database writes."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

STATUS_KEYS = (
    "telegram_status_message_id",
    "telegram_message_id",
    "telegram_status_media_identity",
)
PENDING_KEYS = (
    "telegram_graphic_publish_pending",
    "telegram_legacy_text_confirm_pending",
    "telegram_recovery_rotation_pending_at",
)
DEFAULT_DB = Path("/var/lib/shelly-voltage/voltage.sqlite3")


def valid_message_id(value: object) -> bool:
    """Accept only positive ASCII decimal Telegram message IDs."""

    return (
        isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and int(value) > 0
    )


def read_snapshot(database: Path) -> tuple[dict[str, str], str]:
    """Read metadata and integrity from one WAL-consistent snapshot."""

    uri = database.expanduser().resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        keys = STATUS_KEYS + PENDING_KEYS
        placeholders = ",".join("?" for _ in keys)
        query = (
            "SELECT key,value FROM meta WHERE key IN (" + placeholders + ")"
        )
        saved = dict(db.execute(query, keys))
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
    return saved, integrity


def inspect_status(database: Path) -> dict[str, object]:
    """Evaluate the saved message binding without editing the database."""

    saved, integrity = read_snapshot(database)
    current = saved.get(STATUS_KEYS[0]) or saved.get(STATUS_KEYS[1])
    marker = saved.get(STATUS_KEYS[2], "")
    parts = marker.split(":") if marker else []
    kind, channel, bound = parts if len(parts) == 3 else ("unknown", "", "")
    pending = sum(saved.get(key) is not None for key in PENDING_KEYS)
    bound_ok = (
        valid_message_id(current)
        and valid_message_id(bound)
        and current == bound
        and kind in {"text", "photo"}
        and bool(channel)
    )
    reason = "ok"
    if integrity != "ok":
        reason = "sqlite_integrity"
    elif not valid_message_id(current):
        reason = "missing_status"
    elif not bound_ok:
        reason = "media_identity_mismatch"
    elif pending:
        reason = "publication_pending"

    return {
        "ok": reason == "ok",
        "reason": reason,
        "status_id": current if valid_message_id(current) else None,
        "marker_id": bound if valid_message_id(bound) else None,
        "media_kind": kind,
        "pending_count": pending,
        "sqlite_quick_check": integrity,
    }


def main() -> int:
    """Print a token-free PASS/FAIL and return a stable exit code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect_status(args.db)
    except (sqlite3.Error, OSError, ValueError) as exc:
        result = {"ok": False, "reason": "db_read_error"}
        result["error_type"] = type(exc).__name__

    if args.jsonl:
        print(json.dumps(result, sort_keys=True))
    else:
        label = "PASS" if result["ok"] else "FAIL"
        summary = f"{label}: Telegram identity {result['reason']}"
        print(summary)
        if result["reason"] != "db_read_error":
            print(
                f"status={result['status_id']} marker={result['marker_id']} "
                f"kind={result['media_kind']} pending={result['pending_count']} "
                f"sqlite={result['sqlite_quick_check']}"
            )
    return 2 if result["reason"] == "db_read_error" else int(not result["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
