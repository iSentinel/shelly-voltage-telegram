#!/usr/bin/env python3
"""Confirm an existing Telegram text status once, under full rollout gates."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import stat
import subprocess
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PENDING_KEY = "telegram_legacy_text_confirm_pending"


class VerificationError(Exception):
    """Safe reason code that never contains Telegram credentials or IDs."""


def gate_module():
    """Reuse the audited, read-only snapshot and Owner Approval checks."""

    path = Path(__file__).with_name("graphic-rollout-readiness.py")
    spec = importlib.util.spec_from_file_location(
        "graphic_approval_guard", path
    )
    if spec is None or spec.loader is None:
        raise VerificationError("GUARD_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments() -> argparse.Namespace:
    """Make a one-time public Telegram edit an explicit action."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--database", type=Path, required=True)
    cli.add_argument("--snapshot", type=Path, required=True)
    cli.add_argument("--installed-version-file", type=Path, required=True)
    cli.add_argument("--owner-approval-file", type=Path, required=True)
    cli.add_argument("--token-file", type=Path, required=True)
    cli.add_argument("--chat-env", default="TELEGRAM_STATUS_CHAT_ID")
    cli.add_argument("--execute", action="store_true")
    cli.add_argument("--jsonl", action="store_true")
    return cli.parse_args()


def writer_stopped() -> bool:
    """Require both systemd Compose and the Docker writer to be stopped."""

    try:
        unit = subprocess.run(
            ["systemctl", "is-active", "shelly-voltage-compose.service"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        docker = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}",
                "shelly-voltage-bot",
            ],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (
        unit.stdout.strip() in {"inactive", "failed"}
        and docker.returncode == 0
        and docker.stdout.strip() == "false"
    )


def safe_token(path: Path, gate) -> str:
    """Read the private Bot API token without logging or printing it."""

    if not gate.safe_regular_file(path):
        raise VerificationError("TOKEN_PATH_UNSAFE")
    try:
        info = path.stat()
        if (
            info.st_uid not in (os.geteuid(), 10001)
            or stat.S_IMODE(info.st_mode) != 0o600
            or not 0 < info.st_size <= 512
        ):
            raise VerificationError("TOKEN_PATH_UNSAFE")
        token = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise VerificationError("TOKEN_UNAVAILABLE") from None
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{30,}", token):
        raise VerificationError("TOKEN_INVALID")
    return token


def current_status(database: Path) -> tuple[int, float, float]:
    """Read one exact unmigrated status ID and last historical sample."""

    try:
        with closing(
            sqlite3.connect(
                database.resolve().as_uri() + "?mode=ro", uri=True, timeout=4
            )
        ) as db:
            if db.execute("pragma quick_check").fetchone()[0] != "ok":
                raise VerificationError("DB_CORRUPT")
            meta = dict(
                db.execute(
                    "select key,value from meta where key in "
                    "('telegram_status_message_id',"
                    "'telegram_status_media_identity',"
                    "'telegram_graphic_publish_pending',"
                    "'telegram_legacy_text_confirm_pending')"
                ).fetchall()
            )
            row = db.execute(
                "select ts,voltage from measurements "
                "where phase='L1' order by id desc limit 1"
            ).fetchone()
    except (sqlite3.Error, OSError):
        raise VerificationError("DB_UNAVAILABLE") from None
    saved = meta.get("telegram_status_message_id")
    if (
        not isinstance(saved, str)
        or not re.fullmatch(r"[0-9]{1,20}", saved)
        or int(saved) == 0
    ):
        raise VerificationError("STATUS_ID_INVALID")
    if "telegram_status_media_identity" in meta:
        raise VerificationError("MEDIA_IDENTITY_ALREADY_SET")
    if "telegram_graphic_publish_pending" in meta or PENDING_KEY in meta:
        raise VerificationError("PUBLICATION_UNRESOLVED")
    if not row or not isinstance(row[1], (float, int)):
        raise VerificationError("NO_HISTORICAL_SAMPLE")
    return int(saved), float(row[0]), float(row[1])


def validate_snapshot(args: argparse.Namespace, gate) -> None:
    """Demand a recent exact-version source+full-SQLite rollback."""

    if gate.snapshot_issues(args.snapshot, 2):
        raise VerificationError("SNAPSHOT_INVALID")
    if gate.version_issues(args.snapshot, args.installed_version_file):
        raise VerificationError("SNAPSHOT_VERSION_MISMATCH")
    _, archive = gate.snapshot_archives(args.snapshot)
    if not gate.run_archive_validator(
        "validate-sqlite-archive.py",
        ["--archive", str(archive), "--compare-db", str(args.database)],
    ):
        raise VerificationError("SNAPSHOT_PARITY_FAIL")


def confirmation_text(ts: float, voltage: float) -> str:
    """Describe a temporary text check without claiming live voltage."""

    when = datetime.fromtimestamp(ts, ZoneInfo("Europe/Kyiv"))
    clock = datetime.now(ZoneInfo("Europe/Kyiv"))
    return (
        "⚡ Напруга мережі · L\\n"
        "🛠 Перевірка безпечного переходу на графічний статус\\n"
        f"Останній запис: {voltage:.1f} В · {when:%d.%m %H:%M} "
        "(історичні дані)\\n"
        f"🕐 Перевірено {clock:%d.%m %H:%M:%S}\\n"
        "ℹ️ Це тимчасове повідомлення перед оновленням дизайну"
    ).replace("\\n", "\n")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a Bot API token to another server."""

    def redirect_request(self, request, fp, code, message, headers, newurl):
        """Refuse all HTTP redirects."""

        return None


def verify_receipt(result: object, mid: int, chat: str, body: str) -> None:
    """Only a Telegram Message with exact text and IDs confirms identity."""

    if not isinstance(result, dict) or result.get("ok") is not True:
        raise VerificationError("TELEGRAM_EDIT_REJECTED")
    message = result.get("result")
    if (
        not isinstance(message, dict)
        or type(message.get("message_id")) is not int
    ):
        raise VerificationError("TELEGRAM_RECEIPT_INVALID")
    target = message.get("chat")
    if message["message_id"] != mid or not isinstance(target, dict):
        raise VerificationError("TELEGRAM_RECEIPT_MISMATCH")
    if str(target.get("id")) != chat or message.get("text") != body:
        raise VerificationError("TELEGRAM_RECEIPT_MISMATCH")
    if message.get("photo") is not None:
        raise VerificationError("TELEGRAM_MEDIA_MISMATCH")


def edit_text_once(token: str, chat: str, mid: int, body: str) -> None:
    """Use editMessageText once; never send, delete, pin or retry."""

    raw = json.dumps(
        {"chat_id": chat, "message_id": mid, "text": body},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/editMessageText",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=15
        ) as response:
            payload = response.read(65537)
            if response.status != 200 or len(payload) > 65536:
                raise VerificationError("TELEGRAM_REPLY_INVALID")
        decoded = json.loads(payload)
    except (OSError, urllib.error.URLError, ValueError):
        raise VerificationError("TELEGRAM_REPLY_UNCERTAIN") from None
    verify_receipt(decoded, mid, chat, body)


def reserve_confirmation(args, mid: int) -> None:
    """Persist an irreversible intent before any Telegram network call."""

    try:
        with (
            closing(sqlite3.connect(str(args.database), timeout=4)) as db,
            db,
        ):
            db.execute("BEGIN IMMEDIATE")
            saved = db.execute(
                "select value from meta where key='telegram_status_message_id'"
            ).fetchone()
            markers = db.execute(
                "select key from meta where key in (?,?,?)",
                (
                    "telegram_status_media_identity",
                    "telegram_graphic_publish_pending",
                    PENDING_KEY,
                ),
            ).fetchone()
            if saved != (str(mid),) or markers:
                raise VerificationError("STATUS_ID_CHANGED")
            db.execute(
                "insert into meta(key,value) values (?,?)",
                (PENDING_KEY, "pending"),
            )
    except sqlite3.Error:
        raise VerificationError("RESERVATION_FAILED") from None


def finish_confirmation(args, mid: int, chat: str) -> None:
    """Mark text only after receipt; clear pending atomically."""

    try:
        with (
            closing(sqlite3.connect(str(args.database), timeout=4)) as db,
            db,
        ):
            db.execute("BEGIN IMMEDIATE")
            status = db.execute(
                "select value from meta where key='telegram_status_message_id'"
            ).fetchone()
            pending = db.execute(
                "select value from meta where key=?", (PENDING_KEY,)
            ).fetchone()
            if status != (str(mid),) or pending != ("pending",):
                raise VerificationError("STATUS_ID_CHANGED")
            db.execute(
                "insert into meta(key,value) values (?,?)",
                ("telegram_status_media_identity", f"text:{chat}:{mid}"),
            )
            db.execute("delete from meta where key=?", (PENDING_KEY,))
    except sqlite3.Error:
        raise VerificationError("DB_COMMIT_UNCERTAIN") from None


def verify_and_record(
    args, mid: int, chat: str, token: str, body: str
) -> None:
    """Prevent blind retries after a request or client process crash."""

    if not writer_stopped():
        raise VerificationError("WRITER_RESTARTED")
    reserve_confirmation(args, mid)
    if not writer_stopped():
        raise VerificationError("WRITER_RESTARTED")
    edit_text_once(token, chat, mid, body)
    try:
        if not writer_stopped():
            raise VerificationError("WRITER_RESTARTED_AFTER_EDIT")
        finish_confirmation(args, mid, chat)
    except VerificationError as exc:
        if str(exc) == "STATUS_ID_CHANGED":
            raise VerificationError("STATUS_CHANGED_AFTER_EDIT") from None
        raise
    except Exception:
        raise VerificationError("DB_COMMIT_UNCERTAIN") from None


def run(args: argparse.Namespace) -> dict:
    """Fail closed on identity, Owner Approval, snapshot or writer state."""

    guard = gate_module()
    if not guard.owner_approval_verified(args.owner_approval_file):
        raise VerificationError("OWNER_APPROVAL_INVALID")
    if not guard.safe_regular_file(args.database):
        raise VerificationError("DATABASE_PATH_UNSAFE")
    if args.execute and args.chat_env != "TELEGRAM_STATUS_CHAT_ID":
        raise VerificationError("CHAT_ENV_NOT_PRODUCTION")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", args.chat_env):
        raise VerificationError("CHAT_ENV_INVALID")
    chat = os.getenv(args.chat_env, "")
    if not re.fullmatch(r"-?[0-9]{1,20}", chat) or int(chat) == 0:
        raise VerificationError("CHAT_TARGET_INVALID")
    mid, measured_at, voltage = current_status(args.database)
    if not writer_stopped():
        raise VerificationError("WRITER_NOT_STOPPED")
    validate_snapshot(args, guard)
    if not args.execute:
        return {"status": "DRY_RUN_PASS", "telegram_edited": False}
    token = safe_token(args.token_file, guard)
    body = confirmation_text(measured_at, voltage)
    verify_and_record(args, mid, chat, token, body)
    return {"status": "PASS", "telegram_edited": True}


def main() -> int:
    """Emit a machine-readable and secret-free execution result."""

    args = arguments()
    try:
        result = run(args)
    except VerificationError as exc:
        reason = str(exc)
        uncertain = {
            "TELEGRAM_REPLY_UNCERTAIN",
            "TELEGRAM_REPLY_INVALID",
            "TELEGRAM_RECEIPT_INVALID",
            "TELEGRAM_RECEIPT_MISMATCH",
            "TELEGRAM_MEDIA_MISMATCH",
            "DB_COMMIT_UNCERTAIN",
            "STATUS_CHANGED_AFTER_EDIT",
            "WRITER_RESTARTED_AFTER_EDIT",
        }
        result = {
            "status": "BLOCKED",
            "reason": reason,
            "telegram_edited": (
                "UNKNOWN" if args.execute and reason in uncertain else False
            ),
        }
    except Exception:
        result = {
            "status": "BLOCKED",
            "reason": "UNEXPECTED_ERROR",
            "telegram_edited": False,
        }
    if args.jsonl:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print("LEGACY STATUS CONFIRMATION: " + result["status"])
        if "reason" in result:
            print("Reason: " + result["reason"])
    return 0 if result["status"] in {"PASS", "DRY_RUN_PASS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
