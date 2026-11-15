#!/usr/bin/env python3
"""Rehearse a final local WAL cutover only after one synthetic writer stops."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

BASE = Path(__file__).with_name("disposable-local-wal-rollback-qa.py")
CONSENT = "OFFLINE_QUIESCED_FINAL_WAL_CUTOVER_QA_ONLY"


def load_qa() -> object:
    """Reuse the exact LXC114 guard and synthetic SQLite backup API."""
    spec = importlib.util.spec_from_file_location("qa_quiesced_backup", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError("QA_CUTOVER_HELPER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def synthetic_writer(
    database: Path,
    stop: threading.Event,
    entered: threading.Event,
    progress: list[int],
    errors: list[str],
) -> None:
    """Commit invented rows until explicitly told to quiesce."""
    try:
        with closing(sqlite3.connect(database, timeout=3)) as db:
            while not stop.is_set() and progress[0] < 80:
                db.execute(
                    "INSERT INTO synthetic_samples(volts) VALUES (?)",
                    (221.0 + progress[0] / 100,),
                )
                db.commit()
                progress[0] += 1
                entered.set()
                time.sleep(0.003)
    except sqlite3.Error as exc:
        errors.append(type(exc).__name__)
        entered.set()


def require_quiesced(
    stop: threading.Event, writer: threading.Thread, errors: list[str]
) -> None:
    """Refuse backups while the synthetic writer may still be alive."""
    if not stop.is_set() or writer.is_alive() or errors:
        raise RuntimeError("QA_FINAL_CUTOVER_OLD_WRITER_NOT_QUIESCENT")


def stop_writer(
    stop: threading.Event, writer: threading.Thread, errors: list[str]
) -> None:
    """Signal the exact owned thread and verify its full exit."""
    stop.set()
    if writer.ident is not None:
        writer.join(timeout=5)
    require_quiesced(stop, writer, errors)


def backup_after_stop(root: Path, source: sqlite3.Connection, qa: object):
    """Snapshot only an immutable no-longer-writing synthetic boundary."""
    expected = qa.sqlite_state(source)
    if expected[1] <= 420:
        raise RuntimeError("QA_FINAL_CUTOVER_WRITER_NEVER_COMMITTED")
    copies = (root / "cutover-A.sqlite3", root / "cutover-B.sqlite3")
    for path in copies:
        qa.create_online_backup(source, path)
    states = []
    for i, item in enumerate(copies, 1):
        manifest = {
            "source_sqlite_state": list(expected),
            "local_snapshot_sha256": {item.name: qa.sha256_file(item)},
        }
        state = qa.verify_snapshot(
            item, root / f"restored-cutover-{i}.sqlite3", manifest
        )
        states.append(state)
    if states != [expected, expected]:
        raise RuntimeError("QA_FINAL_CUTOVER_RESTORES_DIFFER")
    return expected, copies


def exercise(root: Path, qa: object) -> dict:
    """Require cooperative stop/join before local twin snapshots."""
    source = qa.make_source(root)
    stopped = threading.Event()
    entered = threading.Event()
    progress = [0]
    errors: list[str] = []
    thread = threading.Thread(
        target=synthetic_writer,
        args=(root / "running.sqlite3", stopped, entered, progress, errors),
        daemon=False,
    )
    try:
        thread.start()
        if not entered.wait(timeout=4):
            raise RuntimeError("QA_FINAL_CUTOVER_WRITER_NEVER_STARTED")
        time.sleep(0.06)
        stop_writer(stopped, thread, errors)
        require_quiesced(stopped, thread, errors)
        state, copies = backup_after_stop(root, source, qa)
        return {
            "status": "PASS_QUIESCED_LOCAL_WAL_CUTOVER",
            "rows_at_cutover": state[1],
            "two_local_copies": len(copies),
            "both_restores_match_final_boundary": True,
            "old_synthetic_writer_joined": True,
            "one_nvme_only": True,
            "every_real_production_start_owner_frozen": False,
            "release_decision": "NO_GO",
        }
    finally:
        stopped.set()
        if thread.ident is not None:
            thread.join(timeout=5)
        source.close()
        if thread.is_alive():
            raise RuntimeError("QA_FINAL_CUTOVER_WRITER_THREAD_LEAK")


def main() -> int:
    """Remain inert without exact isolated offline QA consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no writer, SQLite or backup files created")
        return 0
    if args.confirm != CONSENT:
        print("BLOCKED: exact offline LXC114 consent required")
        return 2
    try:
        qa = load_qa()
        qa.guest_guard()
        with tempfile.TemporaryDirectory(
            prefix="svt-qa-final-local-cutover-", dir="/var/tmp"
        ) as temp:
            result = exercise(Path(temp), qa)
    except (RuntimeError, sqlite3.Error, OSError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:200])
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
