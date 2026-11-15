#!/usr/bin/env python3
"""Check SQLite online backup consistency during synthetic WAL writes."""

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
CONSENT = "OFFLINE_CONCURRENT_WAL_BACKUP_QA_ONLY"


def load_backup_qa() -> object:
    """Reuse real local snapshot and exact offline guest guard helpers."""
    spec = importlib.util.spec_from_file_location("qa_wal_backup_base", BASE)
    if spec is None or spec.loader is None:
        raise RuntimeError("CONCURRENT_QA_BASE_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def writer_thread(
    database: Path,
    stop: threading.Event,
    entered: threading.Event,
    progress: list[int],
    errors: list[str],
) -> None:
    """Commit independent invented rows while the primary has active WAL."""
    try:
        with closing(sqlite3.connect(database, timeout=4)) as conn:
            while not stop.is_set() and progress[0] < 150:
                conn.execute(
                    "INSERT INTO synthetic_samples(volts) VALUES (?)",
                    (222.0 + progress[0] / 1000,),
                )
                conn.commit()
                progress[0] += 1
                entered.set()
                time.sleep(0.004)
    except sqlite3.Error as exc:
        errors.append(type(exc).__name__)
        entered.set()


def snapshot_state(path: Path, qa: object) -> tuple[str, int, int, int]:
    """Read an individual completed backup, not a drifting live count."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return qa.sqlite_state(db)


def validate_arithmetic(state: tuple[str, int, int, int]) -> None:
    """Detect torn synthetic snapshots even when quick_check is successful."""
    quick, rows, total, last = state
    if quick != "ok" or rows <= 420:
        raise RuntimeError("QA_ONLINE_SNAPSHOT_NOT_CONSISTENT")
    if last != rows or total != rows * (rows + 1) // 2:
        raise RuntimeError("QA_SNAPSHOT_TORN_ROW_SEQUENCE")


def restore_each(
    root: Path, qa: object, copies: tuple[Path, Path]
) -> list[tuple[str, int, int, int]]:
    """Bind a checksum and snapshot-specific state before each restore."""
    results = []
    for i, snapshot in enumerate(copies, 1):
        state = snapshot_state(snapshot, qa)
        validate_arithmetic(state)
        manifest = {
            "source_sqlite_state": list(state),
            "local_snapshot_sha256": {snapshot.name: qa.sha256_file(snapshot)},
            "kind": "synthetic_concurrent_writer",
            "physically_independent": False,
            "signature_verified": False,
        }
        destination = root / f"restored-concurrent-{i}.sqlite3"
        recovered = qa.verify_snapshot(snapshot, destination, manifest)
        if recovered != state:
            raise RuntimeError("QA_CONCURRENT_RESTORE_STATE_MISMATCH")
        results.append(recovered)
    return results


def build_result(results: list[tuple[str, int, int, int]]) -> dict:
    """Report snapshot-specific counts without implying release approval."""
    return {
        "result": "PASS_CONCURRENT_WAL_TWO_LOCAL_RESTORES",
        "snapshot_A_rows": results[0][1],
        "snapshot_B_rows": results[1][1],
        "both_integrity_ok": True,
        "both_independent_local_restores": True,
        "no_torn_row_sequence": True,
        "single_nvme_failure_risk": True,
        "real_production_database_touched": False,
        "release_decision": "NO_GO",
    }


def backup_during_writes(root: Path, qa: object) -> dict:
    """Take two online snapshots while another SQLite writer commits."""
    source = qa.make_source(root)
    stopped = threading.Event()
    entered = threading.Event()
    progress = [0]
    errors: list[str] = []
    writer = threading.Thread(
        target=writer_thread,
        args=(root / "running.sqlite3", stopped, entered, progress, errors),
        daemon=False,
    )
    try:
        writer.start()
        if not entered.wait(timeout=4) or errors:
            raise RuntimeError("QA_BACKGROUND_WRITER_NOT_ACTIVE")
        first = root / "concurrent-A.sqlite3"
        second = root / "concurrent-B.sqlite3"
        qa.create_online_backup(source, first)
        time.sleep(0.06)
        qa.create_online_backup(source, second)
        results = restore_each(root, qa, (first, second))
        if results[1][1] <= results[0][1]:
            raise RuntimeError("QA_CONCURRENT_WRITE_NOT_OBSERVED_BETWEEN")
        return build_result(results)
    finally:
        stopped.set()
        if writer.ident is not None:
            writer.join(timeout=5)
            if writer.is_alive():
                raise RuntimeError("QA_BACKGROUND_WRITER_DID_NOT_STOP")
        source.close()
        if errors:
            raise RuntimeError("QA_BACKGROUND_WRITER_SQLITE_FAILED")


def main() -> int:
    """Default inert; require exact networkless LXC114 QA consent."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-disposable", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if not args.run_disposable:
        print("DRY_RUN: no threads, SQLite or files created")
        return 0
    if args.confirm != CONSENT:
        print("BLOCKED: exact offline QA confirmation required")
        return 2
    try:
        qa = load_backup_qa()
        qa.guest_guard()
        with tempfile.TemporaryDirectory(
            prefix="svt-qa-concurrent-wal-", dir="/var/tmp"
        ) as folder:
            result = backup_during_writes(Path(folder), qa)
    except (RuntimeError, sqlite3.Error, OSError) as exc:
        print("QA_BLOCKED", type(exc).__name__, str(exc)[:200])
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
