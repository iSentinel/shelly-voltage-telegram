#!/usr/bin/env python3
"""Triage unresolved Telegram status intents with SQLite read-only access."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

DEFAULT_DB = Path("/var/lib/shelly-voltage/voltage.sqlite3")
TEXT_PENDING = "telegram_legacy_text_confirm_pending"
GRAPHIC_PENDING = "telegram_graphic_publish_pending"
RECOVERY_PENDING = "telegram_recovery_rotation_pending_at"
STATUS_ID = "telegram_status_message_id"
LEGACY_ID = "telegram_message_id"
MEDIA_ID = "telegram_status_media_identity"
META_KEYS = (
    TEXT_PENDING,
    GRAPHIC_PENDING,
    RECOVERY_PENDING,
    STATUS_ID,
    LEGACY_ID,
    MEDIA_ID,
)


def read_snapshot(path: Path) -> tuple[dict[str, str], str]:
    """Read one transaction from an existing local SQLite file."""

    if not path.is_file() or path.is_symlink():
        raise ValueError("database is absent or linked")
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        placeholders = ",".join("?" for _ in META_KEYS)
        rows = db.execute(
            f"SELECT key,value FROM meta WHERE key IN ({placeholders})",
            META_KEYS,
        )
        saved = dict(rows)
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
    return saved, integrity


def pending_kinds(saved: dict[str, str]) -> list[str]:
    """Label pending work without returning Telegram tokens or chat IDs."""

    found = []
    if TEXT_PENDING in saved:
        value = saved[TEXT_PENDING]
        if re.fullmatch(r"send:[0-9a-f]{32}", value):
            found.append("unknown_text_send")
        elif value.startswith("send:"):
            found.append("invalid_text_send_intent")
        else:
            found.append("legacy_text_confirmation")
    if GRAPHIC_PENDING in saved:
        found.append("graphic_publication")
    if RECOVERY_PENDING in saved:
        found.append("recovery_rotation")
    return found


def saved_identity(saved: dict[str, str]) -> tuple[bool, str | None, str]:
    """Check status ID and type without returning the private chat ID."""

    status = saved.get(STATUS_ID) or saved.get(LEGACY_ID)
    marker = saved.get(MEDIA_ID, "")
    pieces = marker.split(":")
    kind = pieces[0] if len(pieces) == 3 else "unknown"
    valid = (
        isinstance(status, str)
        and bool(re.fullmatch(r"[0-9]+", status))
        and int(status) > 0
        and len(pieces) == 3
        and bool(pieces[1])
        and pieces[2] == status
        and kind in {"text", "photo"}
    )
    return valid, status if valid else None, kind


def inspect(path: Path) -> dict[str, object]:
    """Require manual reconciliation for ambiguous text publication."""

    saved, integrity = read_snapshot(path)
    kinds = pending_kinds(saved)
    valid, status_id, kind = saved_identity(saved)
    reason = "ok"
    if integrity != "ok":
        reason = "sqlite_integrity"
    elif "unknown_text_send" in kinds:
        reason = "unknown_remote_send_outcome"
    elif "invalid_text_send_intent" in kinds:
        reason = "invalid_send_intent"
    elif kinds:
        reason = "publication_pending"
    elif not valid:
        reason = "saved_identity_invalid"
    return {
        "status": "CLEAR" if reason == "ok" else "HOLD",
        "reason": reason,
        "saved_status_id": status_id,
        "media_kind": kind,
        "saved_identity_valid": valid,
        "pending_kinds": kinds,
        "pending_count": len(kinds),
        "owner_reconciliation_required": (
            "unknown_text_send" in kinds or "invalid_text_send_intent" in kinds
        ),
        "recipient_visible_delivery_verified": False,
        "release_authorized": False,
        "photo_authorized": False,
        "sqlite_quick_check": integrity,
        "database_modified": False,
    }


def main() -> int:
    """Report the reason for a hold; never clear or create any marker."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect(args.db)
    except (sqlite3.Error, ValueError, OSError) as error:
        result = {
            "status": "ERROR",
            "reason": "database_unreadable",
            "error_type": type(error).__name__,
            "database_modified": False,
            "release_authorized": False,
            "photo_authorized": False,
        }
    if args.jsonl:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"TELEGRAM STATUS INTENT: {result['status']} ({result['reason']})"
        )
    if result["status"] == "ERROR":
        return 2
    return 0 if result["status"] == "CLEAR" else 1


if __name__ == "__main__":
    raise SystemExit(main())
