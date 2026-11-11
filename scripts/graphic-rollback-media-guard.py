#!/usr/bin/env python3
"""Reject legacy source/SQLite rollback after an uncertain Telegram edit."""

from __future__ import annotations

import argparse
import json
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

PENDING = (
    "telegram_legacy_text_confirm_pending",
    "telegram_graphic_publish_pending",
)


def guarded_database(path: Path) -> bool:
    """Verify a local, regular, singly linked SQLite file."""

    if path.is_symlink():
        raise ValueError("UNSAFE_DATABASE_PATH")
    if not path.exists():
        return False
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("UNSAFE_DATABASE_PATH")
    return True


def metadata(path: Path) -> dict[str, str]:
    """Read exact media and pending keys without logging identifiers."""

    try:
        with closing(
            sqlite3.connect(
                path.absolute().as_uri() + "?mode=ro", uri=True, timeout=4
            )
        ) as db:
            if db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise ValueError("DATABASE_INTEGRITY_INVALID")
            rows = db.execute(
                "select key,value from meta where key in (?,?,?,?)",
                (
                    *PENDING,
                    "telegram_status_media_identity",
                    "telegram_status_message_id",
                ),
            ).fetchall()
    except sqlite3.Error:
        raise ValueError("DATABASE_METADATA_UNAVAILABLE") from None
    return dict(rows)


def classify_status(meta: dict[str, str]) -> str:
    """Never permit restoring legacy code across photo or pending media."""

    if any(key in meta for key in PENDING):
        raise ValueError("TELEGRAM_EDIT_UNRESOLVED")
    identity = meta.get("telegram_status_media_identity")
    if identity is None:
        return "legacy_unbound"
    if not isinstance(identity, str) or not identity.startswith("text:"):
        raise ValueError("TELEGRAM_MEDIA_NOT_TEXT")
    try:
        kind, chat, message_id = identity.rsplit(":", 2)
    except ValueError:
        raise ValueError("TELEGRAM_TEXT_IDENTITY_INVALID") from None
    stored = meta.get("telegram_status_message_id")
    if (
        kind != "text"
        or not chat
        or not stored
        or stored != message_id
        or not stored.isascii()
        or not stored.isdecimal()
        or int(stored) <= 0
    ):
        raise ValueError("TELEGRAM_TEXT_IDENTITY_INVALID")
    return "confirmed_text"


def inspect(path: Path) -> dict:
    """Return a safe decision without changing source, SQLite or Telegram."""

    if not guarded_database(path):
        return {"status": "PASS", "reason": "DATABASE_MISSING"}
    state = classify_status(metadata(path))
    return {"status": "PASS", "reason": state}


def main() -> int:
    """Emit simple fail-closed results for shell and CI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect(args.database)
    except (OSError, ValueError):
        result = {"status": "BLOCKED", "reason": "UNSAFE_TELEGRAM_ROLLBACK"}
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("GRAPHIC ROLLBACK MEDIA: " + result["status"])
        if result["status"] == "BLOCKED":
            print("ERROR: Telegram media requires separate reconciliation")
    return 0 if result["status"] == "PASS" else 6


if __name__ == "__main__":
    raise SystemExit(main())
