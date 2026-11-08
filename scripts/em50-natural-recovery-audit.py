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
        integrity = db.execute("PRAGMA quick_check").fetchone()[0]
        outage = db.execute(
            "SELECT id,started_at,ended_at,phase FROM outages "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if outage is None:
            raise ValueError("no recorded Shelly connectivity incident")
        ident, start, ended, phase = outage
        counts = sample_counts(db, phase, start, ended, grace)
        latest_phase = db.execute(
            "SELECT ts,voltage,frequency FROM measurements "
            "WHERE phase=? ORDER BY ts DESC LIMIT 1",
            (phase,),
        ).fetchone()
        active = db.execute(
            "SELECT COUNT(*) FROM outages WHERE ended_at IS NULL"
        ).fetchone()[0]
        status_saved = db.execute(
            "SELECT EXISTS(SELECT 1 FROM meta "
            "WHERE key='telegram_status_message_id' AND value!='')"
        ).fetchone()[0]
    return {
        "integrity": integrity,
        "outage_id": ident,
        "start": start,
        "end": ended,
        "phase": phase,
        "latest_phase_sample": latest_phase,
        **counts,
        "active": active,
        "status_saved": bool(status_saved),
    }


def require_latest_phase_sample(state: dict, now: float) -> None:
    """Reject a stale, future or nonphysical latest same-phase reading."""

    latest = state.get("latest_phase_sample")
    try:
        stamp, voltage, frequency = (float(value) for value in latest)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("latest same-phase sample unavailable") from exc
    if (
        not all(math.isfinite(v) for v in (stamp, voltage, frequency))
        or not 0 < voltage < 500
        or not 40 <= frequency <= 70
        or not float(state["end"]) - 15 <= stamp <= now
        or now - stamp > 45
    ):
        raise ValueError("latest same-phase sample invalid or stale")


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
    require_latest_phase_sample(state, now)


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


def main() -> int:
    """Show PASS or fail safely without altering live state."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="/var/lib/shelly-voltage/voltage.sqlite3"
    )
    parser.add_argument("--health-url", default="http://127.0.0.1:8080/health")
    parser.add_argument("--ready-url", default="http://127.0.0.1:8080/ready")
    parser.add_argument("--grace-seconds", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if not 0 <= args.grace_seconds <= 120:
            raise ValueError("invalid recovery grace")
        now = time.time()
        state = read_state(Path(args.db), args.grace_seconds)
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
