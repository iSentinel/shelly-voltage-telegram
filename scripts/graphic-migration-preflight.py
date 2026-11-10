#!/usr/bin/env python3
"""Read-only approval preflight for a dedicated Telegram graphic status."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from pathlib import Path

STATUS_KEY = "telegram_status_message_id"
MEDIA_KEY = "telegram_status_media_identity"
PENDING_KEY = "telegram_graphic_publish_pending"


def parse_args() -> argparse.Namespace:
    """Require explicit database and an environment variable for the chat."""

    parser = argparse.ArgumentParser(
        description="Check existing channel status metadata without writes."
    )
    parser.add_argument(
        "--database", required=True, type=Path, help="Existing SQLite database"
    )
    parser.add_argument(
        "--chat-env",
        default="TELEGRAM_STATUS_CHAT_ID",
        help="Environment variable holding the expected channel ID",
    )
    return parser.parse_args()


def read_metadata(path: Path) -> tuple[dict[str, str], str]:
    """Read only the fields needed for a status migration decision."""

    if path.is_symlink() or not path.is_file():
        raise ValueError("database path is absent or a symlink")
    try:
        with sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5
        ) as db:
            check = str(db.execute("PRAGMA quick_check").fetchone()[0])
            rows = db.execute(
                "SELECT key, value FROM meta WHERE key IN (?, ?, ?)",
                (STATUS_KEY, MEDIA_KEY, PENDING_KEY),
            ).fetchall()
    except sqlite3.Error:
        raise ValueError("read-only SQLite inspection failed") from None
    return dict(rows), check


def readiness_issues(
    metadata: dict[str, str], check: str, channel: str
) -> list[str]:
    """Refuse any unverified status or ambiguous Telegram send."""

    issues = []
    current = metadata.get(STATUS_KEY)
    media = metadata.get(MEDIA_KEY)
    if check != "ok":
        issues.append("SQLite quick_check failed")
    if (
        not current
        or not re.fullmatch(r"[0-9]{1,20}", current)
        or int(current) <= 0
    ):
        issues.append("dedicated channel status ID is absent or invalid")
    if (
        not channel
        or not re.fullmatch(r"-?[0-9]{1,20}", channel)
        or int(channel) == 0
    ):
        issues.append("expected channel environment is absent or invalid")
    if (
        current
        and channel
        and media
        not in (f"text:{channel}:{current}", f"photo:{channel}:{current}")
    ):
        issues.append("channel media identity is not verified")
    if metadata.get(PENDING_KEY):
        issues.append(
            "unresolved Telegram publication requires reconciliation"
        )
    return issues


def main() -> int:
    """Print an actionable PASS or BLOCKED without exposing identifiers."""

    args = parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", args.chat_env):
        print("PREFLIGHT: BLOCKED - invalid environment variable name")
        return 2
    try:
        metadata, check = read_metadata(args.database)
    except ValueError as exc:
        print(f"PREFLIGHT: BLOCKED - {exc}")
        return 2
    issues = readiness_issues(metadata, check, os.getenv(args.chat_env, ""))
    if issues:
        print("PREFLIGHT: BLOCKED (read-only)")
        for issue in issues:
            print(f" - {issue}")
        return 2
    print("PREFLIGHT: PASS (read-only; deployment still needs Owner Approval)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
