"""Audit natural Shelly recovery without fabricating Telegram delivery."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent local readiness requests from redirecting off-host."""

    def redirect_request(self, req, fp, code, msg, headers, url):
        """Block every redirect without following it."""

        raise ValueError("readiness redirect is forbidden")


def local_json(url: str, expected: str) -> dict:
    """Read only bounded local HTTP endpoints without credentials."""

    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("only a local health endpoint is permitted")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect()
    )
    with opener.open(url, timeout=5) as response:
        payload = response.read(65537)
    if len(payload) > 65536:
        raise ValueError("health response too large")
    decoded = json.loads(payload)
    if not isinstance(decoded, dict):
        raise ValueError("health JSON must be an object")
    return decoded


def sample_counts(
    db: sqlite3.Connection,
    phase: str,
    start: float,
    ended: float | None,
    grace: int,
) -> dict[str, int]:
    """Count physical samples by outage boundary without fetching values."""

    early_end = max(start, (ended or start) - grace)
    queries = {
        "early_count": (
            "SELECT COUNT(*) FROM measurements "
            "WHERE phase=? AND ts>=? AND ts<?",
            (phase, start, early_end),
        ),
        "after_count": (
            "SELECT COUNT(*) FROM measurements WHERE phase=? AND ts>=?",
            (phase, ended or start),
        ),
        "zero_count": (
            "SELECT COUNT(*) FROM measurements "
            "WHERE phase=? AND ts>=? AND voltage=0",
            (phase, start),
        ),
    }
    return {
        name: db.execute(sql, params).fetchone()[0]
        for name, (sql, params) in queries.items()
    }


def read_state(db_path: Path, grace: int) -> dict:
    """Read latest connectivity outage and bounded measurement counts."""

    if not db_path.is_file():
        raise ValueError("SQLite database missing")
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
        # Incident, sample counts and metadata must share one WAL snapshot.
        db.execute("BEGIN")
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        outage = db.execute(
            "SELECT id,started_at,ended_at,phase FROM outages "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if outage is None:
            raise ValueError("no recorded Shelly connectivity incident")
        ident, start, ended, phase = outage
        counts = sample_counts(db, phase, start, ended, grace)
        active = db.execute(
            "SELECT COUNT(*) FROM outages WHERE ended_at IS NULL"
        ).fetchone()[0]
        status_saved = db.execute(
            "SELECT EXISTS(SELECT 1 FROM meta "
            "WHERE key='telegram_status_message_id' AND value!='')"
        ).fetchone()[0]
        db.rollback()
    return {
        "integrity": integrity,
        "outage_id": ident,
        "start": start,
        "end": ended,
        "phase": phase,
        **counts,
        "active": active,
        "status_saved": bool(status_saved),
    }


def check_recovery(
    state: dict, health: dict, ready: dict, now: float, grace: int
) -> None:
    """Require a real closed incident with a fresh physical reading."""

    started, ended = state["start"], state["end"]
    if (
        state["integrity"] != "ok"
        or ended is None
        or not 0 < started <= ended <= now
        or state["active"]
        or state["early_count"]
        or state["zero_count"]
        or state["after_count"] < 1
    ):
        raise ValueError("incident history failed recovery checks")
    age = now - float(health["last_success"])
    if (
        health.get("connectivity") != "online"
        or ready.get("ready") is not True
        or not 0 <= age <= 120
        or float(health["last_success"]) < ended
        or now - ended > 48 * 3600
        or not state["status_saved"]
    ):
        raise ValueError("current Shelly or status readiness failed")
    voltage = health.get("voltage")
    if (
        type(voltage) not in {int, float}
        or not math.isfinite(voltage)
        or not 0 < voltage < 500
    ):
        raise ValueError("fresh Shelly voltage is not physically valid")


def report(state: dict, health: dict, now: float, grace: int) -> dict:
    """Return non-sensitive evidence, never a Telegram delivery claim."""

    ended = datetime.fromtimestamp(state["end"], KYIV)
    started = datetime.fromtimestamp(state["start"], KYIV)
    return {
        "status": "PASS",
        "outage_id": state["outage_id"],
        "phase": state["phase"],
        "started_at": started.isoformat(timespec="seconds"),
        "ended_at": ended.isoformat(timespec="seconds"),
        "duration_seconds": round(state["end"] - state["start"], 1),
        "recovery_grace_seconds": grace,
        "measurements_during_offline": state["early_count"],
        "measurements_after_recovery": state["after_count"],
        "synthetic_zero_samples": state["zero_count"],
        "status_id_saved": state["status_saved"],
        "shelly_sample_age_seconds": round(
            now - float(health["last_success"]), 1
        ),
        "telegram_channel_edit_visible": "NOT_VERIFIED",
        "private_receipt_not_revalidated": True,
        "release_authorized": False,
    }


def pending_record(state: dict, now: float) -> dict | None:
    """Report an intact open outage without claiming recovery."""

    if state["end"] is not None:
        return None
    valid = (
        state["integrity"] == "ok"
        and state["active"] == 1
        and 0 < state["start"] <= now
        and state["zero_count"] == 0
        and state["status_saved"]
    )
    if not valid:
        raise ValueError("open outage evidence is inconsistent")
    return {
        "status": "PENDING",
        "reason": (
            "open_incident_with_unconfirmed_samples"
            if state["after_count"]
            else "shelly_connectivity_loss_open"
        ),
        "outage_id": state["outage_id"],
        "phase": state["phase"],
        "mains_restoration_verified": False,
        "telegram_delivery_verified": False,
        "release_authorized": False,
    }


def emit_pending(record: dict, json_mode: bool) -> int:
    """Use a distinct pending exit code without exposing identifiers."""

    if json_mode:
        print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    else:
        print(f"PENDING natural_recovery incident={record['outage_id']}")
    return 2


def parse_args() -> argparse.Namespace:
    """Parse explicit read-only recovery audit options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    parser.add_argument("--ready-url", default="http://127.0.0.1:8080/ready")
    parser.add_argument("--grace-seconds", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Distinguish PASS, PENDING and FAIL without changing live state."""

    args = parse_args()
    try:
        if not 0 <= args.grace_seconds <= 120:
            raise ValueError("invalid recovery grace")
        now = time.time()
        state = read_state(Path(args.db), args.grace_seconds)
        pending = pending_record(state, now)
        if pending is not None:
            return emit_pending(pending, args.json)
        health = local_json(args.health_url, "/health")
        ready = local_json(args.ready_url, "/ready")
        check_recovery(state, health, ready, now, args.grace_seconds)
        result = report(state, health, now, args.grace_seconds)
    except (
        ValueError,
        TypeError,
        OSError,
        KeyError,
        IndexError,
        sqlite3.DatabaseError,
    ) as exc:
        print(f"FAIL natural_recovery {type(exc).__name__}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"PASS natural_recovery incident={result['outage_id']} "
            "telegram_channel_edit_visible=NOT_VERIFIED"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
