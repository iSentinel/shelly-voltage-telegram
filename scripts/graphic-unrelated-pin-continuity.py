#!/usr/bin/env python3
"""Audit continuity of a different pinned post without changing Telegram."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib.util
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path

MAX_BASELINE_BYTES = 4096
MAX_AGE_SECONDS = 86400


class PinAuditError(Exception):
    """Safe failure code with no Telegram IDs or credentials."""


def pinned_reader():
    """Use the existing bounded, read-only getChat implementation."""

    path = Path(__file__).with_name("graphic-pinned-media-audit.py")
    spec = importlib.util.spec_from_file_location("pin_reader", path)
    if spec is None or spec.loader is None:
        raise PinAuditError("PIN_READER_UNAVAILABLE")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader


def arguments():
    """Require one channel response source and one private baseline path."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--chat-env", default="TELEGRAM_STATUS_CHAT_ID")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--token-file", type=Path)
    group.add_argument("--response-file", type=Path)
    parser.add_argument("--capture", action="store_true")
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def safe_path(path: Path) -> bool:
    """Avoid symlinked paths, relative paths and parent traversal."""

    return (
        path.is_absolute()
        and ".." not in path.parts
        and not any(piece.is_symlink() for piece in (path, *path.parents))
    )


def exact_pin(response: dict, channel: str, status_id: str) -> int:
    """Read only an unrelated pinned ID from the correct channel."""

    if response.get("ok") is not True or not isinstance(
        response.get("result"), dict
    ):
        raise PinAuditError("CHAT_RECEIPT_INVALID")
    chat = response["result"]
    if chat.get("type") != "channel" or str(chat.get("id")) != channel:
        raise PinAuditError("CHANNEL_MISMATCH")
    message = chat.get("pinned_message")
    if not isinstance(message, dict):
        raise PinAuditError("PINNED_MESSAGE_UNAVAILABLE")
    pinned_id = message.get("message_id")
    if type(pinned_id) is not int or pinned_id <= 0:
        raise PinAuditError("PINNED_MESSAGE_INVALID")
    original = message.get("chat")
    if not isinstance(original, dict) or str(original.get("id")) != channel:
        raise PinAuditError("PINNED_MESSAGE_INVALID")
    if pinned_id == int(status_id):
        raise PinAuditError("PIN_IS_STATUS_MESSAGE")
    return pinned_id


def read_pin(options) -> tuple[str, int]:
    """Collect a verified pin using getChat only, never a mutation API."""

    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", options.chat_env):
        raise PinAuditError("CHANNEL_ENV_INVALID")
    channel = os.getenv(options.chat_env, "")
    reader = pinned_reader()
    metadata, check = reader.load_status(options.database)
    if check != "ok":
        raise PinAuditError("DATABASE_INVALID")
    if "telegram_legacy_text_confirm_pending" in metadata:
        raise PinAuditError("UNRESOLVED_TEXT_CONFIRMATION")
    status_id = reader.exact_identity(metadata, channel)
    response = (
        reader.request_chat(options.token_file, channel)
        if options.token_file is not None
        else json.loads(reader.read_bounded_file(options.response_file))
    )
    return channel, exact_pin(response, channel, status_id)


def fingerprint(key: bytes, channel: str, pinned_id: int) -> str:
    """Bind the pin to its channel without persisting raw IDs."""

    return hmac.new(
        key, f"{channel}:{pinned_id}".encode("ascii"), hashlib.sha256
    ).hexdigest()


def capture(baseline: Path, channel: str, pinned_id: int, live: bool) -> dict:
    """Persist a private salt and pin digest exactly once."""

    if not safe_path(baseline) or not baseline.parent.is_dir():
        raise PinAuditError("BASELINE_PATH_UNSAFE")
    if baseline.exists():
        raise PinAuditError("BASELINE_ALREADY_EXISTS")
    key = secrets.token_bytes(32)
    record = {
        "version": 1,
        "captured_unix": int(time.time()),
        "key_hex": key.hex(),
        "pin_fingerprint": fingerprint(key, channel, pinned_id),
        "capture_type": "unrelated_pin",
        "capture_source": "live" if live else "fixture",
    }
    handle = os.open(baseline, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(handle, "w", encoding="ascii") as output:
        json.dump(record, output, separators=(",", ":"))
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    return {
        "status": (
            "UNRELATED_PIN_BASELINE_CAPTURED"
            if live
            else "FIXTURE_PIN_BASELINE_ONLY"
        ),
        "live_attestation": live,
        "deployment_allowed": False,
        "exact_status_media_verified": False,
        "telegram_modified": False,
    }


def load_baseline(baseline: Path, live: bool) -> tuple[bytes, str]:
    """Load private, unlinked and source-bound pin evidence."""

    if not safe_path(baseline):
        raise PinAuditError("BASELINE_PATH_UNSAFE")
    try:
        info = baseline.stat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or not 0 < info.st_size <= MAX_BASELINE_BYTES
        ):
            raise PinAuditError("BASELINE_UNSAFE")
        record = json.loads(baseline.read_text(encoding="ascii"))
        age = time.time() - record["captured_unix"]
        if record["version"] != 1 or record["capture_type"] != "unrelated_pin":
            raise PinAuditError("BASELINE_INVALID")
        if record.get("capture_source") != ("live" if live else "fixture"):
            raise PinAuditError("BASELINE_SOURCE_MISMATCH")
        if not 0 <= age <= MAX_AGE_SECONDS:
            raise PinAuditError("BASELINE_EXPIRED")
        key = bytes.fromhex(record["key_hex"])
        digest = record["pin_fingerprint"]
        if len(key) != 32 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PinAuditError("BASELINE_INVALID")
    except (OSError, KeyError, TypeError, ValueError, UnicodeError):
        raise PinAuditError("BASELINE_INVALID") from None
    return key, digest


def verify(baseline: Path, channel: str, pinned_id: int, live: bool) -> dict:
    """Compare a trusted baseline without returning Telegram IDs."""

    key, digest = load_baseline(baseline, live)
    expected = fingerprint(key, channel, pinned_id)
    if not hmac.compare_digest(expected, digest):
        raise PinAuditError("UNRELATED_PIN_CHANGED")
    return {
        "status": (
            "UNRELATED_PIN_UNCHANGED_FOR_REVIEW"
            if live
            else "FIXTURE_PIN_UNCHANGED_ONLY"
        ),
        "live_attestation": live,
        "deployment_allowed": False,
        "exact_status_media_verified": False,
        "telegram_modified": False,
    }


def main() -> int:
    """Report read-only continuity without exposing chat or message IDs."""

    options = arguments()
    try:
        channel, pinned_id = read_pin(options)
        live = options.token_file is not None
        report = (
            capture(options.baseline, channel, pinned_id, live)
            if options.capture
            else verify(options.baseline, channel, pinned_id, live)
        )
    except PinAuditError as exc:
        report = {
            "status": "BLOCKED",
            "reason": str(exc),
            "deployment_allowed": False,
            "telegram_modified": False,
        }
    except Exception:
        report = {
            "status": "BLOCKED",
            "reason": "PIN_AUDIT_UNAVAILABLE",
            "deployment_allowed": False,
            "telegram_modified": False,
        }
    if options.jsonl:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print("UNRELATED PIN AUDIT: " + report["status"])
    return 0 if report["status"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
