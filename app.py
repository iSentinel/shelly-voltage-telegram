#!/usr/bin/env python3
"""Shelly Pro EM-50 voltage monitor with optional Telegram integration."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from loguru import logger
from requests.auth import HTTPDigestAuth

from advanced_features import (
    backup_health,
    chart_png,
    dashboard_html,
    export_csv,
    format_stamp,
    outage_history,
    phase_stats,
    quality_stats,
)


def read_version() -> str:
    """Read the packaged software version from the adjacent VERSION file."""

    path = Path(__file__).with_name("VERSION")
    try:
        version = path.read_text().strip()
    except OSError as exc:
        raise RuntimeError(f"cannot read VERSION file: {path}") from exc
    if not version:
        raise RuntimeError(f"VERSION file is empty: {path}")
    return version


VERSION = read_version()
SCHEMA_VERSION = 3
STOP = threading.Event()


METER_MODELS = {
    "pro_em50": "Shelly Pro EM-50",
    "em_gen3": "Shelly EM Gen3",
    "pm_mini_gen3": "Shelly PM Mini Gen3",
    "plus_pm_mini": "Shelly Plus PM Mini",
    "plus_1pm": "Shelly Plus 1PM",
    "1pm_gen3": "Shelly 1PM Gen3",
    "1pm_gen4": "Shelly 1PM Gen4",
    "1pm_mini_gen3": "Shelly 1PM Mini Gen3",
    "1pm_mini_gen4": "Shelly 1PM Mini Gen4",
    "pro3em": "Shelly Pro 3EM / Pro 3EM-3CT63",
}

METER_RPC = {
    "pro_em50": "EM1.GetStatus",
    "em_gen3": "EM1.GetStatus",
    "pm_mini_gen3": "PM1.GetStatus",
    "plus_pm_mini": "PM1.GetStatus",
    "plus_1pm": "Switch.GetStatus",
    "1pm_gen3": "Switch.GetStatus",
    "1pm_gen4": "Switch.GetStatus",
    "1pm_mini_gen3": "Switch.GetStatus",
    "1pm_mini_gen4": "Switch.GetStatus",
}


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration."""

    shelly_host: str
    shelly_meter_type: str
    em1_id: int
    shelly_user: str
    shelly_password: str
    shelly_timeout: float
    phase_l2_host: str
    phase_l2_em1_id: int
    phase_l2_user: str
    phase_l2_password: str
    phase_l3_host: str
    phase_l3_em1_id: int
    phase_l3_user: str
    phase_l3_password: str
    poll_interval: float
    telegram_token: str
    telegram_chat_id: str
    telegram_status_chat_id: str
    telegram_interval: float
    telegram_min_edit_interval: float
    telegram_allowed_user_ids: tuple[str, ...]
    telegram_admin_user_ids: tuple[str, ...]
    persist_update_offset: bool
    telegram_silent_default: bool
    telegram_quiet_start_hour: int
    telegram_quiet_end_hour: int
    low_voltage_alert_enabled: bool
    low_voltage_alert_v: float
    low_voltage_recovery_v: float
    significant_change: float
    retention_days: int
    database_path: Path
    timezone: str
    health_host: str
    health_port: int
    stale_after: float
    offline_after_failures: int
    recovery_after_successes: int
    offline_log_interval: float
    availability_alerts_enabled: bool
    alerts_enabled: bool
    warning_low: float
    warning_high: float
    critical_low: float
    critical_high: float
    alert_dwell_sec: float
    alert_recovery_dwell_sec: float
    daily_summary_enabled: bool
    daily_summary_hour: int
    daily_summary_minute: int
    backup_dir: Path
    telegram_pin_status: bool


def env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean environment variable."""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_user_ids(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated Telegram user allow-list."""

    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    if any(not item.lstrip("-").isdigit() for item in values):
        raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain numeric IDs")
    return values


def secret_setting(env_name: str, file_env_name: str) -> str:
    """Read a secret from an env value or an optional secret file."""

    direct = os.getenv(env_name, "").strip()
    if direct:
        return direct
    secret_file = os.getenv(file_env_name, "").strip()
    if not secret_file:
        return ""
    try:
        return Path(secret_file).read_text().strip()
    except FileNotFoundError:
        return ""


def telegram_policy_settings() -> dict[str, object]:
    """Load Telegram notification-policy settings."""

    return {
        "persist_update_offset": env_bool(
            "TELEGRAM_PERSIST_UPDATE_OFFSET", True
        ),
        "telegram_silent_default": env_bool(
            "TELEGRAM_SILENT_BY_DEFAULT", True
        ),
        "telegram_quiet_start_hour": int(
            os.getenv("TELEGRAM_QUIET_START_HOUR", "23")
        ),
        "telegram_quiet_end_hour": int(
            os.getenv("TELEGRAM_QUIET_END_HOUR", "7")
        ),
        "low_voltage_alert_enabled": env_bool(
            "LOW_VOLTAGE_ALERT_ENABLED", True
        ),
        "low_voltage_alert_v": float(os.getenv("LOW_VOLTAGE_ALERT_V", "180")),
        "low_voltage_recovery_v": float(
            os.getenv("LOW_VOLTAGE_RECOVERY_V", "185")
        ),
    }


def telegram_settings() -> dict[str, object]:
    """Load Telegram-specific configuration values."""

    settings = {
        "telegram_token": secret_setting(
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_FILE"
        ),
        "telegram_chat_id": secret_setting(
            "TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID_FILE"
        ),
        "telegram_status_chat_id": os.getenv(
            "TELEGRAM_STATUS_CHAT_ID", ""
        ).strip(),
        "telegram_interval": float(
            os.getenv("TELEGRAM_UPDATE_INTERVAL_SEC", "60")
        ),
        "telegram_min_edit_interval": float(
            os.getenv("TELEGRAM_MIN_EDIT_INTERVAL_SEC", "15")
        ),
        "telegram_allowed_user_ids": parse_user_ids(
            os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        ),
        "telegram_admin_user_ids": parse_user_ids(
            os.getenv("TELEGRAM_ADMIN_USER_IDS", "")
        ),
    }
    settings.update(telegram_policy_settings())
    return settings


def shelly_settings() -> dict[str, object]:
    """Load phase sensor connection settings."""

    return {
        "shelly_host": os.getenv("SHELLY_HOST", "").strip(),
        "shelly_meter_type": os.getenv("SHELLY_METER_TYPE", "pro_em50")
        .strip()
        .lower(),
        "em1_id": int(os.getenv("SHELLY_EM1_ID", "0")),
        "shelly_user": os.getenv("SHELLY_USERNAME", "admin"),
        "shelly_password": secret_setting(
            "SHELLY_PASSWORD", "SHELLY_PASSWORD_FILE"
        ),
        "shelly_timeout": float(os.getenv("SHELLY_TIMEOUT_SEC", "4")),
        "phase_l2_host": os.getenv("PHASE_L2_HOST", "").strip(),
        "phase_l2_em1_id": int(os.getenv("PHASE_L2_EM1_ID", "0")),
        "phase_l2_user": os.getenv("PHASE_L2_USERNAME", "admin"),
        "phase_l2_password": secret_setting(
            "PHASE_L2_PASSWORD", "PHASE_L2_PASSWORD_FILE"
        ),
        "phase_l3_host": os.getenv("PHASE_L3_HOST", "").strip(),
        "phase_l3_em1_id": int(os.getenv("PHASE_L3_EM1_ID", "0")),
        "phase_l3_user": os.getenv("PHASE_L3_USERNAME", "admin"),
        "phase_l3_password": secret_setting(
            "PHASE_L3_PASSWORD", "PHASE_L3_PASSWORD_FILE"
        ),
    }


def monitoring_core_settings(poll: float) -> dict[str, object]:
    """Load storage, polling, health, and connectivity settings."""

    return {
        "poll_interval": poll,
        "significant_change": float(os.getenv("SIGNIFICANT_CHANGE_V", "0.5")),
        "retention_days": int(os.getenv("RETENTION_DAYS", "90")),
        "database_path": Path(
            os.getenv("DATABASE_PATH", "/data/voltage.sqlite3")
        ),
        "timezone": os.getenv("TIMEZONE", "Europe/Kyiv"),
        "health_host": os.getenv("HEALTH_HOST", "0.0.0.0"),
        "health_port": int(os.getenv("HEALTH_PORT", "8080")),
        "stale_after": float(
            os.getenv("STALE_AFTER_SEC", str(max(45, poll * 3)))
        ),
        "offline_after_failures": int(
            os.getenv("SHELLY_OFFLINE_AFTER_FAILURES", "3")
        ),
        "recovery_after_successes": int(
            os.getenv("SHELLY_RECOVERY_AFTER_SUCCESSES", "2")
        ),
        "offline_log_interval": float(
            os.getenv("SHELLY_OFFLINE_LOG_INTERVAL_SEC", "300")
        ),
        "backup_dir": Path(
            os.getenv("BACKUP_DIR", "/var/backups/shelly-voltage")
        ),
    }


def monitoring_alert_settings() -> dict[str, object]:
    """Load voltage alert, summary, and status pin settings."""

    return {
        "availability_alerts_enabled": env_bool("AVAILABILITY_ALERTS_ENABLED"),
        "alerts_enabled": env_bool("ALERTS_ENABLED"),
        "warning_low": float(os.getenv("WARNING_LOW_V", "207")),
        "warning_high": float(os.getenv("WARNING_HIGH_V", "253")),
        "critical_low": float(os.getenv("CRITICAL_LOW_V", "190")),
        "critical_high": float(os.getenv("CRITICAL_HIGH_V", "255")),
        "alert_dwell_sec": float(os.getenv("ALERT_DWELL_SEC", "15")),
        "alert_recovery_dwell_sec": float(
            os.getenv("ALERT_RECOVERY_DWELL_SEC", "30")
        ),
        "daily_summary_enabled": env_bool("DAILY_SUMMARY_ENABLED", True),
        "daily_summary_hour": int(os.getenv("DAILY_SUMMARY_HOUR", "22")),
        "daily_summary_minute": int(os.getenv("DAILY_SUMMARY_MINUTE", "55")),
        "telegram_pin_status": env_bool("TELEGRAM_PIN_STATUS", True),
    }


def monitoring_settings(poll: float) -> dict[str, object]:
    """Load all local monitoring settings."""

    settings = monitoring_core_settings(poll)
    settings.update(monitoring_alert_settings())
    return settings


def load_config() -> Config:
    """Load and validate configuration from the environment."""

    poll = float(os.getenv("POLL_INTERVAL_SEC", "10"))
    values = shelly_settings()
    values.update(telegram_settings())
    values.update(monitoring_settings(poll))
    cfg = Config(**values)
    validate_config(cfg)
    return cfg


def validate_shelly_config(cfg: Config) -> None:
    """Validate Shelly and phase connection settings."""

    if not cfg.shelly_host:
        raise ValueError("SHELLY_HOST is required")
    if cfg.shelly_meter_type not in METER_MODELS:
        choices = ", ".join(sorted(METER_MODELS))
        raise ValueError(f"SHELLY_METER_TYPE must be one of: {choices}")
    if cfg.em1_id not in {0, 1}:
        raise ValueError("SHELLY_EM1_ID must be 0 or 1")
    if cfg.phase_l2_em1_id not in {0, 1} or cfg.phase_l3_em1_id not in {0, 1}:
        raise ValueError("PHASE_L2/L3_EM1_ID must be 0 or 1")
    if cfg.shelly_timeout <= 0:
        raise ValueError("SHELLY_TIMEOUT_SEC must be > 0")
    if cfg.poll_interval < 1:
        raise ValueError("POLL_INTERVAL_SEC must be >= 1")


def validate_telegram_config(cfg: Config) -> None:
    """Validate Telegram timing and notification settings."""

    if cfg.telegram_interval < 5:
        raise ValueError("TELEGRAM_UPDATE_INTERVAL_SEC must be >= 5")
    if cfg.telegram_min_edit_interval < 1:
        raise ValueError("TELEGRAM_MIN_EDIT_INTERVAL_SEC must be >= 1")
    hours = (cfg.telegram_quiet_start_hour, cfg.telegram_quiet_end_hour)
    if any(hour < 0 or hour > 23 for hour in hours):
        raise ValueError("Telegram quiet hours must be between 0 and 23")
    if cfg.low_voltage_recovery_v <= cfg.low_voltage_alert_v:
        raise ValueError(
            "LOW_VOLTAGE_RECOVERY_V must exceed LOW_VOLTAGE_ALERT_V"
        )


def validate_monitoring_config(cfg: Config) -> None:
    """Validate storage, health and voltage alert settings."""

    if cfg.stale_after < cfg.poll_interval:
        raise ValueError("STALE_AFTER_SEC must be >= POLL_INTERVAL_SEC")
    if cfg.offline_after_failures < 1 or cfg.recovery_after_successes < 1:
        raise ValueError("connectivity hysteresis values must be >= 1")
    if cfg.offline_log_interval < cfg.poll_interval:
        raise ValueError(
            "SHELLY_OFFLINE_LOG_INTERVAL_SEC must be >= POLL_INTERVAL_SEC"
        )
    if cfg.retention_days < 1:
        raise ValueError("RETENTION_DAYS must be >= 1")
    if cfg.alert_dwell_sec < 0 or cfg.alert_recovery_dwell_sec < 0:
        raise ValueError("alert dwell values must be >= 0")
    if not 0 <= cfg.daily_summary_hour <= 23:
        raise ValueError("DAILY_SUMMARY_HOUR must be 0..23")
    if not 0 <= cfg.daily_summary_minute <= 59:
        raise ValueError("DAILY_SUMMARY_MINUTE must be 0..59")
    ZoneInfo(cfg.timezone)
    thresholds = [
        cfg.critical_low,
        cfg.warning_low,
        cfg.warning_high,
        cfg.critical_high,
    ]
    if thresholds != sorted(thresholds):
        raise ValueError("Voltage thresholds are not ordered")


def validate_config(cfg: Config) -> None:
    """Validate configuration without network calls."""

    validate_shelly_config(cfg)
    validate_telegram_config(cfg)
    validate_monitoring_config(cfg)


SCHEMA_SQL = """
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    voltage REAL NOT NULL,
                    frequency REAL NOT NULL,
                    phase TEXT NOT NULL DEFAULT 'L1'
                );
                CREATE INDEX IF NOT EXISTS idx_measurements_ts
                ON measurements(ts);
                CREATE INDEX IF NOT EXISTS idx_measurements_phase_ts
                ON measurements(phase, ts);
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    phase TEXT NOT NULL DEFAULT 'L1'
                );
                CREATE INDEX IF NOT EXISTS idx_outages_started
                ON outages(started_at);
                CREATE INDEX IF NOT EXISTS idx_outages_phase_started
                ON outages(phase, started_at);
                CREATE TABLE IF NOT EXISTS settings_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    user_id TEXT NOT NULL,
                    setting TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_settings_audit_ts
                ON settings_audit(ts);
                CREATE TABLE IF NOT EXISTS hourly_aggregates (
                    phase TEXT NOT NULL,
                    hour_ts REAL NOT NULL,
                    samples INTEGER NOT NULL,
                    min_v REAL NOT NULL,
                    max_v REAL NOT NULL,
                    avg_v REAL NOT NULL,
                    PRIMARY KEY(phase, hour_ts)
                );
"""


class Storage:
    """SQLite storage for measurements and metadata."""

    def __init__(self, path: Path, timezone: str) -> None:
        """Initialize the database path and local timezone."""
        self.path = path
        self.zone = ZoneInfo(timezone)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Open SQLite with WAL and a busy timeout."""

        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def existing_schema_version(self, db: sqlite3.Connection) -> int:
        """Return an existing schema version without mutating the database."""

        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()
        if not table:
            return 0
        row = db.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
        return int(row[0]) if row else 0

    def migrate_phase_schema(
        self, db: sqlite3.Connection, current: int
    ) -> None:
        """Add phase columns to legacy v1/v2 tables before creating indexes."""

        if current not in {1, 2}:
            return
        measurement_cols = {
            row[1] for row in db.execute("PRAGMA table_info(measurements)")
        }
        if "phase" not in measurement_cols:
            db.execute(
                "ALTER TABLE measurements ADD COLUMN phase TEXT NOT NULL "
                "DEFAULT 'L1'"
            )
        has_outages = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='outages'"
        ).fetchone()
        if not has_outages:
            return
        outage_cols = {
            row[1] for row in db.execute("PRAGMA table_info(outages)")
        }
        if "phase" not in outage_cols:
            db.execute(
                "ALTER TABLE outages ADD COLUMN phase TEXT NOT NULL "
                "DEFAULT 'L1'"
            )

    def initialize(self) -> None:
        """Create or migrate the schema without downgrading future versions."""

        with closing(self.connect()) as db, db:
            current = self.existing_schema_version(db)
            if current > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than {SCHEMA_VERSION}"
                )
            self.migrate_phase_schema(db, current)
            db.executescript(SCHEMA_SQL)
            self.set_meta("schema_version", str(SCHEMA_VERSION), db)

    def add(
        self, ts: float, voltage: float, frequency: float, phase: str = "L1"
    ) -> None:
        """Insert one valid measurement."""

        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO measurements(ts, voltage, frequency, phase) "
                "VALUES(?,?,?,?)",
                (ts, voltage, frequency, phase),
            )

    def set_meta(
        self,
        key: str,
        value: str,
        db: sqlite3.Connection | None = None,
    ) -> None:
        """Store one metadata value."""

        if db is not None:
            db.execute(
                """
                INSERT INTO meta(key,value) VALUES(?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
            return
        with closing(self.connect()) as conn, conn:
            self.set_meta(key, value, conn)

    def get_meta(self, key: str) -> str | None:
        """Read one metadata value."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
        return str(row[0]) if row else None

    def delete_meta(self, key: str) -> None:
        """Delete one metadata key."""

        with closing(self.connect()) as db, db:
            db.execute("DELETE FROM meta WHERE key=?", (key,))

    def audit_setting(
        self, user_id: str, setting: str, old_value: str | None, new_value: str
    ) -> None:
        """Append one admin settings change to the audit journal."""

        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT INTO settings_audit("
                "ts,user_id,setting,old_value,new_value) "
                "VALUES(?,?,?,?,?)",
                (time.time(), user_id, setting, old_value, new_value),
            )

    def audit_recent(self, limit: int = 10) -> list[tuple]:
        """Return newest settings audit records."""

        with closing(self.connect()) as db, db:
            return db.execute(
                "SELECT ts,user_id,setting,old_value,new_value "
                "FROM settings_audit "
                "ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def rollup_hourly(self, before: float | None = None) -> int:
        """Upsert hourly aggregates for retained raw measurements."""

        cutoff = before or time.time()
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT phase,CAST(ts/3600 AS INT)*3600,COUNT(*),MIN(voltage),"
                "MAX(voltage),AVG(voltage) FROM measurements WHERE ts<? "
                "GROUP BY phase,CAST(ts/3600 AS INT)",
                (cutoff,),
            ).fetchall()
            db.executemany(
                "INSERT OR REPLACE INTO hourly_aggregates "
                "(phase,hour_ts,samples,min_v,max_v,avg_v) "
                "VALUES(?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def database_maintenance(self) -> dict[str, object]:
        """Checkpoint WAL, optimize SQLite and vacuum only when worthwhile."""

        with closing(self.connect()) as db:
            db.execute("PRAGMA wal_checkpoint(PASSIVE)")
            db.execute("PRAGMA optimize")
            pages = int(db.execute("PRAGMA page_count").fetchone()[0])
            free = int(db.execute("PRAGMA freelist_count").fetchone()[0])
        ratio = free / pages if pages else 0.0
        vacuumed = False
        if ratio >= 0.25:
            with closing(self.connect()) as db:
                db.execute("VACUUM")
            vacuumed = True
        return {"pages": pages, "free_pages": free, "vacuumed": vacuumed}

    def day_stats(
        self, now: float, phase: str = "L1"
    ) -> dict[str, float | int | None]:
        """Return current-day voltage statistics."""

        current = datetime.fromtimestamp(now, self.zone)
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        with closing(self.connect()) as db, db:
            row = db.execute(
                """
                SELECT COUNT(*), MIN(voltage), MAX(voltage), AVG(voltage)
                FROM measurements WHERE ts >= ? AND ts < ? AND phase=?
                """,
                (start.timestamp(), end.timestamp(), phase),
            ).fetchone()
        return {
            "count": int(row[0]),
            "min": row[1],
            "max": row[2],
            "avg": row[3],
        }

    def recent(
        self, limit: int = 12, phase: str = "L1"
    ) -> list[tuple[float, float, float]]:
        """Return recent measurements newest first."""

        with closing(self.connect()) as db, db:
            rows = db.execute(
                """
                SELECT ts, voltage, frequency FROM measurements
                WHERE phase=? ORDER BY ts DESC LIMIT ?
                """,
                (phase, limit),
            ).fetchall()
        return [(float(a), float(b), float(c)) for a, b, c in rows]

    def latest(self, phase: str = "L1") -> tuple[float, float, float] | None:
        """Return the newest valid measurement, if any."""

        rows = self.recent(1, phase)
        return rows[0] if rows else None

    def begin_outage(self, started_at: float, phase: str = "L1") -> bool:
        """Open one outage and return whether a new row was created."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT id FROM outages WHERE ended_at IS NULL AND phase=? "
                "ORDER BY started_at DESC LIMIT 1",
                (phase,),
            ).fetchone()
            if row:
                return False
            db.execute(
                "INSERT INTO outages(started_at, phase) VALUES(?,?)",
                (started_at, phase),
            )
            return True

    def open_outage_started(self, phase: str = "L1") -> float | None:
        """Return the start of the currently open outage."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT started_at FROM outages "
                "WHERE ended_at IS NULL AND phase=? "
                "ORDER BY started_at DESC LIMIT 1",
                (phase,),
            ).fetchone()
        return float(row[0]) if row else None

    def end_outage(self, ended_at: float, phase: str = "L1") -> float | None:
        """Close the current outage and return its duration."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT id, started_at FROM outages "
                "WHERE ended_at IS NULL AND phase=? "
                "ORDER BY started_at DESC LIMIT 1",
                (phase,),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE outages SET ended_at=? WHERE id=?",
                (ended_at, int(row[0])),
            )
        return max(0.0, ended_at - float(row[1]))

    def outage_total(self, phase: str = "L1") -> int:
        """Return the number of recorded outage events."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT COUNT(*) FROM outages WHERE phase=?", (phase,)
            ).fetchone()
        return int(row[0])

    def latest_closed_outage_duration(self, phase: str = "L1") -> float | None:
        """Return the duration of the newest closed outage, if any."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT started_at, ended_at FROM outages "
                "WHERE ended_at IS NOT NULL AND phase=? "
                "ORDER BY ended_at DESC LIMIT 1",
                (phase,),
            ).fetchone()
        if row is None:
            return None
        return max(0.0, float(row[1]) - float(row[0]))

    def outage_stats(
        self, now: float, phase: str = "L1"
    ) -> dict[str, float | int]:
        """Return outage count and overlap duration for the current day."""

        current = datetime.fromtimestamp(now, self.zone)
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        with closing(self.connect()) as db, db:
            rows = db.execute(
                "SELECT started_at, ended_at FROM outages "
                "WHERE started_at < ? AND COALESCE(ended_at, ?) >= ? "
                "AND phase=?",
                (end.timestamp(), now, start.timestamp(), phase),
            ).fetchall()
        count = len(rows)
        duration = sum(
            max(
                0.0,
                min(float(row[1] or now), end.timestamp())
                - max(float(row[0]), start.timestamp()),
            )
            for row in rows
        )
        return {"count": count, "duration": duration}

    def prune(self, retention_days: int) -> int:
        """Delete samples older than the configured retention period."""

        cutoff = time.time() - retention_days * 86400
        with closing(self.connect()) as db, db:
            cursor = db.execute(
                "DELETE FROM measurements WHERE ts < ?", (cutoff,)
            )
            return int(cursor.rowcount)

    def integrity(self) -> str:
        """Run SQLite quick_check without writing to the database."""

        return database_integrity(self.path)


def database_integrity(path: Path) -> str:
    """Run SQLite quick_check through a read-only connection."""

    uri = f"{path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
        row = db.execute("PRAGMA quick_check").fetchone()
    return str(row[0])


def db_check(path: Path) -> int:
    """Check an existing database without creating or migrating it."""

    try:
        result = database_integrity(path)
    except sqlite3.Error as exc:
        print(
            f"ERROR: database check failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2
    print(result)
    return 0 if result == "ok" else 2


class RuntimeState:
    """Thread-safe current service and Shelly connectivity state."""

    def __init__(self) -> None:
        """Initialize in-memory connectivity and measurement state."""
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.last_success: float | None = None
        self.last_error: str | None = None
        self.voltage: float | None = None
        self.frequency: float | None = None
        self.rpc_ms: float | None = None
        self.connectivity = "starting"
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.offline_since: float | None = None
        self.outage_count = 0
        self.last_transition = self.started_at
        self.last_outage_duration: float | None = None

    def restore(self, ts: float, voltage: float, frequency: float) -> None:
        """Restore the last persisted sample without pretending it is fresh."""

        with self.lock:
            self.last_success = ts
            self.voltage = voltage
            self.frequency = frequency

    def restore_outage(self, started_at: float, total: int) -> None:
        """Restore an open outage across a process restart."""

        with self.lock:
            self.connectivity = "offline"
            self.offline_since = started_at
            self.outage_count = total
            self.last_transition = started_at

    def restore_outage_count(self, total: int) -> None:
        """Restore the persisted outage counter."""

        with self.lock:
            self.outage_count = total

    def restore_last_outage_duration(self, duration: float | None) -> None:
        """Restore the latest completed outage duration from SQLite."""

        with self.lock:
            self.last_outage_duration = duration

    def success(
        self,
        voltage: float,
        frequency: float,
        rpc_ms: float,
        recovery_after: int = 1,
    ) -> bool:
        """Record a valid read and return whether connectivity changed."""

        now = time.time()
        with self.lock:
            previous = self.connectivity
            self.last_success = now
            self.last_error = None
            self.voltage = voltage
            self.frequency = frequency
            self.rpc_ms = rpc_ms
            self.consecutive_failures = 0
            self.consecutive_successes += 1
            if previous in {"offline", "recovering"}:
                if self.consecutive_successes >= recovery_after:
                    self.connectivity = "online"
                    if self.offline_since is not None:
                        self.last_outage_duration = max(
                            0.0, now - self.offline_since
                        )
                    self.offline_since = None
                else:
                    self.connectivity = "recovering"
            else:
                self.connectivity = "online"
            if previous != self.connectivity:
                self.last_transition = now
            return previous != self.connectivity

    def error(self, message: str, offline_after: int = 1) -> bool:
        """Record a read failure without replacing last valid data."""

        now = time.time()
        with self.lock:
            previous = self.connectivity
            self.last_error = message
            self.consecutive_successes = 0
            self.consecutive_failures += 1
            should_offline = self.consecutive_failures >= offline_after
            if previous == "recovering":
                should_offline = True
            if should_offline and previous != "offline":
                self.connectivity = "offline"
                self.offline_since = self.offline_since or now
                self.outage_count += 1
            if previous != self.connectivity:
                self.last_transition = now
            return previous != self.connectivity

    def snapshot(self) -> dict[str, object]:
        """Return a consistent JSON-ready snapshot."""

        with self.lock:
            return {
                "version": VERSION,
                "started_at": self.started_at,
                "last_success": self.last_success,
                "last_error": self.last_error,
                "voltage": self.voltage,
                "frequency": self.frequency,
                "rpc_ms": self.rpc_ms,
                "connectivity": self.connectivity,
                "consecutive_failures": self.consecutive_failures,
                "consecutive_successes": self.consecutive_successes,
                "offline_since": self.offline_since,
                "outage_count": self.outage_count,
                "last_transition": self.last_transition,
                "last_outage_duration": self.last_outage_duration,
            }


class ShellyClient:
    """Local HTTP RPC client for Shelly Pro EM-50."""

    def __init__(
        self,
        cfg: Config,
        host: str | None = None,
        em1_id: int | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        """Create a read-only Shelly client for one device."""
        self.cfg = cfg
        self.host = host or cfg.shelly_host
        self.em1_id = cfg.em1_id if em1_id is None else em1_id
        self.user = cfg.shelly_user if user is None else user
        self.password = cfg.shelly_password if password is None else password
        self.session = requests.Session()

    def auth(self) -> HTTPDigestAuth | None:
        """Return Shelly Digest auth only when a password is configured."""

        if not self.password:
            return None
        return HTTPDigestAuth(self.user, self.password)

    def rpc(self, method: str, params: dict | None = None) -> dict:
        """Call one read-only Shelly RPC endpoint."""

        host = self.host.rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        response = self.session.get(
            f"{host}/rpc/{method}",
            params=params,
            auth=self.auth(),
            timeout=self.cfg.shelly_timeout,
        )
        response.raise_for_status()
        return response.json()

    def device_health(self) -> dict[str, object]:
        """Read device health without changing Shelly configuration."""

        info = self.rpc("Shelly.GetDeviceInfo")
        status = self.rpc("Sys.GetStatus")
        unix = status.get("unixtime")
        skew = None if unix is None else float(unix) - time.time()
        return {
            "model": info.get("model"),
            "firmware": info.get("ver"),
            "mac": info.get("mac"),
            "uptime": status.get("uptime"),
            "time_skew_sec": skew,
        }

    def read(self) -> tuple[float, float, float]:
        """Read voltage/frequency and return RPC latency in milliseconds."""

        host = self.host.rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        url = f"{host}/rpc/EM1.GetStatus"
        started = time.perf_counter()
        response = self.session.get(
            url,
            params={"id": self.em1_id},
            auth=self.auth(),
            timeout=self.cfg.shelly_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rpc_ms = (time.perf_counter() - started) * 1000
        return float(payload["voltage"]), float(payload["freq"]), rpc_ms


class RpcMeterClient(ShellyClient):
    """Read voltage/frequency through a selected read-only component RPC."""

    def __init__(self, cfg: Config, rpc_method: str) -> None:
        """Create a single-phase client for one read-only RPC method."""
        super().__init__(cfg)
        self.rpc_method = rpc_method

    def read(self) -> tuple[float, float, float]:
        """Read one single-phase status component."""

        started = time.perf_counter()
        payload = self.rpc(self.rpc_method, {"id": self.em1_id})
        rpc_ms = (time.perf_counter() - started) * 1000
        voltage = payload.get("voltage")
        frequency = payload.get("freq")
        if voltage is None or frequency is None:
            raise RuntimeError(
                f"{self.rpc_method} returned no voltage/frequency"
            )
        return float(voltage), float(frequency), rpc_ms


class Pro3EMClient(ShellyClient):
    """Read all three phases from Shelly Pro 3EM in triphase profile."""

    def read_phases(self) -> dict[str, tuple[float, float, float]]:
        """Read phase A/B/C using one EM.GetStatus request."""

        started = time.perf_counter()
        payload = self.rpc("EM.GetStatus", {"id": 0})
        rpc_ms = (time.perf_counter() - started) * 1000
        result = {}
        for phase, prefix in (("L1", "a"), ("L2", "b"), ("L3", "c")):
            voltage = payload.get(f"{prefix}_voltage")
            frequency = payload.get(f"{prefix}_freq")
            if voltage is None or frequency is None:
                continue
            result[phase] = (float(voltage), float(frequency), rpc_ms)
        return result


def telegram_quiet_hours(cfg: Config, now: datetime | None = None) -> bool:
    """Return whether Telegram notifications must be silent now."""

    current = now or datetime.now(ZoneInfo(cfg.timezone))
    hour = current.hour
    start = cfg.telegram_quiet_start_hour
    end = cfg.telegram_quiet_end_hour
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


class Telegram:
    """Small Telegram Bot API client with isolated long polling."""

    def __init__(self, cfg: Config) -> None:
        """Initialize Telegram API sessions and notification defaults."""
        self.cfg = cfg
        self.enabled = bool(cfg.telegram_token and cfg.telegram_chat_id)
        self.status_chat_id = (
            cfg.telegram_status_chat_id or cfg.telegram_chat_id
        )
        self.base = f"https://api.telegram.org/bot{cfg.telegram_token}"
        self.api_session = requests.Session()
        self.updates_session = requests.Session()
        self.api_lock = threading.Lock()
        self.silent_default = cfg.telegram_silent_default
        self.quiet_enabled = True
        self.quiet_start_hour = cfg.telegram_quiet_start_hour
        self.quiet_end_hour = cfg.telegram_quiet_end_hour

    def _post(self, method: str, payload: dict) -> dict:
        """Perform one HTTP request using the appropriate session."""

        session = (
            self.updates_session
            if method == "getUpdates"
            else self.api_session
        )
        try:
            response = session.post(
                f"{self.base}/{method}",
                json=payload,
                timeout=25 if method == "getUpdates" else 20,
            )
        except requests.RequestException as exc:
            error = exc.__class__.__name__
            raise RuntimeError(f"Telegram transport error: {error}") from None
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Telegram returned invalid JSON") from None

    def _call_with_retry(self, method: str, payload: dict) -> dict:
        """Retry one bounded Telegram rate-limit response."""

        data = self._post(method, payload)
        if data.get("error_code") == 429:
            retry = data.get("parameters", {}).get("retry_after", 1)
            delay = min(30.0, max(0.0, float(retry)))
            logger.warning("Telegram rate limited; retry in {:.1f}s", delay)
            time.sleep(delay)
            data = self._post(method, payload)
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram API error"))
        return data["result"]

    def call(self, method: str, payload: dict) -> dict:
        """Call Telegram without blocking status edits on long polling."""

        if method == "getUpdates":
            return self._call_with_retry(method, payload)
        with self.api_lock:
            return self._call_with_retry(method, payload)

    def quiet_now(self) -> bool:
        """Return whether the current runtime policy forces silence."""

        if not self.quiet_enabled:
            return False
        hour = datetime.now(ZoneInfo(self.cfg.timezone)).hour
        start, end = self.quiet_start_hour, self.quiet_end_hour
        if start == end:
            return True
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def send(
        self,
        text: str,
        notify: bool | None = None,
        reply_markup: dict | None = None,
        chat_id: str | None = None,
    ) -> int:
        """Send a message using the current notification policy."""

        should_notify = not self.silent_default if notify is None else notify
        payload = {
            "chat_id": chat_id or self.cfg.telegram_chat_id,
            "text": text,
            "disable_notification": not should_notify or self.quiet_now(),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self.call("sendMessage", payload)
        return int(result["message_id"])

    def edit(
        self,
        message_id: int,
        text: str,
        reply_markup: dict | None = None,
        chat_id: str | None = None,
    ) -> None:
        """Edit a Telegram message."""

        payload = {
            "chat_id": chat_id or self.cfg.telegram_chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self.call("editMessageText", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        """Acknowledge an inline-keyboard callback."""

        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self.call("answerCallbackQuery", payload)

    def send_file(
        self, method: str, field: str, path: Path, caption: str = ""
    ) -> int:
        """Upload one generated file to the private control chat."""

        data = {
            "chat_id": self.cfg.telegram_chat_id,
            "disable_notification": "true",
        }
        if caption:
            data["caption"] = caption
        try:
            with path.open("rb") as handle:
                response = self.api_session.post(
                    f"{self.base}/{method}",
                    data=data,
                    files={field: (path.name, handle)},
                    timeout=30,
                )
            payload = response.json()
        except (OSError, requests.RequestException, ValueError) as exc:
            raise RuntimeError(
                f"Telegram file upload failed: {type(exc).__name__}"
            ) from None
        if not payload.get("ok"):
            raise RuntimeError(
                payload.get("description", "Telegram API error")
            )
        return int(payload["result"]["message_id"])

    def send_photo(self, path: Path, caption: str = "") -> int:
        """Send one generated PNG chart."""

        return self.send_file("sendPhoto", "photo", path, caption)

    def send_document(self, path: Path, caption: str = "") -> int:
        """Send one generated export document."""

        return self.send_file("sendDocument", "document", path, caption)

    def delete(self, message_id: int, chat_id: str | None = None) -> None:
        """Delete one Telegram message."""

        self.call(
            "deleteMessage",
            {
                "chat_id": chat_id or self.cfg.telegram_chat_id,
                "message_id": message_id,
            },
        )

    def pin(self, message_id: int, chat_id: str | None = None) -> None:
        """Pin the stable channel status without generating a notification."""

        self.call(
            "pinChatMessage",
            {
                "chat_id": chat_id or self.status_chat_id,
                "message_id": message_id,
                "disable_notification": True,
            },
        )

    def get_me(self) -> dict:
        """Validate bot token without sending a message."""

        return self.call("getMe", {})

    def get_chat(self, chat_id: str | None = None) -> dict:
        """Validate one configured Telegram destination."""

        return self.call(
            "getChat", {"chat_id": chat_id or self.cfg.telegram_chat_id}
        )

    def set_commands(self) -> None:
        """Register the supported bot commands."""

        descriptions = {
            "start": "Показать команды",
            "help": "Показать команды",
            "voltage": "Текущее напряжение",
            "outages": "Отключения за сегодня",
            "status": "Полный статус",
            "today": "Статистика за сегодня",
            "minmax": "Минимум и максимум",
            "history": "Последние измерения",
            "health": "Состояние сервиса",
            "settings": "Настройки уведомлений",
            "chart": "График напряжения",
            "export": "Экспорт CSV",
            "quality": "Качество сети",
            "summary": "Сводка за сутки",
            "maintenance": "Режим обслуживания",
            "audit": "Журнал настроек",
            "device": "Состояние Shelly",
        }
        commands = [
            {"command": name, "description": description}
            for name, description in descriptions.items()
        ]
        self.call("setMyCommands", {"commands": commands})

    def updates(self, offset: int | None) -> list[dict]:
        """Long-poll bot updates."""

        payload: dict[str, object] = {
            "timeout": 20,
            "allowed_updates": ["message", "channel_post", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload)


def format_time(ts: float, cfg: Config) -> str:
    """Format local timestamp."""

    zone = ZoneInfo(cfg.timezone)
    return datetime.fromtimestamp(ts, zone).strftime("%d.%m.%Y %H:%M:%S")


def fmt(value: float | None) -> str:
    """Format an optional voltage value."""

    return "—" if value is None else f"{value:.1f}"


def format_duration(seconds: float | None) -> str:
    """Format an optional duration without excessive precision."""

    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}ч {minutes}м"
    if minutes:
        return f"{minutes}м {secs}с"
    return f"{secs}с"


def stale_status_lines(
    snap: dict[str, object], ts: float, cfg: Config
) -> list[str]:
    """Render last-good data for offline or recovering state."""

    lines = [
        "",
        "Последнее корректное измерение:",
        f"{float(snap['voltage']):.1f} V / {float(snap['frequency']):.2f} Hz",
    ]
    offline_since = snap.get("offline_since")
    if offline_since is not None:
        lines.append(
            "⏱ Недоступен: "
            f"{format_duration(time.time() - float(offline_since))}"
        )
    lines.extend(["", f"🕐 Последние данные: {format_time(ts, cfg)}"])
    return lines


def status_text(
    state: RuntimeState,
    storage: Storage,
    cfg: Config,
) -> str:
    """Render the stable Telegram status card without inventing zero volts."""

    snap = state.snapshot()
    connectivity = str(snap["connectivity"])
    labels = {
        "online": "🟢 Shelly: online",
        "recovering": "🟡 Shelly: восстанавливается",
        "offline": "🔴 Shelly: недоступен",
        "starting": "🟡 Shelly: запуск",
    }
    lines = ["⚡ Напряжение сети", "", labels.get(connectivity, connectivity)]
    if snap["voltage"] is None or snap["frequency"] is None:
        return "\n".join(lines + ["", "Нет корректных измерений."])
    ts = float(snap["last_success"] or time.time())
    if connectivity in {"offline", "recovering"}:
        return "\n".join(lines + stale_status_lines(snap, ts, cfg))
    stats = storage.day_stats(ts)
    lines.extend(
        [
            f"🔌 Сейчас: {float(snap['voltage']):.1f} V",
            f"〰️ Частота: {float(snap['frequency']):.2f} Hz",
            "",
            f"📉 Минимум сегодня: {fmt(stats['min'])} V",
            f"📈 Максимум сегодня: {fmt(stats['max'])} V",
            f"📊 Среднее сегодня: {fmt(stats['avg'])} V",
            f"🔢 Измерений сегодня: {stats['count']}",
            "",
            f"🕐 Обновлено: {format_time(ts, cfg)}",
        ]
    )
    return "\n".join(lines)


def phase_line(monitor: Monitor, phase: str) -> str:
    """Render one phase line for Telegram and dashboard views."""

    if not monitor.phase_configured(phase):
        return f"⚪ {phase}: не настроена"
    snap = monitor.phase_states[phase].snapshot()
    if snap["voltage"] is None:
        return f"🟡 {phase}: нет данных"
    icon = "🟢" if snap["connectivity"] == "online" else "🔴"
    suffix = "" if snap["connectivity"] == "online" else " (offline)"
    return f"{icon} {phase}: {float(snap['voltage']):.1f} V{suffix}"


def voltage_event_lines(monitor: Monitor) -> list[str]:
    """Render the latest low-voltage transition above the stable status."""

    event = monitor.last_voltage_event
    if event is None:
        return []
    kind, voltage, frequency, happened_at = event
    title = (
        "🚨 Низкое напряжение сети"
        if kind == "low"
        else "✅ Напряжение восстановилось"
    )
    zone = ZoneInfo(monitor.cfg.timezone)
    when = datetime.fromtimestamp(happened_at, zone).strftime("%H:%M")
    return [
        f"{title} · {when}",
        f"{voltage:.1f} V · {frequency:.2f} Hz",
        "",
    ]


def multiphase_status_text(monitor: Monitor) -> str:
    """Render three-phase-aware status while retaining detailed L1 stats."""

    lines = voltage_event_lines(monitor)
    lines.extend(["⚡ Напряжение сети · 3 фазы", ""])
    lines.extend(phase_line(monitor, p) for p in ("L1", "L2", "L3"))
    l1 = monitor.state.snapshot()
    if l1["voltage"] is None:
        return "\n".join(lines + ["", "L1: нет корректных измерений."])
    ts = float(l1["last_success"] or time.time())
    stats = monitor.storage.day_stats(ts, "L1")
    lines.extend(
        [
            "",
            f"L1 сегодня: {fmt(stats['min'])}–{fmt(stats['max'])} V, "
            f"avg {fmt(stats['avg'])} V",
        ]
    )
    if monitor.last_recovery_duration is not None:
        lines.append(
            "✅ Питание восстановлено · outage "
            f"{format_duration(monitor.last_recovery_duration)}"
        )
    lines.append(f"🕐 Последние данные L1: {format_time(ts, monitor.cfg)}")
    return "\n".join(lines)


def message_not_modified(exc: Exception) -> bool:
    """Return true when Telegram says the requested edit is identical."""

    return "message is not modified" in str(exc).lower()


def message_missing(exc: Exception) -> bool:
    """Return true for Telegram errors that require a new status message."""

    text = str(exc).lower()
    markers = (
        "message to edit not found",
        "message can't be edited",
        "message_id_invalid",
    )
    return any(marker in text for marker in markers)


def voltage_level(voltage: float, cfg: Config) -> str:
    """Classify a voltage sample for optional alerts."""

    if voltage <= cfg.critical_low or voltage >= cfg.critical_high:
        return "critical"
    if voltage <= cfg.warning_low or voltage >= cfg.warning_high:
        return "warning"
    return "normal"


class Monitor:
    """Coordinate polling, persistence, and Telegram."""

    def __init__(self, cfg: Config) -> None:
        """Create storage, meter clients, Telegram, and runtime state."""
        self.cfg = cfg
        self.storage = Storage(cfg.database_path, cfg.timezone)
        self.meter_type = cfg.shelly_meter_type
        self.state = RuntimeState()
        self.shelly = self.create_primary_client()
        self.phase_states = {"L1": self.state}
        self.phase_clients = {"L1": self.shelly}
        self.configure_phases()
        self.telegram = Telegram(cfg)
        self.initialize_runtime_policy()
        self.initialize_runtime_counters()

    def initialize_runtime_policy(self) -> None:
        """Initialize mutable alert and notification settings."""

        cfg = self.cfg
        self.low_voltage_alert_enabled = cfg.low_voltage_alert_enabled
        self.low_voltage_alert_v = cfg.low_voltage_alert_v
        self.low_voltage_recovery_v = cfg.low_voltage_recovery_v
        self.alerts_enabled = cfg.alerts_enabled
        self.warning_low, self.warning_high = cfg.warning_low, cfg.warning_high
        self.critical_low, self.critical_high = (
            cfg.critical_low,
            cfg.critical_high,
        )
        self.availability_alerts_enabled = cfg.availability_alerts_enabled
        self.daily_summary_enabled = cfg.daily_summary_enabled
        self.daily_summary_hour = cfg.daily_summary_hour
        self.daily_summary_minute = cfg.daily_summary_minute
        self.pin_status = cfg.telegram_pin_status
        self.alert_dwell = cfg.alert_dwell_sec
        self.recovery_dwell = cfg.alert_recovery_dwell_sec

    def initialize_runtime_counters(self) -> None:
        """Initialize transient counters and transition state."""

        self.last_sent_voltage: float | None = None
        self.last_sent_connectivity: str | None = None
        self.last_telegram = 0.0
        self.last_failure_log = 0.0
        self.alert_level = "normal"
        self.low_voltage_active = False
        self.low_voltage_candidate_since: float | None = None
        self.last_voltage_event: tuple[str, float, float, float] | None = None
        self.next_prune = 0.0
        self.last_summary_day: str | None = None
        self.last_recovery_duration: float | None = None
        self.maintenance_until = 0.0
        self.last_loop_tick = time.monotonic()
        self.telegram_failures = 0
        self.last_db_maintenance = 0.0
        self.alert_candidate: tuple[str, float] | None = None
        self.phase_alert_levels = {"L2": "normal", "L3": "normal"}
        self.phase_alert_candidates: dict[str, tuple[str, float]] = {}
        self.phase_low_active = {"L2": False, "L3": False}
        self.phase_low_candidates: dict[str, float] = {}

    def create_primary_client(self) -> ShellyClient:
        """Create a read-only client for the selected Shelly model."""

        if self.meter_type == "pro3em":
            return Pro3EMClient(self.cfg)
        rpc_method = METER_RPC.get(self.meter_type)
        if rpc_method is None:
            raise ValueError("unsupported Shelly meter type")
        if self.meter_type == "pro_em50":
            return ShellyClient(self.cfg)
        return RpcMeterClient(self.cfg, rpc_method)

    def reconfigure_meter_type(self, meter_type: str) -> None:
        """Rebuild read-only clients/states for a selected Shelly backend."""

        if meter_type not in METER_MODELS:
            raise ValueError("unsupported Shelly meter type")
        self.meter_type = meter_type
        self.state = RuntimeState()
        self.shelly = self.create_primary_client()
        self.phase_states = {"L1": self.state}
        self.phase_clients = {"L1": self.shelly}
        self.configure_phases()
        for phase in self.phase_states:
            self.restore_phase(phase)
        self.last_sent_voltage = None
        self.last_sent_connectivity = None
        self.last_telegram = 0.0

    def configure_phases(self) -> None:
        """Configure virtual Pro3EM phases or optional separate sensors."""

        if self.meter_type == "pro3em":
            for phase in ("L2", "L3"):
                self.phase_states[phase] = RuntimeState()
                self.phase_clients[phase] = self.shelly
            return
        self.configure_optional_phases()

    def configure_optional_phases(self) -> None:
        """Create runtime clients only for configured L2/L3 sensors."""

        specs = {
            "L2": (
                self.cfg.phase_l2_host,
                self.cfg.phase_l2_em1_id,
                self.cfg.phase_l2_user,
                self.cfg.phase_l2_password,
            ),
            "L3": (
                self.cfg.phase_l3_host,
                self.cfg.phase_l3_em1_id,
                self.cfg.phase_l3_user,
                self.cfg.phase_l3_password,
            ),
        }
        for phase, (host, em1_id, user, password) in specs.items():
            if not host:
                continue
            self.phase_states[phase] = RuntimeState()
            self.phase_clients[phase] = ShellyClient(
                self.cfg, host, em1_id, user, password
            )

    def phase_configured(self, phase: str) -> bool:
        """Return whether a phase has an active sensor configuration."""

        return phase in self.phase_clients

    def restore_phase(self, phase: str) -> None:
        """Restore one configured phase from SQLite."""

        state = self.phase_states[phase]
        latest = self.storage.latest(phase)
        if latest:
            state.restore(*latest)
        total = self.storage.outage_total(phase)
        state.restore_last_outage_duration(
            self.storage.latest_closed_outage_duration(phase)
        )
        opened = self.storage.open_outage_started(phase)
        if opened is None:
            state.restore_outage_count(total)
        else:
            state.restore_outage(opened, total)

    def poll_secondary_phase(self, phase: str) -> bool:
        """Poll one optional phase without affecting L1 compatibility."""

        state = self.phase_states[phase]
        client = self.phase_clients[phase]
        try:
            voltage, frequency, rpc_ms = client.read()
        except Exception as exc:
            return self.record_secondary_failure(phase, state, exc)
        self.accept_phase_sample(
            phase, (voltage, frequency, rpc_ms), time.time()
        )
        return True

    def record_secondary_failure(
        self, phase: str, state: RuntimeState, exc: Exception
    ) -> bool:
        """Record an optional-phase failure and its independent outage."""

        changed = state.error(str(exc), self.cfg.offline_after_failures)
        snap = state.snapshot()
        if changed and snap["connectivity"] == "offline":
            started = float(snap["offline_since"] or time.time())
            if self.storage.begin_outage(started, phase):
                self.maybe_phase_outage(phase)
        logger.debug("{} poll unavailable: {}", phase, exc)
        return False

    def poll_optional_phases(self) -> None:
        """Poll all configured phases after the legacy-compatible L1 poll."""

        for phase in ("L2", "L3"):
            if self.phase_configured(phase):
                self.poll_secondary_phase(phase)

    def maybe_phase_outage(self, phase: str) -> None:
        """Notify about one optional-phase outage transition."""

        if not self.availability_alerts_enabled or not self.telegram.enabled:
            return
        self.telegram.send(
            f"⚠️ {phase}: датчик недоступен",
            chat_id=self.telegram.status_chat_id,
        )

    def maybe_phase_recovery(self, phase: str, duration: float) -> None:
        """Notify when an optional phase sensor recovers."""

        if not self.availability_alerts_enabled or not self.telegram.enabled:
            return
        self.telegram.send(
            f"✅ {phase}: питание/датчик восстановлены\n"
            f"⏱ {format_duration(duration)}",
            chat_id=self.telegram.status_chat_id,
        )

    def maybe_secondary_alerts(
        self, phase: str, voltage: float, frequency: float
    ) -> None:
        """Apply low-voltage and level alerts to L2/L3 independently."""

        if not self.telegram.enabled or self.maintenance_active():
            return
        now = time.monotonic()
        self.maybe_secondary_low(phase, voltage, frequency, now)
        self.maybe_secondary_level(phase, voltage, frequency, now)

    def maybe_secondary_low(
        self, phase: str, voltage: float, frequency: float, now: float
    ) -> None:
        """Handle loud low-voltage alert dwell for one optional phase."""

        if not self.low_voltage_alert_enabled:
            return
        if (
            voltage < self.low_voltage_alert_v
            and not self.phase_low_active[phase]
        ):
            self.phase_low_candidates.setdefault(phase, now)
            if now - self.phase_low_candidates[phase] < self.alert_dwell:
                return
            self.phase_low_active[phase] = True
            self.telegram.send(
                f"🚨 {phase}: низкое напряжение {voltage:.1f} V / "
                f"{frequency:.2f} Hz",
                notify=True,
            )
        elif voltage >= self.low_voltage_recovery_v:
            self.phase_low_candidates.pop(phase, None)
            if self.phase_low_active[phase]:
                self.phase_low_active[phase] = False
                self.telegram.send(
                    f"✅ {phase}: напряжение восстановилось · {voltage:.1f} V"
                )

    def maybe_secondary_level(
        self, phase: str, voltage: float, frequency: float, now: float
    ) -> None:
        """Handle warning/critical transition dwell for one optional phase."""

        if not self.alerts_enabled:
            return
        level = self.runtime_voltage_level(voltage)
        if level == self.phase_alert_levels[phase]:
            self.phase_alert_candidates.pop(phase, None)
            return
        candidate = self.phase_alert_candidates.get(phase)
        if candidate is None or candidate[0] != level:
            self.phase_alert_candidates[phase] = (level, now)
            return
        dwell = self.recovery_dwell if level == "normal" else self.alert_dwell
        if now - candidate[1] < dwell:
            return
        self.phase_alert_levels[phase] = level
        self.phase_alert_candidates.pop(phase, None)
        self.telegram.send(
            f"⚠️ {phase}: {level} · {voltage:.1f} V / {frequency:.2f} Hz"
        )

    def initialize(self) -> None:
        """Initialize storage and start health service."""

        self.storage.initialize()
        self.load_runtime_settings()
        self.last_summary_day = self.storage.get_meta("daily_summary_day")
        self.restore_voltage_event()
        for phase in self.phase_states:
            self.restore_phase(phase)
        latest = self.storage.latest("L1")
        if latest:
            logger.info(
                "Restored L1 last valid sample: {:.1f} V / {:.2f} Hz",
                latest[1],
                latest[2],
            )
        start_health_server(self)
        stored_maintenance = self.storage.get_meta("maintenance_until")
        self.maintenance_until = float(stored_maintenance or 0)
        mode = "enabled" if self.telegram.enabled else "disabled"
        logger.info("Telegram integration: {}", mode)
        if self.telegram.enabled:
            try:
                self.telegram.set_commands()
            except Exception as exc:
                logger.warning("Telegram command registration failed: {}", exc)

    def clear_voltage_event(self) -> None:
        """Remove a stale voltage transition after a confirmed outage."""

        self.last_voltage_event = None
        self.storage.delete_meta("last_voltage_event")

    def record_poll_failure(self, exc: Exception) -> None:
        """Record one failed poll and persist an offline transition."""

        changed = self.state.error(str(exc), self.cfg.offline_after_failures)
        snap = self.state.snapshot()
        now = time.monotonic()
        should_warn = (
            changed
            or int(snap["consecutive_failures"]) == 1
            or now - self.last_failure_log >= self.cfg.offline_log_interval
        )
        if should_warn:
            logger.warning(
                "Shelly {} after {} failed poll(s): {}",
                snap["connectivity"],
                snap["consecutive_failures"],
                exc,
            )
            self.last_failure_log = now
        else:
            logger.debug("Shelly read still unavailable: {}", exc)
        if changed and snap["connectivity"] == "offline":
            self.clear_voltage_event()
            started = float(snap["offline_since"] or time.time())
            if self.storage.begin_outage(started):
                self.maybe_availability_alert("offline")

    def accept_phase_sample(
        self, phase: str, sample: tuple[float, float, float], now: float
    ) -> None:
        """Persist one valid phase sample and update its state/alerts."""

        voltage, frequency, rpc_ms = sample
        self.storage.add(now, voltage, frequency, phase)
        state = self.phase_states[phase]
        changed = state.success(
            voltage, frequency, rpc_ms, self.cfg.recovery_after_successes
        )
        if phase == "L1":
            self.maybe_prune()
            if changed and state.snapshot()["connectivity"] == "online":
                duration = self.storage.end_outage(now, phase)
                if duration is not None:
                    self.rotate_status_after_recovery(duration)
            self.maybe_low_voltage_alert(voltage, frequency)
            self.maybe_alert(voltage, frequency)
            return
        if changed and state.snapshot()["connectivity"] == "online":
            duration = self.storage.end_outage(now, phase)
            if duration is not None:
                self.maybe_phase_recovery(phase, duration)
        self.maybe_secondary_alerts(phase, voltage, frequency)

    def record_phase_missing(self, phase: str, reason: str) -> None:
        """Record a missing Pro3EM phase without storing a fake sample."""

        error = RuntimeError(reason)
        if phase == "L1":
            self.record_poll_failure(error)
            return
        self.record_secondary_failure(phase, self.phase_states[phase], error)

    def poll_pro3em_once(self) -> bool:
        """Read and persist all Pro3EM triphase values in one polling cycle."""

        try:
            readings = self.shelly.read_phases()
        except Exception as exc:
            for phase in ("L1", "L2", "L3"):
                self.record_phase_missing(phase, str(exc))
            return False
        now = time.time()
        for phase in ("L1", "L2", "L3"):
            sample = readings.get(phase)
            if sample is None:
                self.record_phase_missing(
                    phase, f"{phase} missing from EM.GetStatus"
                )
            else:
                self.accept_phase_sample(phase, sample, now)
        return bool(readings)

    def poll_cycle(self) -> bool:
        """Poll either a Pro3EM atomically or the legacy/separate sensors."""

        if self.meter_type == "pro3em":
            return self.poll_pro3em_once()
        ok = self.poll_once()
        self.poll_optional_phases()
        return ok

    def poll_once(self) -> bool:
        """Read Shelly and persist only valid measurements."""

        try:
            voltage, frequency, rpc_ms = self.shelly.read()
        except Exception as exc:
            self.record_poll_failure(exc)
            return False
        self.accept_phase_sample(
            "L1", (voltage, frequency, rpc_ms), time.time()
        )
        return True

    def maybe_prune(self) -> None:
        """Prune old history at most once every six hours."""

        now = time.monotonic()
        if now < self.next_prune:
            return
        cutoff = time.time() - self.cfg.retention_days * 86400
        self.storage.rollup_hourly(cutoff)
        deleted = self.storage.prune(self.cfg.retention_days)
        self.next_prune = now + 21600
        if deleted:
            logger.info("Pruned {} old measurements", deleted)

    def telegram_due(self, voltage: float | None, connectivity: str) -> bool:
        """Return whether the stable Telegram status should be updated."""

        elapsed = time.monotonic() - self.last_telegram
        if elapsed < self.cfg.telegram_min_edit_interval:
            return False
        if self.last_sent_connectivity != connectivity:
            return True
        if elapsed >= self.cfg.telegram_interval:
            return True
        if voltage is None or self.last_sent_voltage is None:
            return voltage is not None
        return (
            abs(voltage - self.last_sent_voltage)
            >= self.cfg.significant_change
        )

    def status_keyboard(self) -> dict | None:
        """Return read-only channel controls for the stable status message."""

        if not self.cfg.telegram_status_chat_id:
            return None
        return {
            "inline_keyboard": [
                [
                    {"text": "🔄 Обновить", "callback_data": "view:refresh"},
                    {"text": "📊 Сегодня", "callback_data": "view:today"},
                    {"text": "🔌 Отключения", "callback_data": "view:outages"},
                ]
            ]
        }

    def status_callback_text(self, action: str) -> str:
        """Render compact read-only responses for status-channel buttons."""

        if action == "today":
            rows = []
            for phase in ("L1", "L2", "L3"):
                if not self.phase_configured(phase):
                    continue
                stats = self.storage.day_stats(time.time(), phase)
                rows.append(
                    f"{phase} {fmt(stats['min'])}–{fmt(stats['max'])}V"
                )
            return "Сегодня: " + " · ".join(rows)
        if action == "outages":
            rows = []
            for phase in ("L1", "L2", "L3"):
                if not self.phase_configured(phase):
                    continue
                stats = self.storage.outage_stats(time.time(), phase)
                rows.append(
                    f"{phase} {stats['count']}/"
                    f"{format_duration(float(stats['duration']))}"
                )
            return "Отключения: " + " · ".join(rows)
        return "Статус обновлён"

    def handle_status_callback(self, query: dict) -> None:
        """Handle non-mutating buttons attached to the channel status."""

        callback_id = str(query.get("id", ""))
        chat_id = str(
            (query.get("message") or {}).get("chat", {}).get("id", "")
        )
        if chat_id != self.telegram.status_chat_id:
            self.telegram.answer_callback(callback_id)
            return
        action = str(query.get("data", "")).split(":", 1)[-1]
        if action == "refresh":
            self.last_telegram = 0.0
            self.update_status()
        self.telegram.answer_callback(
            callback_id, self.status_callback_text(action)
        )

    def status_message_key(self) -> str:
        """Return the persisted key for the stable status destination."""

        if self.cfg.telegram_status_chat_id:
            return "telegram_status_message_id"
        return "telegram_message_id"

    def upsert_status_message(self, text: str, key: str, target: str) -> bool:
        """Edit the stable status or recreate a missing Telegram message."""

        stored = self.storage.get_meta(key)
        markup = self.status_keyboard()
        try:
            if stored:
                self.telegram.edit(
                    int(stored), text, reply_markup=markup, chat_id=target
                )
            else:
                message_id = self.telegram.send(
                    text, reply_markup=markup, chat_id=target
                )
                self.storage.set_meta(key, str(message_id))
            return True
        except Exception as exc:
            if stored and message_not_modified(exc):
                logger.debug("Telegram status message is already current")
                return True
            if not (stored and message_missing(exc)):
                logger.warning("Telegram status update failed: {}", exc)
                return False
        try:
            message_id = self.telegram.send(
                text, reply_markup=markup, chat_id=target
            )
            self.storage.set_meta(key, str(message_id))
            return True
        except Exception as exc:
            logger.warning("Telegram status recreation failed: {}", exc)
            return False

    def update_status(self) -> None:
        """Create or edit the stable Telegram message."""

        if not self.telegram.enabled:
            return
        snap = self.state.snapshot()
        voltage = None if snap["voltage"] is None else float(snap["voltage"])
        connectivity = str(snap["connectivity"])
        if not self.telegram_due(voltage, connectivity):
            return
        key = self.status_message_key()
        if not self.upsert_status_message(
            multiphase_status_text(self), key, self.telegram.status_chat_id
        ):
            return
        latest_id = self.storage.get_meta(key)
        if latest_id:
            self.ensure_status_pinned(int(latest_id))
        self.last_sent_voltage = voltage
        self.last_sent_connectivity = connectivity
        self.last_telegram = time.monotonic()

    def ensure_status_pinned(self, message_id: int) -> None:
        """Pin a channel status once; failures are non-fatal."""

        if not self.pin_status or not self.cfg.telegram_status_chat_id:
            return
        stored = self.storage.get_meta("telegram_status_pinned_id")
        if stored == str(message_id):
            return
        try:
            self.telegram.pin(message_id, self.telegram.status_chat_id)
            self.storage.set_meta("telegram_status_pinned_id", str(message_id))
        except Exception as exc:
            logger.warning("Telegram status pin failed: {}", exc)

    def rotate_status_after_recovery(self, duration: float) -> None:
        """Replace the stable status post after a confirmed power recovery."""

        if not self.telegram.enabled:
            return
        key = self.status_message_key()
        stored = self.storage.get_meta(key)
        if stored:
            try:
                self.telegram.delete(int(stored), self.telegram.status_chat_id)
            except Exception as exc:
                logger.warning("Telegram old status delete failed: {}", exc)
        self.storage.delete_meta(key)
        self.storage.delete_meta("telegram_status_pinned_id")
        self.last_recovery_duration = duration
        self.last_telegram = 0.0
        self.last_sent_connectivity = None
        self.update_status()

    def maybe_availability_alert(
        self, state: str, duration: float | None = None
    ) -> None:
        """Send one optional alert for an availability transition."""

        if not self.availability_alerts_enabled or not self.telegram.enabled:
            return
        if state == "offline":
            text = (
                "⚠️ Shelly недоступен. "
                "Последнее корректное измерение сохранено."
            )
        else:
            text = (
                "✅ Shelly восстановлен\n"
                f"⏱ Длительность: {format_duration(duration)}"
            )
        try:
            self.telegram.send(text, chat_id=self.telegram.status_chat_id)
        except Exception as exc:
            logger.warning("Telegram availability alert failed: {}", exc)

    def restore_voltage_event(self) -> None:
        """Restore the latest voltage transition from SQLite metadata."""

        raw = self.storage.get_meta("last_voltage_event")
        if not raw:
            return
        try:
            payload = json.loads(raw)
            self.last_voltage_event = (
                str(payload["kind"]),
                float(payload["voltage"]),
                float(payload["frequency"]),
                float(payload["timestamp"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Ignoring invalid stored voltage event metadata")

    def record_voltage_event(
        self, kind: str, voltage: float, frequency: float
    ) -> None:
        """Store a voltage transition and refresh the stable status header."""

        happened_at = time.time()
        self.last_voltage_event = (kind, voltage, frequency, happened_at)
        payload = {
            "kind": kind,
            "voltage": voltage,
            "frequency": frequency,
            "timestamp": happened_at,
        }
        self.storage.set_meta("last_voltage_event", json.dumps(payload))
        self.last_telegram = 0.0
        self.update_status()

    def maybe_low_voltage_alert(
        self, voltage: float, frequency: float
    ) -> None:
        """Show low-voltage and recovery transitions in the status header."""

        if not self.low_voltage_alert_enabled or not self.telegram.enabled:
            return
        if self.maintenance_active():
            return
        now = time.monotonic()
        if voltage < self.low_voltage_alert_v and not self.low_voltage_active:
            self.low_voltage_candidate_since = (
                self.low_voltage_candidate_since or now
            )
            if now - self.low_voltage_candidate_since < self.alert_dwell:
                return
            self.low_voltage_active = True
            self.record_voltage_event("low", voltage, frequency)
            return
        if voltage >= self.low_voltage_recovery_v:
            self.low_voltage_candidate_since = None
            if self.low_voltage_active:
                self.low_voltage_active = False
                self.record_voltage_event("recovered", voltage, frequency)

    def runtime_voltage_level(self, voltage: float) -> str:
        """Classify voltage using live-adjustable alert thresholds."""

        if voltage <= self.critical_low or voltage >= self.critical_high:
            return "critical"
        if voltage <= self.warning_low or voltage >= self.warning_high:
            return "warning"
        return "normal"

    def maybe_alert(self, voltage: float, frequency: float) -> None:
        """Send quiet warning/critical transitions after dwell hysteresis."""

        if (
            not self.alerts_enabled
            or not self.telegram.enabled
            or self.maintenance_active()
        ):
            return
        level = self.runtime_voltage_level(voltage)
        if level == self.alert_level:
            self.alert_candidate = None
            return
        now = time.monotonic()
        if not self.alert_candidate or self.alert_candidate[0] != level:
            self.alert_candidate = (level, now)
            return
        dwell = self.recovery_dwell if level == "normal" else self.alert_dwell
        if now - self.alert_candidate[1] < dwell:
            return
        self.alert_level = level
        self.alert_candidate = None
        label = {
            "normal": "✅ Напряжение вернулось в норму",
            "warning": "⚠️ Предупреждение по напряжению",
            "critical": "🚨 Критическое напряжение",
        }[level]
        self.telegram.send(f"{label}\n\n{voltage:.1f} V\n{frequency:.2f} Hz")

    def maintenance_active(self) -> bool:
        """Return whether alert delivery is temporarily suppressed."""

        return time.time() < self.maintenance_until

    def set_maintenance(self, minutes: int) -> str:
        """Set or clear maintenance mode."""

        self.maintenance_until = (
            0.0 if minutes <= 0 else time.time() + minutes * 60
        )
        self.storage.set_meta("maintenance_until", str(self.maintenance_until))
        return "OFF" if minutes <= 0 else f"{minutes} мин"

    def quality_text(self, phase: str = "L1", hours: int = 24) -> str:
        """Render voltage-quality statistics for one phase."""

        since = time.time() - hours * 3600
        q = quality_stats(
            self.cfg.database_path,
            phase,
            since,
            self.cfg.warning_low,
            self.cfg.critical_low,
            self.cfg.warning_high,
        )
        return (
            f"📊 Качество {phase} за {hours}ч\n"
            f"Измерений: {q['samples']}\n"
            f"Ниже {self.cfg.warning_low:g} V: {q['below_warning']}\n"
            f"Ниже {self.cfg.critical_low:g} V: {q['below_critical']}\n"
            f"Выше {self.cfg.warning_high:g} V: {q['above_warning']}\n"
            f"Вне нормы: {q['bad_pct']:.2f}%"
        )

    def outage_history_text(self, phase: str = "L1") -> str:
        """Render recent confirmed outage intervals."""

        try:
            rows = outage_history(self.cfg.database_path, phase, 10)
        except sqlite3.Error:
            stats = self.storage.outage_stats(time.time(), phase)
            return (
                f"🔌 Подтверждённых отключений сегодня: {stats['count']}\n"
                "⏱ Подтверждённая недоступность: "
                f"{format_duration(float(stats['duration']))}\n"
                f"ℹ️ Границы: {self.cfg.offline_after_failures} ошибок / "
                f"{self.cfg.recovery_after_successes} успешных опросов"
            )
        lines = [f"🔌 Последние отключения {phase}"]
        now = time.time()
        for start, end in rows:
            duration = (end or now) - start
            state = "идёт сейчас" if end is None else format_duration(duration)
            lines.append(f"{format_stamp(start, self.cfg.timezone)} — {state}")
        return "\n".join(lines) if rows else f"{phase}: отключений нет"

    def daily_summary_text(self) -> str:
        """Render the three-phase daily operational summary."""

        since = time.time() - 86400
        lines = ["📅 Суточная сводка"]
        for phase in ("L1", "L2", "L3"):
            if not self.phase_configured(phase):
                lines.append(f"⚪ {phase}: не настроена")
                continue
            st = phase_stats(self.cfg.database_path, phase, since)
            outages = self.storage.outage_stats(time.time(), phase)
            lines.append(
                f"{phase}: {fmt(st['min'])}–{fmt(st['max'])} V, "
                f"avg {fmt(st['avg'])} V; "
                f"отключений {outages['count']}, "
                f"{format_duration(float(outages['duration']))}"
            )
        return "\n".join(lines)

    def maybe_daily_summary(self) -> None:
        """Post one daily summary after the configured local time."""

        if not self.daily_summary_enabled or not self.telegram.enabled:
            return
        now = datetime.now(ZoneInfo(self.cfg.timezone))
        day = now.date().isoformat()
        due = (now.hour, now.minute) >= (
            self.daily_summary_hour,
            self.daily_summary_minute,
        )
        if not due or day == self.last_summary_day:
            return
        self.telegram.send(
            self.daily_summary_text(), chat_id=self.telegram.status_chat_id
        )
        self.last_summary_day = day
        self.storage.set_meta("daily_summary_day", day)

    def device_health_text(self) -> str:
        """Render read-only device health for configured phases."""

        lines = ["🔧 Shelly device health"]
        for phase in ("L1", "L2", "L3"):
            if not self.phase_configured(phase):
                lines.append(f"{phase}: не настроена")
                continue
            try:
                info = self.phase_clients[phase].device_health()
                skew = info.get("time_skew_sec")
                skew_text = "n/a" if skew is None else f"{float(skew):+.1f}s"
                lines.append(
                    f"{phase}: {info.get('model')} · "
                    f"fw {info.get('firmware')} · "
                    f"uptime {info.get('uptime')}s · skew {skew_text}"
                )
            except Exception as exc:
                lines.append(f"{phase}: offline ({type(exc).__name__})")
        return "\n".join(lines)

    def backup_health_text(self) -> str:
        """Render backup and SQLite health for diagnostics."""

        info = backup_health(self.cfg.backup_dir)
        if info["status"] != "ok":
            return "💾 Backup: отсутствует"
        stamp = format_stamp(float(info["mtime"]), self.cfg.timezone)
        size = int(info["size"]) // 1024
        return (
            f"💾 Backup: {stamp}, {size} KiB; "
            f"SQLite: {self.storage.integrity()}"
        )

    def audit_text(self) -> str:
        """Render recent admin changes without exposing secrets."""

        lines = ["🧾 Последние изменения настроек"]
        for ts, user, setting, old, new in self.storage.audit_recent(10):
            stamp = format_stamp(float(ts), self.cfg.timezone)
            lines.append(f"{stamp} · {user} · {setting}: {old} → {new}")
        return "\n".join(lines)

    def setting_defaults(self) -> dict[str, str]:
        """Return notification defaults from environment configuration."""

        return {
            "meter_type": self.cfg.shelly_meter_type,
            "silent": "1" if self.cfg.telegram_silent_default else "0",
            "quiet_enabled": "1",
            "quiet_start": str(self.cfg.telegram_quiet_start_hour),
            "quiet_end": str(self.cfg.telegram_quiet_end_hour),
            "low_enabled": "1" if self.cfg.low_voltage_alert_enabled else "0",
            "low_v": str(self.cfg.low_voltage_alert_v),
            "recovery_v": str(self.cfg.low_voltage_recovery_v),
            "alerts_enabled": "1" if self.cfg.alerts_enabled else "0",
            "warning_low": str(self.cfg.warning_low),
            "critical_low": str(self.cfg.critical_low),
            "warning_high": str(self.cfg.warning_high),
            "critical_high": str(self.cfg.critical_high),
            "availability_enabled": "1"
            if self.cfg.availability_alerts_enabled
            else "0",
            "daily_enabled": "1" if self.cfg.daily_summary_enabled else "0",
            "daily_hour": str(self.cfg.daily_summary_hour),
            "daily_minute": str(self.cfg.daily_summary_minute),
            "pin_status": "1" if self.cfg.telegram_pin_status else "0",
            "alert_dwell": str(self.cfg.alert_dwell_sec),
            "recovery_dwell": str(self.cfg.alert_recovery_dwell_sec),
        }

    def setting_value(self, name: str) -> str:
        """Read one runtime setting with environment fallback."""

        stored = self.storage.get_meta(f"telegram_setting_{name}")
        return stored if stored is not None else self.setting_defaults()[name]

    def load_runtime_settings(self) -> None:
        """Apply persisted Telegram settings without restarting the service."""

        meter_type = self.setting_value("meter_type")
        if meter_type != self.meter_type:
            self.reconfigure_meter_type(meter_type)
        self.telegram.silent_default = self.setting_value("silent") == "1"
        self.telegram.quiet_enabled = (
            self.setting_value("quiet_enabled") == "1"
        )
        self.telegram.quiet_start_hour = int(self.setting_value("quiet_start"))
        self.telegram.quiet_end_hour = int(self.setting_value("quiet_end"))
        self.low_voltage_alert_enabled = (
            self.setting_value("low_enabled") == "1"
        )
        self.low_voltage_alert_v = float(self.setting_value("low_v"))
        self.low_voltage_recovery_v = float(self.setting_value("recovery_v"))
        self.alerts_enabled = self.setting_value("alerts_enabled") == "1"
        self.warning_low = float(self.setting_value("warning_low"))
        self.critical_low = float(self.setting_value("critical_low"))
        self.warning_high = float(self.setting_value("warning_high"))
        self.critical_high = float(self.setting_value("critical_high"))
        self.availability_alerts_enabled = (
            self.setting_value("availability_enabled") == "1"
        )
        self.daily_summary_enabled = self.setting_value("daily_enabled") == "1"
        self.daily_summary_hour = int(self.setting_value("daily_hour"))
        self.daily_summary_minute = int(self.setting_value("daily_minute"))
        self.pin_status = self.setting_value("pin_status") == "1"
        self.alert_dwell = float(self.setting_value("alert_dwell"))
        self.recovery_dwell = float(self.setting_value("recovery_dwell"))

    def is_admin(self, user_id: str) -> bool:
        """Return whether a Telegram user may change runtime settings."""

        return user_id in self.cfg.telegram_admin_user_ids

    def settings_text(self) -> str:
        """Render the current Telegram notification policy."""
        meter_name = METER_MODELS.get(self.meter_type, self.meter_type)
        silent = "тихие" if self.telegram.silent_default else "со звуком"
        quiet = "ON" if self.telegram.quiet_enabled else "OFF"
        low = "ON" if self.low_voltage_alert_enabled else "OFF"
        levels = "ON" if self.alerts_enabled else "OFF"
        maintenance = "ON" if self.maintenance_active() else "OFF"
        availability = "ON" if self.availability_alerts_enabled else "OFF"
        summary = "ON" if self.daily_summary_enabled else "OFF"
        pin = "ON" if self.pin_status else "OFF"
        l2 = "ON" if self.phase_configured("L2") else "—"
        l3 = "ON" if self.phase_configured("L3") else "—"
        quiet_hours = (
            f"{self.telegram.quiet_start_hour:02d}:00–"
            f"{self.telegram.quiet_end_hour:02d}:00"
        )
        voltage_range = (
            f"{self.critical_low:g}/{self.warning_low:g}–"
            f"{self.warning_high:g}/{self.critical_high:g} V"
        )
        lines = [
            "⚙️ Настройки бота",
            "",
            f"📟 Модуль: {meter_name}",
            f"🔕 Обычные сообщения: {silent}",
            f"🌙 Ночной режим: {quiet} {quiet_hours}",
            f"🚨 Низкое напряжение: {low} < {self.low_voltage_alert_v:g} V",
            f"✅ Сброс тревоги: ≥ {self.low_voltage_recovery_v:g} V",
            f"⚠️ Уровни: {levels} {voltage_range}",
            f"🛠 Maintenance: {maintenance}",
            f"🔌 Фазы: L1 ON · L2 {l2} · L3 {l3}",
            f"🔌 Outage alerts: {availability}",
            f"📅 Сводка: {summary} "
            f"{self.daily_summary_hour:02d}:{self.daily_summary_minute:02d}",
            f"📌 Pin status: {pin}",
            f"⏳ Dwell: {self.alert_dwell:g}s / "
            f"recovery {self.recovery_dwell:g}s",
        ]
        return "\n".join(lines)

    def setting_button(self, text: str, data: str) -> dict[str, str]:
        """Build one Telegram settings callback button."""

        return {"text": text, "callback_data": f"cfg:{data}"}

    def meter_setting_rows(self) -> list[list[dict[str, str]]]:
        """Build Shelly model selector rows."""

        button = self.setting_button
        return [
            [button("📟 Pro EM-50", "meter_em50")],
            [button("💰 EM Gen3", "meter_em_gen3")],
            [button("💰 PM Mini Gen3", "meter_pm_mini_gen3")],
            [button("💰 Plus PM Mini", "meter_plus_pm_mini")],
            [button("🔌 Plus 1PM", "meter_plus_1pm")],
            [
                button("🔌 1PM Gen3", "meter_1pm_gen3"),
                button("1PM Gen4", "meter_1pm_gen4"),
            ],
            [
                button("🔌 1PM Mini G3", "meter_1pm_mini_gen3"),
                button("1PM Mini G4", "meter_1pm_mini_gen4"),
            ],
            [button("⚡ Pro 3EM / 3CT63", "meter_pro3em")],
        ]

    def notification_setting_rows(self) -> list[list[dict[str, str]]]:
        """Build basic notification setting rows."""

        button = self.setting_button
        return [
            [button("🔕 Тихие ON/OFF", "silent_toggle")],
            [button("🌙 Ночь ON/OFF", "quiet_toggle")],
            [
                button("◀ Начало -1ч", "start_dec"),
                button("Начало +1ч ▶", "start_inc"),
            ],
            [
                button("◀ Конец -1ч", "end_dec"),
                button("Конец +1ч ▶", "end_inc"),
            ],
            [button("🚨 Alert ON/OFF", "low_toggle")],
            [button("Порог -5V", "low_dec"), button("Порог +5V", "low_inc")],
            [
                button("Recovery -5V", "rec_dec"),
                button("Recovery +5V", "rec_inc"),
            ],
        ]

    def voltage_setting_rows(self) -> list[list[dict[str, str]]]:
        """Build warning and critical voltage setting rows."""

        button = self.setting_button
        return [
            [button("⚠️ Уровни ON/OFF", "alerts_toggle")],
            [
                button("Warn low -5V", "warnlow_dec"),
                button("Warn low +5V", "warnlow_inc"),
            ],
            [
                button("Crit low -5V", "critlow_dec"),
                button("Crit low +5V", "critlow_inc"),
            ],
            [
                button("Warn high -5V", "warnhigh_dec"),
                button("Warn high +5V", "warnhigh_inc"),
            ],
            [
                button("Crit high -5V", "crithigh_dec"),
                button("Crit high +5V", "crithigh_inc"),
            ],
        ]

    def schedule_setting_rows(self) -> list[list[dict[str, str]]]:
        """Build outage, summary, pin, and maintenance setting rows."""

        button = self.setting_button
        return [
            [button("🔌 Outage alerts ON/OFF", "availability_toggle")],
            [button("📅 Summary ON/OFF", "daily_toggle")],
            [
                button("Summary -1h", "dailyhour_dec"),
                button("Summary +1h", "dailyhour_inc"),
            ],
            [
                button("Summary -5m", "dailymin_dec"),
                button("Summary +5m", "dailymin_inc"),
            ],
            [button("📌 Pin ON/OFF", "pin_toggle")],
            [
                button("Dwell -5s", "dwell_dec"),
                button("Dwell +5s", "dwell_inc"),
            ],
            [
                button("Recovery -5s", "recdwell_dec"),
                button("Recovery +5s", "recdwell_inc"),
            ],
            [
                button("🛠 Maintenance 60m", "maint_60"),
                button("Maintenance OFF", "maint_off"),
            ],
            [button("↩️ Сбросить к .env", "reset")],
        ]

    def settings_keyboard(self) -> dict:
        """Build the admin inline keyboard from small setting groups."""

        rows = self.meter_setting_rows()
        rows.extend(self.notification_setting_rows())
        rows.extend(self.voltage_setting_rows())
        rows.extend(self.schedule_setting_rows())
        return {"inline_keyboard": rows}

    def apply_meter_setting(self, action: str, values: dict[str, str]) -> bool:
        """Select one supported Shelly monitoring backend."""

        choices = {
            "meter_em50": "pro_em50",
            "meter_em_gen3": "em_gen3",
            "meter_pm_mini_gen3": "pm_mini_gen3",
            "meter_plus_pm_mini": "plus_pm_mini",
            "meter_plus_1pm": "plus_1pm",
            "meter_1pm_gen3": "1pm_gen3",
            "meter_1pm_gen4": "1pm_gen4",
            "meter_1pm_mini_gen3": "1pm_mini_gen3",
            "meter_1pm_mini_gen4": "1pm_mini_gen4",
            "meter_pro3em": "pro3em",
        }
        if action not in choices:
            return False
        values["meter_type"] = choices[action]
        return True

    def apply_basic_setting(self, action: str, values: dict[str, str]) -> bool:
        """Apply toggles and quiet-hour actions; return whether handled."""

        toggles = {
            "silent_toggle": "silent",
            "quiet_toggle": "quiet_enabled",
            "low_toggle": "low_enabled",
            "alerts_toggle": "alerts_enabled",
            "availability_toggle": "availability_enabled",
            "daily_toggle": "daily_enabled",
            "pin_toggle": "pin_status",
        }
        if action in toggles:
            key = toggles[action]
            values[key] = "0" if values[key] == "1" else "1"
            return True
        if action not in {"start_dec", "start_inc", "end_dec", "end_inc"}:
            return False
        key = "quiet_start" if action.startswith("start") else "quiet_end"
        step = -1 if action.endswith("dec") else 1
        values[key] = str((int(values[key]) + step) % 24)
        return True

    def apply_low_setting(self, action: str, values: dict[str, str]) -> bool:
        """Adjust low-voltage and recovery thresholds."""

        if action not in {"low_dec", "low_inc", "rec_dec", "rec_inc"}:
            return False
        key = "low_v" if action.startswith("low") else "recovery_v"
        step = -5.0 if action.endswith("dec") else 5.0
        candidate = max(100.0, min(260.0, float(values[key]) + step))
        values[key] = f"{candidate:g}"
        if float(values["recovery_v"]) > float(values["low_v"]):
            return True
        if key == "low_v":
            values["recovery_v"] = f"{min(265.0, candidate + 5):g}"
        else:
            values["low_v"] = f"{max(95.0, candidate - 5):g}"
        return True

    def apply_timing_setting(
        self, action: str, values: dict[str, str]
    ) -> bool:
        """Adjust daily-summary time and alert dwell values."""

        if action in {"dailyhour_dec", "dailyhour_inc"}:
            step = -1 if action.endswith("dec") else 1
            values["daily_hour"] = str((int(values["daily_hour"]) + step) % 24)
            return True
        if action in {"dailymin_dec", "dailymin_inc"}:
            step = -5 if action.endswith("dec") else 5
            values["daily_minute"] = str(
                (int(values["daily_minute"]) + step) % 60
            )
            return True
        if action in {
            "dwell_dec",
            "dwell_inc",
            "recdwell_dec",
            "recdwell_inc",
        }:
            key = (
                "recovery_dwell"
                if action.startswith("recdwell")
                else "alert_dwell"
            )
            step = -5.0 if action.endswith("dec") else 5.0
            values[key] = (
                f"{max(0.0, min(300.0, float(values[key]) + step)):g}"
            )
            return True
        return False

    def apply_level_setting(self, action: str, values: dict[str, str]) -> bool:
        """Adjust ordered warning and critical voltage thresholds."""

        prefixes = {
            "warnlow": "warning_low",
            "critlow": "critical_low",
            "warnhigh": "warning_high",
            "crithigh": "critical_high",
        }
        prefix = action.rsplit("_", 1)[0]
        if prefix not in prefixes or not action.endswith(("dec", "inc")):
            return False
        key = prefixes[prefix]
        step = -5.0 if action.endswith("dec") else 5.0
        values[key] = f"{max(100.0, min(300.0, float(values[key]) + step)):g}"
        order = (
            "critical_low",
            "warning_low",
            "warning_high",
            "critical_high",
        )
        ordered = [float(values[name]) for name in order]
        if ordered != sorted(ordered):
            raise ValueError("threshold order")
        return True

    def persist_settings(
        self, values: dict[str, str], original: dict[str, str], user_id: str
    ) -> None:
        """Persist setting values and append best-effort audit entries."""

        for name, value in values.items():
            self.storage.set_meta(f"telegram_setting_{name}", value)
            if original.get(name) == value:
                continue
            try:
                self.storage.audit_setting(
                    user_id, name, original.get(name), value
                )
            except sqlite3.Error as exc:
                logger.warning(
                    "Settings audit write failed: {}", type(exc).__name__
                )
        self.load_runtime_settings()

    def update_setting(self, action: str, user_id: str = "system") -> None:
        """Apply one validated settings action and persist it."""

        values = {
            name: self.setting_value(name) for name in self.setting_defaults()
        }
        original = values.copy()
        if action in {"maint_60", "maint_off"}:
            self.set_maintenance(60 if action == "maint_60" else 0)
            return
        if action == "reset":
            values = self.setting_defaults()
        elif not (
            self.apply_meter_setting(action, values)
            or self.apply_basic_setting(action, values)
            or self.apply_low_setting(action, values)
            or self.apply_timing_setting(action, values)
            or self.apply_level_setting(action, values)
        ):
            raise ValueError("unknown settings action")
        self.persist_settings(values, original, user_id)

    def handle_callback(self, update: dict) -> None:
        """Handle one admin settings callback."""

        query = update.get("callback_query") or {}
        if str(query.get("data", "")).startswith("view:"):
            self.handle_status_callback(query)
            return
        message = query.get("message") or {}
        sender = query.get("from") or {}
        if (
            str(message.get("chat", {}).get("id", ""))
            != self.cfg.telegram_chat_id
        ):
            return
        callback_id = str(query.get("id", ""))
        user_id = str(sender.get("id", ""))
        if not self.is_admin(user_id):
            self.telegram.answer_callback(callback_id, "Только администратор")
            return
        data = str(query.get("data", ""))
        if not data.startswith("cfg:"):
            self.telegram.answer_callback(callback_id)
            return
        try:
            self.update_setting(data.split(":", 1)[1], user_id)
            self.telegram.answer_callback(callback_id, "Сохранено")
            self.telegram.edit(
                int(message["message_id"]),
                self.settings_text(),
                self.settings_keyboard(),
            )
        except Exception as exc:
            logger.warning("Telegram settings callback failed: {}", exc)
            self.telegram.answer_callback(callback_id, "Ошибка настройки")

    def run(self) -> None:
        """Run monitoring until SIGINT/SIGTERM."""

        self.initialize()
        if self.telegram.enabled:
            threading.Thread(
                target=self.command_loop,
                name="telegram-commands",
                daemon=True,
            ).start()
        while not STOP.is_set():
            self.last_loop_tick = time.monotonic()
            self.poll_cycle()
            self.update_status()
            try:
                self.maybe_daily_summary()
                self.maybe_database_maintenance()
            except Exception as exc:
                logger.warning("Periodic maintenance failed: {}", exc)
            STOP.wait(self.cfg.poll_interval)

    def maybe_database_maintenance(self) -> None:
        """Run rollup/checkpoint/optimize no more than once per day."""

        now = time.time()
        if now - self.last_db_maintenance < 86400:
            return
        self.storage.rollup_hourly(now - 3600)
        result = self.storage.database_maintenance()
        self.last_db_maintenance = now
        logger.info("SQLite maintenance: {}", result)

    def command_loop(self) -> None:
        """Process commands and persist the Telegram update offset."""

        offset = self.telegram_offset()
        while not STOP.is_set():
            try:
                updates = self.telegram.updates(offset)
                for update in updates:
                    offset = int(update["update_id"]) + 1
                    if update.get("callback_query"):
                        self.handle_callback(update)
                    else:
                        self.handle_update(update)
                    self.remember_telegram_offset(offset)
            except Exception as exc:
                self.telegram_failures += 1
                logger.warning("Telegram command polling failed: {}", exc)
                STOP.wait(5)

    def telegram_offset(self) -> int | None:
        """Restore the last acknowledged Telegram update offset."""

        if not self.cfg.persist_update_offset:
            return None
        stored = self.storage.get_meta("telegram_update_offset")
        return int(stored) if stored else None

    def remember_telegram_offset(self, offset: int) -> None:
        """Persist an acknowledged Telegram update offset."""

        if self.cfg.persist_update_offset:
            self.storage.set_meta("telegram_update_offset", str(offset))

    def message_sender_allowed(self, msg: dict) -> bool:
        """Return whether a Telegram message sender may use commands."""

        if not self.cfg.telegram_allowed_user_ids:
            return True
        sender = msg.get("from") or {}
        sender_id = str(sender.get("id", ""))
        return sender_id in self.cfg.telegram_allowed_user_ids

    def handle_settings_command(self, sender_id: str) -> None:
        """Send settings and show edit controls only to an administrator."""

        keyboard = (
            self.settings_keyboard() if self.is_admin(sender_id) else None
        )
        try:
            self.telegram.send(self.settings_text(), reply_markup=keyboard)
        except Exception as exc:
            logger.warning("Telegram settings response failed: {}", exc)

    def send_command_response(self, command: str) -> None:
        """Send one simple text response for a Telegram command."""

        text = self.command_text(command)
        if not text:
            return
        try:
            self.telegram.send(text)
        except Exception as exc:
            logger.warning("Telegram command response failed: {}", exc)

    def handle_update(self, update: dict) -> None:
        """Handle one authorized Telegram command."""

        msg = update.get("message") or update.get("channel_post")
        if not msg or str(msg["chat"]["id"]) != self.cfg.telegram_chat_id:
            return
        if not self.message_sender_allowed(msg):
            return
        parts = str(msg.get("text", "")).split()
        if not parts:
            return
        command = parts[0].split("@")[0].lower()
        sender_id = str((msg.get("from") or {}).get("id", ""))
        if command in {"/chart", "/export"}:
            self.handle_file_command(command, parts)
        elif command in {"/quality", "/history", "/outages"}:
            self.handle_phase_command(command, parts)
        elif command == "/maintenance":
            self.handle_maintenance(sender_id, parts)
        elif command == "/settings":
            self.handle_settings_command(sender_id)
        else:
            self.send_command_response(command)

    def command_phase_hours(self, parts: list[str]) -> tuple[str, int]:
        """Parse optional phase and hour arguments for export/chart."""

        phase = parts[1].upper() if len(parts) > 1 else "L1"
        hours = int(parts[2]) if len(parts) > 2 else 24
        if phase not in {"L1", "L2", "L3"}:
            raise ValueError("phase must be L1, L2 or L3")
        return phase, max(1, min(hours, 168))

    def handle_phase_command(self, command: str, parts: list[str]) -> None:
        """Handle phase-aware text analytics commands."""

        phase = parts[1].upper() if len(parts) > 1 else "L1"
        if phase not in {"L1", "L2", "L3"}:
            self.telegram.send("Фаза должна быть L1, L2 или L3")
            return
        if not self.phase_configured(phase):
            self.telegram.send(f"{phase}: датчик ещё не настроен")
            return
        if command == "/quality":
            hours = max(1, min(int(parts[2]) if len(parts) > 2 else 24, 168))
            text = self.quality_text(phase, hours)
        elif command == "/history":
            text = self.history_text(phase)
        else:
            text = self.outage_history_text(phase)
        self.telegram.send(text)

    def handle_file_command(self, command: str, parts: list[str]) -> None:
        """Generate and send a chart or CSV export."""

        try:
            phase, hours = self.command_phase_hours(parts)
            target = Path(f"/tmp/shelly-{phase.lower()}-{hours}h")
            if command == "/chart":
                path = target.with_suffix(".png")
                count = chart_png(self.cfg.database_path, phase, hours, path)
                self.telegram.send_photo(
                    path, f"{phase} · {hours}ч · {count} точек"
                )
            else:
                path = target.with_suffix(".csv")
                count = export_csv(
                    self.cfg.database_path,
                    phase,
                    time.time() - hours * 3600,
                    path,
                )
                self.telegram.send_document(
                    path, f"{phase} · {hours}ч · {count} строк"
                )
            path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Telegram file command failed: {}", exc)
            self.telegram.send(f"Ошибка: {type(exc).__name__}")

    def handle_maintenance(self, sender_id: str, parts: list[str]) -> None:
        """Change maintenance mode for an authorized admin."""

        if not self.is_admin(sender_id):
            self.telegram.send(
                "Только администратор может менять maintenance mode."
            )
            return
        try:
            minutes = (
                0
                if len(parts) < 2 or parts[1].lower() == "off"
                else int(parts[1])
            )
            value = self.set_maintenance(max(0, min(minutes, 1440)))
            self.storage.audit_setting(sender_id, "maintenance", None, value)
            self.telegram.send(f"🛠 Maintenance: {value}")
        except ValueError:
            self.telegram.send(
                "Использование: /maintenance 60 или /maintenance off"
            )

    def command_text(self, command: str) -> str | None:
        """Render command responses."""

        snap = self.state.snapshot()
        if command in {"/start", "/help"}:
            return (
                "Команды: /voltage /status /today /minmax "
                "/history /outages /health /settings /chart /export "
                "/quality /summary /maintenance /audit /device"
            )
        if command == "/health":
            phases = "; ".join(phase_line(self, p) for p in ("L1", "L2", "L3"))
            return (
                "✅ Сервис работает; "
                + phases
                + "\n"
                + self.backup_health_text()
            )
        if command == "/quality":
            return self.quality_text()
        if command == "/summary":
            return self.daily_summary_text()
        if command == "/audit":
            return self.audit_text()
        if command == "/device":
            return self.device_health_text()
        if snap["voltage"] is None:
            return "Нет актуальных данных измерений."
        if command == "/voltage":
            return "⚡ Фазы\n" + "\n".join(
                phase_line(self, phase) for phase in ("L1", "L2", "L3")
            )
        if command in {"/status", "/today", "/minmax"}:
            return multiphase_status_text(self)
        if command == "/history":
            return self.history_text()
        if command == "/outages":
            return self.outage_history_text()
        return None

    def history_text(self, phase: str = "L1") -> str:
        """Render recent measurements for one phase."""

        lines = [f"🗂 Последние измерения {phase}"]
        for ts, voltage, frequency in self.storage.recent(12, phase):
            stamp = format_time(ts, self.cfg).split()[1]
            lines.append(f"{stamp} — {voltage:.1f} V / {frequency:.2f} Hz")
        return "\n".join(lines)


OUTAGE_COUNT_HELP = (
    "# HELP shelly_outages_total "
    "Confirmed connectivity outages after hysteresis."
)
OUTAGE_DURATION_HELP = (
    "# HELP shelly_outage_duration_today_seconds "
    "Confirmed outage duration after hysteresis; "
    "not exact mains-loss boundaries."
)


def phase_metrics(monitor: Monitor, phase: str) -> list[str]:
    """Render Prometheus metrics for one configured phase."""

    if not monitor.phase_configured(phase):
        return [f'shelly_phase_configured{{phase="{phase}"}} 0']
    snap = monitor.phase_states[phase].snapshot()
    voltage = "NaN" if snap["voltage"] is None else str(snap["voltage"])
    frequency = "NaN" if snap["frequency"] is None else str(snap["frequency"])
    last = snap["last_success"]
    age = -1 if last is None else max(0.0, time.time() - float(last))
    connectivity = snap["connectivity"]
    state_labels = f'phase="{phase}",state="{connectivity}"'
    return [
        f'shelly_phase_configured{{phase="{phase}"}} 1',
        f'shelly_voltage_volts{{phase="{phase}"}} {voltage}',
        f'shelly_frequency_hz{{phase="{phase}"}} {frequency}',
        f'shelly_last_success_age_seconds{{phase="{phase}"}} {age:.3f}',
        f'shelly_outages_total{{phase="{phase}"}} {snap["outage_count"]}',
        f"shelly_connectivity_state{{{state_labels}}} 1",
    ]


def metrics_text(monitor: Monitor) -> str:
    """Render legacy-compatible and three-phase Prometheus metrics."""
    snap = monitor.state.snapshot()
    now = time.time()
    last = snap["last_success"]
    age = -1 if last is None else max(0.0, now - float(last))
    voltage = "NaN" if snap["voltage"] is None else str(snap["voltage"])
    frequency = "NaN" if snap["frequency"] is None else str(snap["frequency"])
    outage_stats = monitor.storage.outage_stats(now, "L1")
    maintenance = int(monitor.maintenance_active())
    loop_age = time.monotonic() - monitor.last_loop_tick
    outage_help = OUTAGE_COUNT_HELP
    duration_help = OUTAGE_DURATION_HELP
    lines = [
        f"shelly_voltage_volts {voltage}",
        f"shelly_frequency_hz {frequency}",
        f"shelly_last_success_age_seconds {age:.3f}",
        outage_help,
        f"shelly_outages_total {snap['outage_count']}",
        "# HELP shelly_outages_today Confirmed connectivity outages today.",
        f"shelly_outages_today {outage_stats['count']}",
        duration_help,
        f"shelly_outage_duration_today_seconds {outage_stats['duration']:.3f}",
        f"shelly_telegram_enabled {int(monitor.telegram.enabled)}",
        f"shelly_telegram_failures_total {monitor.telegram_failures}",
        f"shelly_maintenance_active {maintenance}",
        f"shelly_monitor_loop_age_seconds {loop_age:.3f}",
    ]
    for state in ("starting", "online", "recovering", "offline"):
        active = 1 if snap["connectivity"] == state else 0
        lines.append(f'shelly_connectivity{{state="{state}"}} {active}')
    for phase in ("L1", "L2", "L3"):
        lines.extend(phase_metrics(monitor, phase))
    return "\n".join(lines) + "\n"


def service_snapshot(monitor: Monitor) -> dict[str, object]:
    """Return liveness plus phase/watchdog state for HTTP diagnostics."""

    payload = monitor.state.snapshot()
    payload["phases"] = {
        p: (
            monitor.phase_states[p].snapshot()
            if monitor.phase_configured(p)
            else {"connectivity": "not_configured"}
        )
        for p in ("L1", "L2", "L3")
    }
    payload["watchdog"] = {
        "loop_age": time.monotonic() - monitor.last_loop_tick,
        "telegram_failures": monitor.telegram_failures,
    }
    payload["maintenance"] = monitor.maintenance_active()
    return payload


def dashboard_rows(monitor: Monitor) -> list[dict[str, object]]:
    """Return three phase cards for the local dashboard."""

    rows = []
    for phase in ("L1", "L2", "L3"):
        if not monitor.phase_configured(phase):
            rows.append(
                {"phase": phase, "state": "not configured", "voltage": None}
            )
            continue
        snap = monitor.phase_states[phase].snapshot()
        rows.append(
            {
                "phase": phase,
                "state": snap["connectivity"],
                "voltage": snap["voltage"],
            }
        )
    return rows


class HealthHandler(BaseHTTPRequestHandler):
    """Serve local read-only monitoring HTTP endpoints."""

    monitor: Monitor

    def do_GET(self) -> None:  # noqa: N802
        """Route one monitoring HTTP request."""

        if self.path == "/metrics":
            self.reply(200, metrics_text(self.monitor), "text/plain")
        elif self.path == "/dashboard":
            page = dashboard_html(
                "Shelly Voltage · 3 phases", dashboard_rows(self.monitor)
            )
            self.reply(200, page, "text/html; charset=utf-8")
        elif self.path in {"/", "/health", "/status.json"}:
            self.reply_json(200, service_snapshot(self.monitor))
        elif self.path == "/ready":
            self.reply_json(*readiness(self.monitor))
        else:
            self.send_error(404)

    def reply_json(self, status: int, payload: dict) -> None:
        """Send a JSON response with the requested HTTP status."""

        self.reply(status, json.dumps(payload), "application/json")

    def reply(self, status: int, text: str, content_type: str) -> None:
        """Send one HTTP response with explicit content metadata."""

        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """Disable the default HTTP server access log."""

        return


def health_handler(monitor: Monitor):
    """Build an HTTP handler class bound to one monitor instance."""

    class BoundHealthHandler(HealthHandler):
        """Bind the shared HTTP handler to one monitor instance."""

    BoundHealthHandler.monitor = monitor
    return BoundHealthHandler


def readiness(monitor: Monitor) -> tuple[int, dict]:
    """Return data readiness without conflating it with service liveness."""

    snap = monitor.state.snapshot()
    last = snap["last_success"]
    if snap["connectivity"] == "offline":
        payload = {
            "ready": False,
            "reason": "shelly offline",
            "connectivity": "offline",
        }
        if last is not None:
            payload["age"] = max(0.0, time.time() - float(last))
        return 503, payload
    if last is None:
        return 503, {
            "ready": False,
            "reason": "no successful sample yet",
            "connectivity": snap["connectivity"],
        }
    age = max(0.0, time.time() - float(last))
    if age > monitor.cfg.stale_after:
        return 503, {
            "ready": False,
            "reason": "sample is stale",
            "age": age,
            "connectivity": snap["connectivity"],
        }
    return 200, {
        "ready": True,
        "age": age,
        "connectivity": snap["connectivity"],
    }


def start_health_server(monitor: Monitor) -> ThreadingHTTPServer:
    """Start health/metrics server in a daemon thread."""

    server = ThreadingHTTPServer(
        (monitor.cfg.health_host, monitor.cfg.health_port),
        health_handler(monitor),
    )
    threading.Thread(
        target=server.serve_forever,
        name="health-server",
        daemon=True,
    ).start()
    return server


def install_signals() -> None:
    """Install graceful shutdown handlers."""

    def stop(_signum, _frame) -> None:
        """Ask the main loop to stop after a termination signal."""
        STOP.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)


def read_hardware_snapshot(cfg: Config) -> dict[str, object]:
    """Read one snapshot from the configured meter type."""

    if cfg.shelly_meter_type == "pro3em":
        readings = Pro3EMClient(cfg).read_phases()
        if len(readings) != 3:
            raise RuntimeError("Pro3EM triphase response is incomplete")
        return {
            "meter_type": "pro3em",
            "phases": {
                phase: {"voltage": row[0], "frequency": row[1]}
                for phase, row in readings.items()
            },
            "rpc_ms": round(max(row[2] for row in readings.values()), 1),
        }
    if cfg.shelly_meter_type == "pro_em50":
        client = ShellyClient(cfg)
    else:
        client = RpcMeterClient(cfg, METER_RPC[cfg.shelly_meter_type])
    voltage, frequency, rpc_ms = client.read()
    return {
        "meter_type": cfg.shelly_meter_type,
        "voltage": voltage,
        "frequency": frequency,
        "rpc_ms": round(rpc_ms, 1),
    }


def probe(cfg: Config) -> int:
    """Read one hardware snapshot without Telegram."""

    payload = {"version": VERSION, "host": cfg.shelly_host}
    payload.update(read_hardware_snapshot(cfg))
    print(json.dumps(payload, indent=2))
    return 0


def shelly_doctor(cfg: Config) -> dict[str, object]:
    """Check Shelly without aborting the rest of doctor diagnostics."""

    try:
        payload = read_hardware_snapshot(cfg)
    except Exception as exc:
        return {"status": "error", "error_type": exc.__class__.__name__}
    payload["status"] = "ok"
    return payload


def storage_doctor(cfg: Config) -> dict[str, object]:
    """Check SQLite without hiding a Shelly or Telegram result."""

    try:
        storage = Storage(cfg.database_path, cfg.timezone)
        storage.initialize()
        return {
            "status": "ok",
            "quick_check": storage.integrity(),
            "schema_version": storage.get_meta("schema_version"),
        }
    except Exception as exc:
        return {"status": "error", "error_type": exc.__class__.__name__}


def safe_telegram_doctor(telegram: Telegram) -> dict[str, object]:
    """Check Telegram without exposing transport details or the token."""

    try:
        return telegram_doctor(telegram)
    except Exception as exc:
        return {"status": "error", "error_type": exc.__class__.__name__}


def doctor(cfg: Config) -> int:
    """Run all diagnostics and emit one structured JSON result."""

    result: dict[str, object] = {
        "version": VERSION,
        "shelly": shelly_doctor(cfg),
        "sqlite": storage_doctor(cfg),
        "telegram": safe_telegram_doctor(Telegram(cfg)),
    }
    statuses = [
        str(result[name].get("status"))
        for name in ("shelly", "sqlite", "telegram")
        if isinstance(result[name], dict)
    ]
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if all(status in {"ok", "disabled"} for status in statuses) else 2


def telegram_doctor(telegram: Telegram) -> dict[str, object]:
    """Validate Telegram without posting a message."""

    if not telegram.enabled:
        return {"status": "disabled"}
    bot = telegram.get_me()
    control_chat = telegram.get_chat()
    status_chat = telegram.get_chat(telegram.status_chat_id)
    return {
        "status": "ok",
        "bot": bot.get("username"),
        "control_chat_id": str(control_chat.get("id")),
        "status_chat_id": str(status_chat.get("id")),
        "status_chat_type": status_chat.get("type"),
    }


def telegram_chat_rows(updates: list[dict]) -> list[dict[str, object]]:
    """Return unique chat summaries from Telegram updates."""

    chats: dict[str, dict[str, object]] = {}
    for update in updates:
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            continue
        chats[chat_id] = {
            "chat_id": chat_id,
            "type": chat.get("type"),
            "name": (
                chat.get("title")
                or chat.get("username")
                or chat.get("first_name")
            ),
            "text": msg.get("text"),
        }
    return list(chats.values())


def telegram_discover(cfg: Config) -> int:
    """List pending Telegram chats without exposing the bot token."""

    if not cfg.telegram_token:
        print(json.dumps({"status": "token_missing"}, ensure_ascii=False))
        return 2
    telegram = Telegram(cfg)
    try:
        bot = telegram.get_me()
        updates = telegram.call(
            "getUpdates",
            {
                "timeout": 0,
                "limit": 20,
                "allowed_updates": [
                    "message",
                    "channel_post",
                    "callback_query",
                ],
            },
        )
    except Exception as exc:
        payload = {"status": "error", "error_type": exc.__class__.__name__}
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    chats = telegram_chat_rows(updates)
    payload = {
        "status": "ok" if chats else "waiting_for_update",
        "bot": bot.get("username"),
        "chats": chats,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if chats else 2


def parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=[
            "run",
            "probe",
            "doctor",
            "db-check",
            "version",
            "telegram-discover",
        ],
    )
    return cli


def main() -> int:
    """CLI entrypoint."""

    args = parser().parse_args()
    if args.command == "version":
        print(VERSION)
        return 0
    if args.command == "db-check":
        path = Path(os.getenv("DATABASE_PATH", "/data/voltage.sqlite3"))
        return db_check(path)
    cfg = load_config()
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        level=os.getenv("LOG_LEVEL", "INFO"),
    )
    if args.command == "probe":
        return probe(cfg)
    if args.command == "doctor":
        return doctor(cfg)
    if args.command == "telegram-discover":
        return telegram_discover(cfg)
    install_signals()
    Monitor(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
