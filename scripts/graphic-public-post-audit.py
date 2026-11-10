#!/usr/bin/env python3
"""Read-only public Telegram post inspection bound to existing SQLite ID."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

MAX_PUBLIC_HTML = 131072
PUBLIC_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,31}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


VOID_TAGS = frozenset(("area", "br", "hr", "img", "input", "link", "meta"))


class PostScanner(HTMLParser):
    """Inspect only the exact embedded Telegram post, not surrounding HTML."""

    def __init__(self, expected: str):
        """Capture one expected public username/message ID pair."""

        super().__init__(convert_charrefs=True)
        self.expected = expected
        self.depth = 0
        self.target_depth: int | None = None
        self.post_ids: list[str] = []
        self.markers: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list) -> None:
        """Record media classes only inside the exact post wrapper."""

        attributes = dict(attrs)
        post_id = attributes.get("data-post")
        if post_id is not None:
            self.post_ids.append(post_id)
            if tag == "div" and post_id == self.expected:
                self.target_depth = self.depth
        if self.target_depth is not None:
            self.markers.update(
                word
                for word in attributes.get("class", "").split()
                if word.startswith("tgme_widget_message_")
            )
        if tag not in VOID_TAGS:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Leave the current post when its wrapper ends."""

        if tag in VOID_TAGS:
            return
        self.depth = max(0, self.depth - 1)
        if self.target_depth is not None and self.depth <= self.target_depth:
            self.target_depth = None


def parse_args() -> argparse.Namespace:
    """Accept one live getChat+public-page flow or an offline fixture."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--chat-env", default="TELEGRAM_STATUS_CHAT_ID")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--token-file", type=Path)
    mode.add_argument("--html-file", type=Path)
    parser.add_argument("--public-username")
    parser.add_argument("--jsonl", action="store_true")
    return parser.parse_args()


def shared_reader():
    """Reuse the existing safe read-only SQLite and Telegram getChat checks."""

    source = Path(__file__).with_name("graphic-pinned-media-audit.py")
    spec = importlib.util.spec_from_file_location(
        "public_status_shared", source
    )
    if spec is None or spec.loader is None:
        raise ValueError("READER_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_chat(response: dict, chat_id: str) -> str:
    """Bind a public username to the target Bot API channel ID."""

    if response.get("ok") is not True:
        raise ValueError("GETCHAT_UNVERIFIED")
    info = response.get("result")
    if not isinstance(info, dict) or info.get("type") != "channel":
        raise ValueError("CHANNEL_MISMATCH")
    if str(info.get("id")) != chat_id:
        raise ValueError("CHANNEL_MISMATCH")
    username = info.get("username")
    if not isinstance(username, str) or not PUBLIC_NAME.fullmatch(username):
        raise ValueError("PUBLIC_USERNAME_UNAVAILABLE")
    return username


def fetch_public_post(username: str, status_id: str) -> str:
    """Fetch only t.me's embed page; never invoke a Bot API write method."""

    url = f"https://t.me/{username}/{status_id}?embed=1"
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.hostname != "t.me" or final_url.scheme != "https":
                raise ValueError("PUBLIC_PAGE_REDIRECT")
            document = response.read(MAX_PUBLIC_HTML + 1)
    except ValueError:
        raise
    except Exception:
        raise ValueError("PUBLIC_PAGE_UNAVAILABLE") from None
    if len(document) > MAX_PUBLIC_HTML:
        raise ValueError("PUBLIC_PAGE_TOO_LARGE")
    return document.decode("utf-8", errors="replace")


def determine_kind(html: str, username: str, status_id: str) -> str:
    """Reject any unmatched, missing, or ambiguous message media markup."""

    scanner = PostScanner(f"{username}/{status_id}")
    scanner.feed(html)
    if scanner.post_ids != [scanner.expected]:
        raise ValueError("EXACT_POST_MISMATCH")
    text = "tgme_widget_message_text" in scanner.markers
    photo = bool(
        scanner.markers.intersection(
            {"tgme_widget_message_photo_wrap", "tgme_widget_message_photo"}
        )
    )
    unsupported = bool(
        scanner.markers.intersection(
            {
                "tgme_widget_message_video_player",
                "tgme_widget_message_document",
            }
        )
    )
    if unsupported or text == photo:
        raise ValueError("POST_MEDIA_AMBIGUOUS")
    return "text" if text else "photo"


def validate_consistency(
    reader,
    args: argparse.Namespace,
    previous: dict,
    check: str,
    kind: str,
    channel: str,
    message_id: str,
) -> None:
    """Refuse a concurrent status change or contrary persisted media."""

    if reader.load_status(args.database) != (previous, check):
        raise ValueError("STATUS_CHANGED_DURING_AUDIT")
    marker = previous.get("telegram_status_media_identity")
    if marker is not None and marker != f"{kind}:{channel}:{message_id}":
        raise ValueError("MEDIA_MARKER_CONFLICT")


def audit(args: argparse.Namespace) -> dict:
    """Keep public HTML evidence provisional and never alter production."""

    if not ENV_NAME.fullmatch(args.chat_env):
        raise ValueError("ENV_INVALID")
    shared = shared_reader()
    chat_id = os.getenv(args.chat_env, "")
    meta, integrity = shared.load_status(args.database)
    if integrity != "ok":
        raise ValueError("DATABASE_INVALID")
    status_id = shared.exact_identity(meta, chat_id)
    live = args.token_file is not None
    if live:
        if args.public_username is not None:
            raise ValueError("LIVE_USERNAME_OVERRIDE_REFUSED")
        username = verify_chat(
            shared.request_chat(args.token_file, chat_id), chat_id
        )
        html = fetch_public_post(username, status_id)
    else:
        username = args.public_username
        if not username or not PUBLIC_NAME.fullmatch(username):
            raise ValueError("USERNAME_INVALID")
        html = shared.read_bounded_file(args.html_file).decode(
            "utf-8", errors="replace"
        )
    kind = determine_kind(html, username, status_id)
    validate_consistency(
        shared, args, meta, integrity, kind, chat_id, status_id
    )
    return {
        "status": "PUBLIC_POST_MATCH_FOR_REVIEW"
        if live
        else "FIXTURE_MATCH_ONLY",
        "media_kind": kind,
        "live_page_checked": live,
        "deployment_allowed": False,
        "sqlite_modified": False,
        "telegram_posts_or_edits": False,
    }


def main() -> int:
    """Print non-sensitive PASS/BLOCKED and a stable exit code."""

    args = parse_args()
    try:
        result = audit(args)
    except Exception as exc:
        reason = str(exc) if type(exc) is ValueError else "AUDIT_UNAVAILABLE"
        if not re.fullmatch(r"[A-Z_]{3,70}", reason):
            reason = "AUDIT_UNAVAILABLE"
        result = {
            "status": "BLOCKED",
            "reason": reason,
            "deployment_allowed": False,
            "sqlite_modified": False,
            "telegram_posts_or_edits": False,
        }
    if args.jsonl:
        print(json.dumps(result, separators=(",", ":")))
    else:
        print("PUBLIC STATUS POST AUDIT: " + result["status"])
        if result["status"] == "BLOCKED":
            print("Reason: " + result["reason"])
    return 2 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
