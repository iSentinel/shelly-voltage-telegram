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
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                raise ValueError("ORPHAN_SQLITE_JOURNAL")
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


def configured_chat(env_file: Path) -> str:
    """Read one trusted Telegram chat ID without executing dotenv code."""

    if env_file.is_symlink() or not env_file.is_file():
        raise ValueError("TELEGRAM_CONFIG_UNAVAILABLE")
    if env_file.stat().st_nlink != 1:
        raise ValueError("TELEGRAM_CONFIG_UNSAFE")
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("TELEGRAM_CONFIG_UNAVAILABLE") from None
    matches = []
    for line in lines:
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "TELEGRAM_STATUS_CHAT_ID":
            matches.append(value.strip())
    if len(matches) != 1:
        raise ValueError("TELEGRAM_CONFIG_AMBIGUOUS")
    chat = matches[0]
    if len(chat) >= 2 and chat[0] in ("'", '"') and chat[-1] == chat[0]:
        chat = chat[1:-1]
    if not chat or any(character.isspace() for character in chat):
        raise ValueError("TELEGRAM_CONFIG_INVALID")
    return chat


def classify_status(
    meta: dict[str, str], expected_chat: str | None = None
) -> str:
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
        or not expected_chat
        or chat != expected_chat
        or not stored
        or stored != message_id
        or not stored.isascii()
        or not stored.isdecimal()
        or int(stored) <= 0
    ):
        raise ValueError("TELEGRAM_TEXT_IDENTITY_INVALID")
    return "confirmed_text"


def inspect(path: Path, env_file: Path | None = None) -> dict:
    """Fail closed when bound media references another Telegram channel."""

    if not guarded_database(path):
        return {"status": "PASS", "reason": "DATABASE_MISSING"}
    meta = metadata(path)
    expected_chat = None
    if meta.get("telegram_status_media_identity") is not None:
        if env_file is None:
            raise ValueError("TELEGRAM_CONFIG_UNAVAILABLE")
        expected_chat = configured_chat(env_file)
    state = classify_status(meta, expected_chat)
    return {"status": "PASS", "reason": state}


def main() -> int:
    """Emit simple fail-closed results for shell and CI."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--jsonl", action="store_true")
    args = parser.parse_args()
    try:
        result = inspect(args.database, args.env_file)
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
