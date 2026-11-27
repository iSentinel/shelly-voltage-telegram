#!/usr/bin/env python3
"""Review synthetic report delivery evidence; never send a message."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import closing

DAILY = "daily_summary_day"
PENDING = "telegram_report_pending:telegram_weekly_private_sent_7"
WEEKLY = "telegram_weekly_private_sent_8"


def read_events(root):
    """Read a bounded regular synthetic API event log."""
    path = root / "data/accepted"
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_uid != 10001
        or before.st_mode & 0o022
        or not 0 < before.st_size <= 4096
    ):
        raise ValueError("unsafe synthetic event log")
    raw = path.read_bytes()
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError("synthetic event log changed")
    return raw


def report_metadata(root):
    """Read only synthetic report markers from the isolated database."""
    path = root / "data/qa.sqlite3"
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    ) as db:
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT key,value FROM meta WHERE key='daily_summary_day' "
            "OR key GLOB 'telegram_report_pending:*' "
            "OR key GLOB 'telegram_weekly_private_sent_*' ORDER BY key"
        ).fetchall()
    return dict(rows)


def verify_events(mode, raw, meta):
    """Require the known exact synthetic transition and no duplicate sends."""
    expected = {
        "paused": b"ordinary-paused\n",
        "enabled": b"ordinary-paused\nresumed-report\nordinary-resumed\n",
    }
    if mode not in expected or raw != expected[mode]:
        raise ValueError("synthetic delivery sequence mismatch")
    markers = {DAILY, PENDING}
    if mode == "enabled":
        markers.add(WEEKLY)
    if (
        set(meta) != markers
        or meta[DAILY] != "2026-11-27"
        or not isinstance(meta[PENDING], str)
        or not meta[PENDING].startswith("2026-W48:")
        or (mode == "enabled" and meta[WEEKLY] != "2026-W48")
    ):
        raise ValueError("synthetic report marker mismatch")


def verify_predecessor(start, root, prior_token, prior_sha, meta, journal):
    """Bind resumed delivery to the exact earlier paused receipt."""
    path = start.path_for(root, prior_token, "delivery")
    raw = start.read_bytes(path)
    if start.TX.sha(raw) != prior_sha:
        raise ValueError("paused delivery receipt mismatch")
    prior = json.loads(raw, object_pairs_hook=start.OP.PLAN.unique_object)
    if (
        prior["mode"] != "paused"
        or prior["history"] != journal["baseline"]
        or prior.get("synthetic_api_only") is not True
        or prior.get("telegram_delivery_verified") is not False
        or prior["deployment_authorized"] is not False
        or prior["pending_sha256"] != start.TX.sha(meta[PENDING].encode())
        or prior["event_sha256"] != start.TX.sha(b"ordinary-paused\n")
    ):
        raise ValueError("paused predecessor mismatch")
    return prior


def stopped_history(start, root, accepted, journal, mode):
    """Require the accepted prefix and unchanged paused metadata."""
    history = start.TX.history_snapshot(root)
    old = accepted["history"]
    prefix = start.TX.history_snapshot(root, old["last_id"])
    if any(prefix[k] != old[k] for k in ("device", "inode", "prefix_sha256")):
        raise ValueError("accepted history changed")
    if (
        mode == "paused"
        and history["metadata_sha256"]
        != journal["baseline"]["metadata_sha256"]
    ):
        raise ValueError("paused metadata changed")
    return history


def evidence(
    start, root, token, mode, acceptance_sha, prior_token=None, prior_sha=None
):
    """Persist exact synthetic delivery evidence, with a paused predecessor."""
    acceptance = start.read_bytes(start.path_for(root, token, "acceptance"))
    if start.TX.sha(acceptance) != acceptance_sha:
        raise ValueError("acceptance anchor mismatch")
    accepted = json.loads(
        acceptance, object_pairs_hook=start.OP.PLAN.unique_object
    )
    journal = start.OP.read(root, token)
    if (
        accepted["qa_runtime_verified"] is not True
        or accepted["deployment_authorized"] is not False
        or journal["phase"] != "released"
        or journal["target"] != mode
    ):
        raise ValueError("unaccepted delivery context")
    history = stopped_history(start, root, accepted, journal, mode)
    raw, meta = read_events(root), report_metadata(root)
    verify_events(mode, raw, meta)
    if mode == "enabled":
        verify_predecessor(start, root, prior_token, prior_sha, meta, journal)
    elif prior_token is not None or prior_sha is not None:
        raise ValueError("unexpected paused predecessor")
    return {
        "schema": 1,
        "history": history,
        "mode": mode,
        "acceptance_sha256": acceptance_sha,
        "event_sha256": start.TX.sha(raw),
        "pending_sha256": start.TX.sha(meta[PENDING].encode()),
        "metadata_sha256": start.hash_object(meta),
        "predecessor_sha256": prior_sha,
        "synthetic_api_only": True,
        "telegram_delivery_verified": False,
        "deployment_authorized": False,
    }


def persist(start, root, token, record):
    """Write once; repeated review must reproduce identical evidence."""
    path = start.path_for(root, token, "delivery")
    if os.path.lexists(path):
        previous = json.loads(
            start.read_bytes(path),
            object_pairs_hook=start.OP.PLAN.unique_object,
        )
        if previous != record:
            raise ValueError("delivery evidence changed")
    else:
        start.write_once(path, record)
    return start.TX.sha(start.read_bytes(path))
