#!/usr/bin/env python3
"""Rehearse offline Shelly SQLite recovery in disposable sandboxes."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app  # noqa: E402


def load_verifier():
    """Reuse the production backup checksum and extraction safeguards."""

    file = ROOT / "scripts/verify-backups.py"
    spec = importlib.util.spec_from_file_location("backup_verify", file)
    if spec is None or spec.loader is None:
        raise RuntimeError("backup verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load_verifier()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read noninteractive restore-drill options."""

    parser = argparse.ArgumentParser(
        description="Restore Shelly backups into disposable QA directories."
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("/var/backups/shelly-voltage"),
        help="Directory containing voltage-*.sqlite3.gz files",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of newest copies to rehearse (default: 3)",
    )
    parser.add_argument(
        "--max-size-mib",
        type=int,
        default=64,
        help="Maximum unpacked size of each backup (default: 64)",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Write one machine-readable PASS/FAIL summary",
    )
    return parser.parse_args(argv)


def isolated_config(database: Path) -> app.Config:
    """Build an explicitly disconnected monitor with no Telegram access."""

    with patch.dict(os.environ, {"SHELLY_HOST": "192.0.2.1"}, clear=True):
        source = app.load_config()
    return replace(
        source,
        database_path=database,
        shelly_host="192.0.2.1",
        telegram_token="",
        telegram_chat_id="",
        telegram_status_chat_id="",
        telegram_allowed_user_ids=(),
        telegram_admin_user_ids=(),
    )


def exercise_restored_copy(database: Path) -> tuple[int, str]:
    """Migrate a disposable copy, then validate restored offline state."""

    with closing(sqlite3.connect(database)) as db:
        quick_check = db.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise ValueError("restored SQLite failed quick_check")
        count = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        old_columns = {
            row[1] for row in db.execute("PRAGMA table_info(measurements)")
        }
        old_latest = db.execute(
            "SELECT ts,voltage FROM measurements ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if not old_latest or count < 1:
            raise ValueError("restored measurements missing")
    monitor = app.Monitor(isolated_config(database))
    monitor.storage.initialize()
    with closing(sqlite3.connect(database)) as db:
        new_count = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[
            0
        ]
        latest = db.execute(
            "SELECT ts,voltage FROM measurements WHERE phase='L1' "
            "ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    if new_count != count or not latest:
        raise ValueError("migration lost historic readings")
    if "phase" not in old_columns and latest != old_latest:
        raise ValueError("legacy L1 history changed during migration")
    monitor.restore_phase("L1")
    state = monitor.state.snapshot()
    status, ready = app.readiness(monitor)
    if state["last_success"] != latest[0] or state["voltage"] != latest[1]:
        raise ValueError("last valid reading was not restored")
    if status != 503 or ready.get("ready") is not False:
        raise ValueError("historic backup incorrectly marked ready")
    return int(count), str(state["connectivity"])


def restore_one(index: int, archive: Path, limit: int) -> bool:
    """Verify and restore a backup, then discard every temporary file."""

    if VERIFY.checksum_error(archive):
        logger.error("FAIL copy {}: archive checksum invalid", index)
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="svt-restore-drill-") as tmp:
            target = Path(tmp) / "restored.sqlite3"
            VERIFY.unpack_archive(archive, target, limit)
            target.chmod(0o600)
            rows, state = exercise_restored_copy(target)
    except (OSError, EOFError, ValueError, RuntimeError, sqlite3.Error) as exc:
        logger.error("FAIL copy {}: {}", index, type(exc).__name__)
        return False
    logger.info(
        "PASS copy {}: {} measurements, initial state {}",
        index,
        rows,
        state,
    )
    return True


def report(passed: int, requested: int, jsonl: bool) -> int:
    """Summarize all restored copies without exposing file paths."""

    ok = passed == requested
    result = "PASS" if ok else "FAIL"
    if jsonl:
        print(
            json.dumps(
                {
                    "status": result.lower(),
                    "requested": requested,
                    "passed": passed,
                    "read_only_source": True,
                    "network_calls": 0,
                    "temporary_copies_removed": True,
                },
                sort_keys=True,
            )
        )
    logger.info("{}: {}/{} copies restored", result, passed, requested)
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    """Check all requested archives and return a stable exit status."""

    args = parse_args(argv)
    if args.count < 1 or not 1 <= args.max_size_mib <= 2048:
        logger.error("FAIL: invalid --count or --max-size-mib")
        return 2
    backups = VERIFY.recent_backups(args.backup_dir)
    if backups is None or len(backups) < args.count:
        logger.error("FAIL: fewer backup archives than requested")
        return 2
    limit = args.max_size_mib * 1024 * 1024
    passed = sum(
        restore_one(index, archive, limit)
        for index, archive in enumerate(backups[: args.count], 1)
    )
    return report(passed, args.count, args.jsonl)


if __name__ == "__main__":
    raise SystemExit(main())
