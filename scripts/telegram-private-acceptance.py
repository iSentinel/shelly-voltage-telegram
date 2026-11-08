"""Explicitly guarded private Telegram send/edit acceptance."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIRM = "PRIVATE_EM50_SEND_EDIT_TEST"


class DenyRedirect(urllib.request.HTTPRedirectHandler):
    """Never redirect a bot-token request."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        """Refuse redirects without exposing the token."""

        raise ValueError("Telegram redirect rejected")


def recipient_id(recipient: str, admin: str) -> int:
    """Match two explicit positive numeric private-user identifiers."""

    values = (recipient, admin)
    if any(
        not v.isascii() or not v.isdecimal() or len(v) > 19 for v in values
    ):
        raise ValueError("invalid private identifier")
    ids = tuple(int(v) for v in values)
    if ids[0] != ids[1] or not 0 < ids[0] < 2**63:
        raise ValueError("recipient is not the approved admin")
    return ids[0]


def validate_admin_allowlist(recipient: int) -> None:
    """Require the recipient to be a configured production admin."""

    configured = os.getenv("TELEGRAM_ADMIN_USER_IDS", "")
    allowed = {
        value.strip()
        for value in configured.split(",")
        if value.strip().isascii() and value.strip().isdecimal()
    }
    if str(recipient) not in allowed:
        raise ValueError("recipient missing from configured admin allowlist")


def read_token(path: str) -> str:
    """Read a protected file, never from command-line token text."""

    file = Path(path)
    info = file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("token must be a regular file")
    if info.st_mode & 0o077 or info.st_size > 256:
        raise ValueError("unsafe token permissions or size")
    value = file.read_text(encoding="utf-8").strip()
    if not value or "/" in value or len(value) > 200:
        raise ValueError("invalid token")
    return value


def api(token: str, method: str, payload: dict) -> dict:
    """Call one of four allowed Telegram Bot API methods."""

    if method not in {"getMe", "getChat", "sendMessage", "editMessageText"}:
        raise ValueError("method is not allowed")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), DenyRedirect()
    )
    try:
        with client.open(request, timeout=8) as response:
            raw = response.read(16385)
    except (OSError, urllib.error.URLError):
        raise ValueError("Telegram request failed") from None
    if len(raw) > 16384:
        raise ValueError("Telegram response too large")
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid Telegram JSON") from None
    if not isinstance(document, dict) or document.get("ok") is not True:
        raise ValueError("Telegram API rejected request")
    result = document.get("result")
    if not isinstance(result, dict):
        raise ValueError("unexpected Telegram response shape")
    return result


def verify_private(token: str, recipient: int) -> None:
    """Read-only checks for bot identity and exact private chat type."""

    if api(token, "getMe", {}).get("is_bot") is not True:
        raise ValueError("Telegram identity is not a bot")
    chat = api(token, "getChat", {"chat_id": recipient})
    if chat.get("type") != "private" or chat.get("id") != recipient:
        raise ValueError("recipient is not the exact private chat")


def send_and_edit(token: str, recipient: int) -> None:
    """Send and edit only a fresh silent, explicitly labelled test."""

    original = "EM-50 QA TEST: no power outage is asserted."
    result = api(
        token,
        "sendMessage",
        {"chat_id": recipient, "text": original, "disable_notification": True},
    )
    message_id = result.get("message_id")
    if type(message_id) is not int or message_id < 1:
        raise ValueError("Telegram did not confirm a message ID")
    api(
        token,
        "editMessageText",
        {
            "chat_id": recipient,
            "message_id": message_id,
            "text": original + " Edit test completed.",
        },
    )


def run(args: argparse.Namespace) -> str:
    """Never assume acceptance unless explicit write consent is present."""

    if args.dry_run:
        if args.recipient_id or args.admin_id:
            recipient_id(args.recipient_id, args.admin_id)
        return "DRY_RUN no_network=true writes=0"
    target = recipient_id(args.recipient_id, args.admin_id)
    if args.execute and args.confirm != CONFIRM:
        raise ValueError("explicit private-send confirmation required")
    validate_admin_allowlist(target)
    token = read_token(args.token_file)
    verify_private(token, target)
    if args.check:
        return "CHECK exact_private_admin=true writes=0"
    send_and_edit(token, target)
    return "PRIVATE_SEND_EDIT_DONE human_visibility_still_pending=true"


def main() -> int:
    """Provide non-secret PASS/FAIL with standard exit codes."""

    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--execute", action="store_true")
    parser.add_argument("--recipient-id", default="")
    parser.add_argument("--admin-id", default="")
    parser.add_argument("--token-file", default="/run/secrets/telegram_token")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        print("PASS", run(args))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print("FAIL private_telegram_qa", type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
