#!/usr/bin/env python3
"""Review operator-confirmed mains loss separately from Shelly availability."""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from manual_dtek_forecast import KYIV, local_stamp  # noqa: E402

LABELS = {
    "ru": (
        "Отключение электричества сообщил пользователь",
        "Потеря связи Shelly",
        "Точное время выключения неизвестно",
        "Ориентировочный график не определяет причину",
        "Текущее напряжение неизвестно: Shelly офлайн",
    ),
    "uk": (
        "Відключення електрики повідомив користувач",
        "Втрата зв'язку Shelly",
        "Точний час вимкнення невідомий",
        "Орієнтовний графік не визначає причину",
        "Поточна напруга невідома: Shelly офлайн",
    ),
    "en": (
        "Power loss reported by operator",
        "Shelly connection loss",
        "Exact power-off time unknown",
        "Approximate schedule does not establish cause",
        "Current voltage unknown: Shelly offline",
    ),
}


def parse_args() -> argparse.Namespace:
    """Parse inputs for a single read-only review."""

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--incident", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--language", choices=tuple(LABELS), default="ru")
    p.add_argument("--json", action="store_true")
    p.add_argument("--at", help="Timezone-aware ISO-8601 observation time")
    return p.parse_args()


def offset_stamp(raw: str) -> float:
    """Only accept timezone-aware, finite event timestamp strings."""

    item = datetime.fromisoformat(raw)
    if item.tzinfo is None:
        raise ValueError("event timestamp must include timezone")
    stamp = item.timestamp()
    if not math.isfinite(stamp):
        raise ValueError("nonfinite event timestamp")
    return stamp


def read_incident(path: Path) -> dict:
    """Reject oversize, forged provenance or invented power-off times."""

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16384:
        raise ValueError("incident snapshot is missing or oversized")
    item = json.loads(path.read_text())
    if not isinstance(item, dict):
        raise ValueError("incident snapshot must be a JSON object")
    evidence = item.get("independent_evidence")
    observed = item.get("observed_evidence")
    forecast = item.get("forecast")
    if not all(isinstance(x, dict) for x in (evidence, observed, forecast)):
        raise ValueError("incident needs three separate evidence sections")
    if evidence.get("actual_power_off") != "user_confirmed":
        raise ValueError("independent operator report is not confirmed")
    if evidence.get("exact_power_off_timestamp") is not None:
        raise ValueError("exact mains switching time must remain unknown")
    if forecast.get("source") != "user_supplied_unverified":
        raise ValueError("manual forecast source must remain unverified")
    if forecast.get("provider_crosschecked") is not False:
        raise ValueError("manual forecast was not provider cross-checked")
    return item


def query_incident(db_path: Path, incident_id: int) -> tuple:
    """Find one real connectivity event without writing SQLite."""

    if db_path.is_symlink() or not db_path.is_file():
        raise ValueError("SQLite file does not exist or is a symlink")
    uri = "file:" + quote(str(db_path.resolve())) + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=4)) as db:
        row = db.execute(
            "select started_at, ended_at, phase from outages where id=?",
            (incident_id,),
        ).fetchone()
        if row is None:
            raise ValueError("incident is absent from SQLite")
        last = db.execute(
            "select ts,voltage from measurements "
            "where ts <= ? and voltage > 0 and voltage < 500 "
            "and frequency >= 40 and frequency <= 70 "
            "order by ts desc limit 1",
            (row[0],),
        ).fetchone()
    return row, last


def scheduled_at(forecast: dict, started_at: float) -> bool:
    """Compare timing only, never imply DTEK caused the loss."""

    day = date.fromisoformat(forecast["date"])
    if datetime.fromtimestamp(started_at, KYIV).date() != day:
        return False
    windows = forecast["approximate_window"]
    if not isinstance(windows, list) or len(windows) != 2:
        raise ValueError("forecast window must have two endpoints")
    start, end = (local_stamp(day, str(x)) for x in windows)
    if end <= start:
        raise ValueError("invalid forecast window")
    return start <= started_at < end


def checked_timeline(
    record: dict, event: tuple, last: tuple | None
) -> tuple[int, float]:
    """Validate network timing and the independent operator report."""

    match = re.fullmatch(r"natural incident #(\d+)", record["incident"])
    if not match or int(match[1]) < 1:
        raise ValueError("invalid incident identifier")
    connection = event[0]
    expected = offset_stamp(
        record["observed_evidence"]["shelly_connection_loss_started_kyiv"]
    )
    confirmation = offset_stamp(
        record["independent_evidence"]["user_confirmation_received_by_kyiv"]
    )
    if abs(connection - expected) > 1.0:
        raise ValueError("incident report disagrees with recorded timeline")
    if last is None or last[0] > connection:
        raise ValueError("real pre-incident voltage sample not found")
    return int(match[1]), confirmation


def connection_gap_timing(
    event: tuple, last: tuple, observed_at: float
) -> dict:
    """Report a network gap without guessing physical outage duration."""

    connection, finish, _phase = event
    end = observed_at if finish is None else finish
    if not all(math.isfinite(x) for x in (connection, end, observed_at)):
        raise ValueError("nonfinite incident observation time")
    if observed_at < connection or end < connection:
        raise ValueError("observation predates connectivity event")
    if finish is not None and observed_at < finish:
        raise ValueError("observation predates recorded recovery")
    return {
        "connection_gap_state": "open" if finish is None else "closed",
        "connection_gap_elapsed_seconds": round(end - connection, 1),
        "last_sample_age_at_loss_seconds": round(connection - last[0], 1),
        "current_voltage_v": None,
        "current_voltage_status": (
            "unknown_while_connection_lost"
            if finish is None
            else "not_verified_by_review"
        ),
        "actual_power_off_duration_seconds": None,
    }


def review(
    record: dict,
    event: tuple,
    last: tuple | None,
    observed_at: float | None = None,
) -> dict:
    """Correlate independent evidence without inferring an exact outage."""

    incident_id, confirmation = checked_timeline(record, event, last)
    connection, finish, phase = event
    clock = time.time() if observed_at is None else observed_at
    return {
        "status": "pass",
        "incident_id": incident_id,
        "phase": phase,
        "connection_lost_kyiv": datetime.fromtimestamp(
            connection, KYIV
        ).isoformat(),
        "connection_restored_kyiv": datetime.fromtimestamp(
            finish, KYIV
        ).isoformat()
        if finish is not None
        else None,
        "last_real_voltage_before_loss_v": round(last[1], 1),
        "power_off_reported_by_operator": True,
        "operator_confirmation_kyiv": datetime.fromtimestamp(
            confirmation, KYIV
        ).isoformat(),
        "exact_power_off_time": None,
        "schedule_is_approximate": True,
        "gap_began_in_planned_window": scheduled_at(
            record["forecast"], connection
        ),
        "cause_verified_by_schedule": False,
        "current_voltage_assumed_zero": False,
        "read_only": True,
        **connection_gap_timing(event, last, clock),
    }


def operator_text(record: dict, language: str) -> str:
    """Explain proven and unproven facts without false outage times."""

    confirmed, loss, unknown, caution, offline = LABELS[language]
    lines = [
        f"PASS: #{record['incident_id']}",
        f"✅ {confirmed}",
        f"📡 {loss}: {record['connection_lost_kyiv']}",
        f"⚠️ {unknown}",
        f"📅 {caution}",
    ]
    if record["connection_gap_state"] == "open":
        lines.append(f"⚡ {offline}")
    return "\n".join(lines)


def main() -> int:
    """Execute a bounded one-shot private incident review."""

    args = parse_args()
    try:
        record = read_incident(args.incident)
        match = re.fullmatch(r"natural incident #(\d+)", record["incident"])
        if match is None:
            raise ValueError("incident identifier is missing")
        row, last = query_incident(args.db, int(match[1]))
        clock = offset_stamp(args.at) if args.at else time.time()
        result = review(record, row, last, observed_at=clock)
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        OverflowError,
    ) as error:
        result = {"status": "fail", "reason": type(error).__name__}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(
            operator_text(result, args.language)
            if result["status"] == "pass"
            else f"FAIL: {result['reason']}"
        )
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
