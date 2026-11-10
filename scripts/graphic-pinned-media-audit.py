#!/usr/bin/env python3
"""Read-only audit of the exact Telegram channel status media type."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import urllib.request
from pathlib import Path

MAX_RESPONSE_BYTES = 65536


def parse_args() -> argparse.Namespace:
    """Require a database and exactly one Telegram response source."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--chat-env", default="TELEGRAM_STATUS_CHAT_ID")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--token-file", type=Path)
    mode.add_argument("--response-file", type=Path)
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def safe_file(path: Path) -> bool:
    """Require independent regular files and no linked parents."""

    try:
        if any(part.is_symlink() for part in (path, *path.parents)):
            return False
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    except OSError:
        return False


def read_bounded_file(path: Path) -> bytes:
    """Read at most one bounded private fixture or bot secret."""

    if not safe_file(path) or path.stat().st_size > MAX_RESPONSE_BYTES:
        raise ValueError("INPUT_UNSAFE")
    with path.open("rb") as stream:
        value = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(value) > MAX_RESPONSE_BYTES:
        raise ValueError("INPUT_TOO_LARGE")
    return value


def load_status(path: Path) -> tuple[dict, str]:
    """Reuse the existing read-only SQLite status metadata reader."""

    module_file = Path(__file__).with_name("graphic-migration-preflight.py")
    spec = importlib.util.spec_from_file_location(
        "graphic_status_reader", module_file
    )
    if spec is None or spec.loader is None:
        raise ValueError("STATUS_READER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return module.read_metadata(path)
    except ValueError:
        raise ValueError("DATABASE_INVALID") from None


def request_chat(token_file: Path, chat_id: str) -> dict:
    """Request getChat only: never send, edit, pin or delete messages."""

    token = read_bounded_file(token_file).decode("ascii").strip()
    if not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,128}", token):
        raise ValueError("TOKEN_INVALID")
    url = "https://api.telegram.org/bot" + token + "/getChat"
    body = json.dumps({"chat_id": chat_id}).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
        return json.loads(data)
    except Exception:
        raise ValueError("TELEGRAM_READ_UNAVAILABLE") from None


def exact_identity(metadata: dict, channel: str) -> str:
    """Reject unresolved publications and inconsistent message identities."""

    if "telegram_graphic_publish_pending" in metadata:
        raise ValueError("UNRESOLVED_PUBLICATION")
    current = metadata.get("telegram_status_message_id")
    if not isinstance(current, str) or not re.fullmatch(
        r"[0-9]{1,20}", current
    ):
        raise ValueError("STATUS_ID_INVALID")
    if int(current) <= 0:
        raise ValueError("STATUS_ID_INVALID")
    if not re.fullmatch(r"-?[0-9]{1,20}", channel) or int(channel) == 0:
        raise ValueError("CHANNEL_ID_INVALID")
    return current


def pinned_kind(response: dict, channel: str, status_id: str) -> str:
    """Accept only the exact pinned post and an unambiguous text/photo type."""

    if response.get("ok") is not True or not isinstance(
        response.get("result"), dict
    ):
        raise ValueError("TELEGRAM_RESPONSE_INVALID")
    chat = response["result"]
    if chat.get("type") != "channel" or str(chat.get("id")) != channel:
        raise ValueError("CHANNEL_MISMATCH")
    pinned = chat.get("pinned_message")
    if not isinstance(pinned, dict):
        raise ValueError("PINNED_MESSAGE_UNAVAILABLE")
    if pinned.get("message_id") != int(status_id):
        raise ValueError("PINNED_MESSAGE_MISMATCH")
    if not isinstance(pinned.get("chat"), dict):
        raise ValueError("PINNED_MESSAGE_MISMATCH")
    if str(pinned["chat"].get("id")) != channel:
        raise ValueError("PINNED_MESSAGE_MISMATCH")
    has_text = isinstance(pinned.get("text"), str)
    has_photo = isinstance(pinned.get("photo"), list) and bool(pinned["photo"])
    if has_text == has_photo:
        raise ValueError("MEDIA_TYPE_AMBIGUOUS")
    return "text" if has_text else "photo"


def audit(args: argparse.Namespace) -> dict:
    """Produce evidence for review, never a deployment permission."""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", args.chat_env):
        raise ValueError("CHANNEL_ENV_INVALID")
    channel = os.getenv(args.chat_env, "")
    metadata, integrity = load_status(args.database)
    if integrity != "ok":
        raise ValueError("DATABASE_INVALID")
    status_id = exact_identity(metadata, channel)
    live = args.token_file is not None
    response = (
        request_chat(args.token_file, channel)
        if live
        else json.loads(read_bounded_file(args.response_file))
    )
    if not isinstance(response, dict):
        raise ValueError("TELEGRAM_RESPONSE_INVALID")
    kind = pinned_kind(response, channel, status_id)
    known = metadata.get("telegram_status_media_identity")
    if known is not None and known != f"{kind}:{channel}:{status_id}":
        raise ValueError("CONFLICTING_MEDIA_MARKER")
    return {
        "status": "MATCH_FOR_REVIEW" if live else "FIXTURE_MATCH_ONLY",
        "media_kind": kind,
        "live_attestation": live,
        "deployment_allowed": False,
        "database_modified": False,
        "telegram_messages_modified": False,
    }


def main() -> int:
    """Print one safe decision and its stable exit code."""

    args = parse_args()
    try:
        result = audit(args)
    except Exception as exc:
        reason = str(exc) if type(exc) is ValueError else "AUDIT_UNAVAILABLE"
        if not re.fullmatch(r"[A-Z_]{3,64}", reason):
            reason = "AUDIT_UNAVAILABLE"
        result = {
            "status": "BLOCKED",
            "reason": reason,
            "deployment_allowed": False,
            "database_modified": False,
            "telegram_messages_modified": False,
        }
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("PINNED MEDIA AUDIT: " + result["status"])
        if result["status"] == "BLOCKED":
            print("Reason: " + result["reason"])
    return 2 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
