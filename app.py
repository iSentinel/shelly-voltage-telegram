#!/usr/bin/env python3
"""Shelly Pro EM-50 voltage monitor with optional Telegram integration."""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sqlite3
import sys
import threading
import time
from contextlib import closing, suppress
from dataclasses import dataclass, replace
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
    weekly_connection_stats,
    weekly_quality_stats,
)
from apartment_phases import ApartmentRegistry, apartment_command
from dtek_schedule import format_schedule
from notification_features import (
    abnormal_seconds,
    instability_count,
    load_history,
    load_json_object,
    new_episode,
    update_episode,
    update_history,
)
from telegram_i18n import LANGUAGES, command_descriptions, duration_text, tr
from update_info import release_notice


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
RECOVERY_STATUS_STABLE_SECONDS = 15 * 60
RECOVERY_STATUS_STABLE_KEY = "voltage_recovery_stable_since"
RECOVERY_ROTATION_PENDING_KEY = "telegram_recovery_rotation_pending_at"
STOP = threading.Event()

MENU_ALLOWED_ACTIONS = (
    "status",
    "settings",
    "today",
    "outages",
    "chart",
    "help",
    "apartments",
    "updates",
    "events",
    "summary",
)


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
    telegram_language: str
    telegram_quiet_start_hour: int
    telegram_quiet_end_hour: int
    voltage_notification_cooldown_min: int
    voltage_status_edit_window_min: int
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
    voltage_instability_enabled: bool
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
        "telegram_language": os.getenv("TELEGRAM_LANGUAGE", "ru")
        .strip()
        .lower(),
        "telegram_quiet_start_hour": int(
            os.getenv("TELEGRAM_QUIET_START_HOUR", "23")
        ),
        "telegram_quiet_end_hour": int(
            os.getenv("TELEGRAM_QUIET_END_HOUR", "7")
        ),
        "voltage_notification_cooldown_min": int(
            os.getenv("VOLTAGE_NOTIFICATION_COOLDOWN_MIN", "5")
        ),
        "voltage_status_edit_window_min": int(
            os.getenv("VOLTAGE_STATUS_EDIT_WINDOW_MIN", "10")
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
        "voltage_instability_enabled": env_bool(
            "VOLTAGE_INSTABILITY_ENABLED", True
        ),
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
        "telegram_pin_status": env_bool("TELEGRAM_PIN_STATUS", False),
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
    if cfg.telegram_language not in LANGUAGES:
        raise ValueError("TELEGRAM_LANGUAGE must be ru, uk, or en")
    if cfg.telegram_min_edit_interval < 1:
        raise ValueError("TELEGRAM_MIN_EDIT_INTERVAL_SEC must be >= 1")
    hours = (cfg.telegram_quiet_start_hour, cfg.telegram_quiet_end_hour)
    if any(hour < 0 or hour > 23 for hour in hours):
        raise ValueError("Telegram quiet hours must be between 0 and 23")
    if not 0 <= cfg.voltage_notification_cooldown_min <= 60:
        raise ValueError(
            "VOLTAGE_NOTIFICATION_COOLDOWN_MIN must be between 0 and 60"
        )
    if cfg.voltage_status_edit_window_min not in {3, 5, 10}:
        raise ValueError("VOLTAGE_STATUS_EDIT_WINDOW_MIN must be 3, 5, or 10")
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

    def range_stats(
        self, start: float, end: float, phase: str = "L1"
    ) -> dict[str, float | int | None]:
        """Return voltage statistics for one exact time interval."""

        with closing(self.connect()) as db, db:
            row = db.execute(
                "SELECT COUNT(*),MIN(voltage),MAX(voltage),AVG(voltage) "
                "FROM measurements WHERE ts>=? AND ts<=? AND phase=?",
                (start, end, phase),
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
                "WHERE started_at <= ? AND COALESCE(ended_at, ?) > ? "
                "AND phase=?",
                (now, now, start.timestamp(), phase),
            ).fetchall()
        count = len(rows)
        duration = sum(
            max(
                0.0,
                min(
                    float(row[1]) if row[1] is not None else now,
                    now,
                    end.timestamp(),
                )
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
        faults = em_status_errors(payload)
        if faults:
            raise RuntimeError(
                "EM1.GetStatus meter errors: " + ", ".join(faults)
            )
        rpc_ms = (time.perf_counter() - started) * 1000
        voltage, frequency = validated_meter_numbers(
            payload["voltage"], payload["freq"]
        )
        return voltage, frequency, rpc_ms


def validated_meter_numbers(
    voltage: object, frequency: object
) -> tuple[float, float]:
    """Reject invalid RPC numbers without echoing untrusted data."""

    if isinstance(voltage, bool) or isinstance(frequency, bool):
        raise RuntimeError("invalid Shelly numeric measurement")
    try:
        values = (float(voltage), float(frequency))
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError("invalid Shelly numeric measurement") from None
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("non-finite Shelly measurement")
    if any(value < 0 for value in values):
        raise RuntimeError("negative Shelly measurement")
    return values


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
        faults = em_status_errors(payload)
        if faults:
            raise RuntimeError(
                f"{self.rpc_method} meter errors: " + ", ".join(faults)
            )
        rpc_ms = (time.perf_counter() - started) * 1000
        voltage = payload.get("voltage")
        frequency = payload.get("freq")
        if voltage is None or frequency is None:
            raise RuntimeError(
                f"{self.rpc_method} returned no voltage/frequency"
            )
        valid_voltage, valid_frequency = validated_meter_numbers(
            voltage, frequency
        )
        return valid_voltage, valid_frequency, rpc_ms


EM_ERROR_FIELDS = (
    "errors",
    "a_errors",
    "b_errors",
    "c_errors",
    "n_errors",
    "total_errors",
)
KNOWN_EM_ERRORS = frozenset(
    {
        "power_meter_failure",
        "phase_sequence",
        "ct_type_not_set",
        "out_of_range:active_power",
        "out_of_range:apparent_power",
        "out_of_range:voltage",
        "out_of_range:current",
    }
)


def em_status_errors(payload: dict) -> list[str]:
    """Describe reported meter faults without exposing arbitrary RPC data."""

    faults = []
    for field in EM_ERROR_FIELDS:
        if field not in payload:
            continue
        values = payload[field]
        if not isinstance(values, list):
            faults.append(f"{field}:invalid_format")
            continue
        for value in values[:8]:
            code = value if isinstance(value, str) else ""
            safe_code = code if code in KNOWN_EM_ERRORS else "unknown_error"
            faults.append(f"{field}:{safe_code}")
        if len(values) > 8:
            faults.append(f"{field}:additional_errors")
    return faults


class Pro3EMClient(ShellyClient):
    """Read all three phases from Shelly Pro 3EM in triphase profile."""

    def read_phases(self) -> dict[str, tuple[float, float, float]]:
        """Read phase A/B/C using one EM.GetStatus request."""

        started = time.perf_counter()
        payload = self.rpc("EM.GetStatus", {"id": 0})
        if not isinstance(payload, dict):
            raise RuntimeError("EM.GetStatus returned invalid status")
        faults = em_status_errors(payload)
        if faults:
            raise RuntimeError(
                "EM.GetStatus meter errors: " + ", ".join(faults)
            )
        rpc_ms = (time.perf_counter() - started) * 1000
        result = {}
        for phase, prefix in (("L1", "a"), ("L2", "b"), ("L3", "c")):
            voltage = payload.get(f"{prefix}_voltage")
            frequency = payload.get(f"{prefix}_freq")
            if voltage is None or frequency is None:
                continue
            valid_voltage, valid_frequency = validated_meter_numbers(
                voltage, frequency
            )
            result[phase] = (valid_voltage, valid_frequency, rpc_ms)
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


def safe_telegram_api_error(payload: object) -> str:
    """Redact untrusted Telegram details but preserve known status markers."""

    if not isinstance(payload, dict):
        return "Telegram API invalid response"
    code = payload.get("error_code")
    prefix = (
        f"Telegram API error {code}"
        if type(code) is int and 400 <= code <= 599
        else "Telegram API error"
    )
    raw = payload.get("description")
    if not isinstance(raw, str):
        return prefix
    text = raw.strip().lower()
    if text.startswith("bad request: "):
        text = text[len("bad request: ") :]
    markers = (
        "message is not modified",
        "message to edit not found",
        "message can't be edited",
        "message_id_invalid",
        "message to unpin not found",
    )
    for marker in markers:
        if text == marker or text.startswith(marker + ":"):
            return f"{prefix}: {marker}"
    return prefix


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
            data = response.json()
        except ValueError:
            raise RuntimeError("Telegram returned invalid JSON") from None
        if not isinstance(data, dict):
            raise RuntimeError("Telegram returned invalid JSON object")
        return data

    @staticmethod
    def rate_limit_delay(data: dict) -> float:
        """Bound a malformed Telegram 429 retry value without exceptions."""

        params = data.get("parameters")
        retry = params.get("retry_after", 1) if isinstance(params, dict) else 1
        if isinstance(retry, bool):
            return 1.0
        try:
            seconds = float(retry)
        except (TypeError, ValueError, OverflowError):
            return 1.0
        if not math.isfinite(seconds) or seconds < 0:
            return 1.0
        return min(seconds, 30.0)

    def _call_with_retry(self, method: str, payload: dict) -> dict:
        """Retry only a Telegram 429 response, once, with a safe delay."""

        data = self._post(method, payload)
        if data.get("error_code") == 429:
            delay = self.rate_limit_delay(data)
            logger.warning("Telegram rate limited; retry in {:.1f}s", delay)
            time.sleep(delay)
            data = self._post(method, payload)
        if not data.get("ok"):
            raise RuntimeError(safe_telegram_api_error(data))
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

    def send_test(self, text: str, notify: bool) -> int:
        """Send an explicit admin test without applying quiet hours."""

        result = self.call(
            "sendMessage",
            {
                "chat_id": self.cfg.telegram_chat_id,
                "text": text,
                "disable_notification": not notify,
            },
        )
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
        try:
            self.call("editMessageText", payload)
        except RuntimeError as exc:
            if not message_not_modified(exc):
                raise

    def clear_reply_markup(
        self, message_id: int, chat_id: str | None = None
    ) -> None:
        """Remove inline controls from one Telegram message."""

        try:
            self.call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id or self.cfg.telegram_chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except RuntimeError as exc:
            if not message_not_modified(exc):
                raise

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
        if not isinstance(payload, dict):
            raise RuntimeError("Telegram returned invalid JSON object")
        if not payload.get("ok"):
            raise RuntimeError(safe_telegram_api_error(payload))
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

    def unpin(self, message_id: int, chat_id: str | None = None) -> None:
        """Unpin one status message before it is removed."""

        self.call(
            "unpinChatMessage",
            {
                "chat_id": chat_id or self.status_chat_id,
                "message_id": message_id,
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

    def set_commands(self, language: str = "ru") -> None:
        """Register localized supported bot commands."""

        commands = [
            {"command": name, "description": description}
            for name, description in command_descriptions(language).items()
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


def private_weekly_due_key(local_time: datetime) -> str | None:
    """Return the ISO week only on Sunday at or after 20:00 local."""

    if local_time.weekday() != 6 or (local_time.hour, local_time.minute) < (
        20,
        0,
    ):
        return None
    week = local_time.isocalendar()
    return f"{week.year}-W{week.week:02d}"


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


def offline_status_lines(
    snap: dict[str, object], ts: float, cfg: Config
) -> list[str]:
    """Render outage timing without presenting stale electrical values."""

    lines = []
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
    if connectivity == "offline":
        return "\n".join(lines + offline_status_lines(snap, ts, cfg))
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


def localized_clock_skew(language: str, seconds: object) -> str:
    """Render a device time difference in the selected language."""

    if seconds is None:
        return tr(language, "device_unknown")
    try:
        value = float(seconds)
    except (TypeError, ValueError, OverflowError):
        return tr(language, "device_unknown")
    return f"{value:+.1f}{tr(language, 'unit_seconds')}"


def phase_line(monitor: Monitor, phase: str) -> str:
    """Render one localized phase line."""

    lang = monitor.language
    if not monitor.phase_configured(phase):
        return tr(lang, "phase_unconfigured", phase=phase)
    snap = monitor.phase_states[phase].snapshot()
    phase = monitor.phase_label(phase)
    connectivity = str(snap["connectivity"])
    if connectivity == "offline":
        return tr(lang, "phase_offline", phase=phase)
    if snap["voltage"] is None:
        return f"🟡 {phase}: {tr(lang, 'phase_no_data')}"
    voltage = float(snap["voltage"])
    if connectivity == "recovering":
        return tr(lang, "phase_recovering", phase=phase, voltage=voltage)
    if connectivity == "starting":
        return tr(
            lang,
            "phase_cached",
            phase=phase,
            voltage=voltage,
            unit=tr(lang, "unit_voltage"),
        )
    warning = voltage <= monitor.warning_low or voltage >= monitor.warning_high
    critical = (
        voltage <= monitor.critical_low or voltage >= monitor.critical_high
    )
    icon = "🔴" if critical else "🟡" if warning else "🟢"
    return f"{icon} {phase}: {voltage:.1f} {tr(lang, 'unit_voltage')}"


def voltage_event_title(monitor: Monitor, kind: str, voltage: float) -> str:
    """Describe the measured level instead of assuming full recovery."""

    if kind != "recovered":
        return tr(monitor.language, f"event_{kind}")
    if not math.isfinite(voltage):
        key = "event_recovered_unknown"
    elif voltage <= monitor.critical_low:
        key = "event_recovered_critical_low"
    elif voltage >= monitor.critical_high:
        key = "event_recovered_critical_high"
    elif voltage <= monitor.warning_low:
        key = "event_recovered_warning_low"
    elif voltage >= monitor.warning_high:
        key = "event_recovered_warning_high"
    else:
        key = "event_recovered"
    return tr(monitor.language, key)


def voltage_event_lines(monitor: Monitor) -> list[str]:
    """Render the latest localized voltage transition."""

    event = monitor.last_voltage_event
    if event is None or not monitor.phase_configured(event[4]):
        return []
    kind, voltage, frequency, happened_at, phase = event
    last_success = monitor.state.snapshot().get("last_success")
    if (
        kind == "recovered"
        and last_success is not None
        and float(last_success) - happened_at >= 1800
    ):
        return []
    title = voltage_event_title(monitor, kind, voltage)
    if phase != "L1":
        icon, _, rest = title.partition(" ")
        title = f"{icon} {phase}: {rest}"
    zone = ZoneInfo(monitor.cfg.timezone)
    when = datetime.fromtimestamp(happened_at, zone).strftime("%H:%M")
    unit_v = tr(monitor.language, "unit_voltage")
    unit_f = tr(monitor.language, "unit_frequency")
    return [
        f"{title} · {when}",
        f"{voltage:.1f} {unit_v} · {frequency:.2f} {unit_f}",
        "",
    ]


def voltage_episode_summary_lines(monitor: Monitor) -> list[str]:
    """Render a finalized voltage episode summary, if one exists."""

    data = monitor.voltage_episode_summary()
    if not data:
        return []
    lang = monitor.language
    lines = [
        "",
        tr(lang, "episode_title"),
        tr(lang, "episode_period", **data),
        tr(lang, "episode_stats", **data),
        tr(lang, "episode_transitions", count=data["transitions"]),
        tr(lang, "episode_abnormal", duration=data["abnormal"]),
    ]
    if data["starts_on_recovery"]:
        lines.append(tr(lang, "episode_recovery_scope"))
    return lines


def instability_lines(monitor: Monitor) -> list[str]:
    """Render a compact instability indicator when its threshold is reached."""

    if not monitor.instability_enabled:
        return []
    snap = monitor.state.snapshot()
    sample_at = float(snap.get("last_success") or 0)
    event_at = (
        float(monitor.last_voltage_event[3])
        if monitor.last_voltage_event
        else 0
    )
    now = max(sample_at, event_at) or time.time()
    count = monitor.instability_transition_count(now)
    if count < 4:
        return []
    return ["", tr(monitor.language, "unstable", count=count)]


def multiphase_status_text(
    monitor: Monitor, include_event_context: bool = True
) -> str:
    """Render a coherent status for the selected voltage phases."""

    with monitor.config_lock:
        return render_voltage_status(monitor, include_event_context)


def recovery_banner_lines(monitor: Monitor) -> list[str]:
    """Show one confirmed outage restoration only while it is recent."""

    recovered_at = monitor.last_recovery_at
    duration = monitor.last_recovery_duration
    if duration is None or recovered_at is None:
        return []
    if not 0 <= time.time() - recovered_at < 30 * 60:
        return []
    return [
        tr(
            monitor.language,
            "power_recovered",
            duration=duration_text(duration, monitor.language),
        )
    ]


def compact_preview_text(monitor: Monitor) -> str:
    """Show fresh measurements or explicit unknown status privately."""

    with monitor.config_lock:
        lang = monitor.language
        snap = monitor.state.snapshot()
        connection = str(snap["connectivity"])
        if connection not in {"online", "offline", "starting", "recovering"}:
            connection = "starting"
        lines = [
            tr(lang, "compact_preview_title", phases=monitor.phase_labels()),
            "",
            tr(lang, "status_" + connection),
        ]
        lines.extend(phase_line(monitor, p) for p in monitor.active_phases())
        frequency = snap.get("frequency")
        if connection == "online" and frequency is not None:
            lines.append(tr(lang, "status_frequency", value=float(frequency)))
        stamp = snap.get("last_success")
        if stamp is not None:
            lines.append(
                tr(
                    lang,
                    "last_l1",
                    phase=monitor.phase_label("L1"),
                    stamp=format_time(float(stamp), monitor.cfg),
                )
            )
        else:
            lines.append(
                tr(lang, "no_measurements", phase=monitor.phase_label("L1"))
            )
        lines.append(tr(lang, "compact_preview_note"))
        return "\n".join(lines)


def render_voltage_status(
    monitor: Monitor, include_event_context: bool
) -> str:
    """Render voltage and primary history under the configuration lock."""

    lang = monitor.language
    lines = voltage_event_lines(monitor) if include_event_context else []
    lines.extend(
        [tr(lang, "network_title", phases=monitor.phase_labels()), ""]
    )
    l1 = monitor.state.snapshot()
    connectivity = str(l1["connectivity"])
    status_key = "status_" + connectivity
    if connectivity not in {"online", "recovering", "offline", "starting"}:
        status_key = "status_starting"
    lines.append(tr(lang, status_key))
    lines.extend(phase_line(monitor, p) for p in monitor.active_phases())
    frequency = l1.get("frequency")
    if connectivity == "online" and frequency is not None:
        lines.append(tr(lang, "status_frequency", value=float(frequency)))
    if l1["voltage"] is None:
        notice = tr(lang, "no_measurements", phase=monitor.phase_label("L1"))
        return "\n".join(lines + ["", notice])
    ts = float(l1["last_success"] or time.time())
    lines.extend(primary_day_lines(monitor, ts))
    lines.extend(recovery_banner_lines(monitor))
    lines.extend(instability_lines(monitor))
    if include_event_context:
        lines.extend(voltage_episode_summary_lines(monitor))
    lines.append(
        tr(
            lang,
            "last_l1",
            phase=monitor.phase_label("L1"),
            stamp=format_time(ts, monitor.cfg),
        )
    )
    return "\n".join(lines)


def primary_day_lines(monitor: Monitor, ts: float) -> list[str]:
    """Render primary history using its stable L1 database identifier."""

    stats = monitor.storage.day_stats(ts, "L1")
    return [
        "",
        tr(
            monitor.language,
            "l1_today",
            phase=monitor.phase_label("L1"),
            minimum=fmt(stats["min"]),
            maximum=fmt(stats["max"]),
            average=fmt(stats["avg"]),
        ),
    ]


def report_period_line(
    language: str, key: str, since: float, now: float, timezone: str
) -> str:
    """Label the actual local observation interval without extrapolating."""

    zone = ZoneInfo(timezone)
    start = datetime.fromtimestamp(since, zone).strftime("%d.%m.%Y %H:%M")
    end = datetime.fromtimestamp(now, zone).strftime("%d.%m.%Y %H:%M")
    return tr(language, key, start=start, end=end, zone=timezone)


def daily_phase_report_line(
    monitor: Monitor, phase: str, since: float, now: float
) -> tuple[str, bool]:
    """Format only actual local-day phase samples and connection losses."""

    data = phase_stats(monitor.cfg.database_path, phase, since, until=now)
    outages = monitor.storage.outage_stats(now, phase)
    line = tr(
        monitor.language,
        "summary_phase",
        phase=monitor.phase_label(phase),
        minimum=fmt(data["min"]),
        maximum=fmt(data["max"]),
        average=fmt(data["avg"]),
        samples=data["count"],
        count=outages["count"],
        duration=duration_text(float(outages["duration"]), monitor.language),
    )
    return line, bool(outages["count"])


def weekly_measurement_line(
    monitor: Monitor, phase: str, data: dict[str, float | int | None]
) -> str:
    """Format actual voltage samples without claiming unknown periods."""

    return tr(
        monitor.language,
        "weekly_phase",
        phase=monitor.phase_label(phase),
        minimum=fmt(data["min"]),
        maximum=fmt(data["max"]),
        average=fmt(data["avg"]),
        count=data["count"],
    )


def weekly_phase_lines(
    monitor: Monitor, phase: str, since: float, now: float
) -> list[str]:
    """Render seven-day quality with measured and unknown time separate."""

    data = weekly_quality_stats(
        monitor.cfg.database_path,
        phase,
        since,
        now,
        monitor.warning_low,
        monitor.warning_high,
    )
    outages = weekly_connection_stats(
        monitor.cfg.database_path, phase, since, now
    )
    lang = monitor.language
    return [
        weekly_measurement_line(monitor, phase, data),
        tr(
            lang,
            "weekly_coverage",
            observed=duration_text(data["observed"], lang),
            unknown=duration_text(data["unknown"], lang),
        ),
        tr(
            lang,
            "weekly_threshold",
            below=duration_text(data["below"], lang),
            above=duration_text(data["above"], lang),
        ),
        tr(
            lang,
            "weekly_connection",
            count=outages["count"],
            duration=duration_text(outages["duration"], lang),
        ),
    ]


def voltage_timeline_events(
    monitor: Monitor, since: float, now: float
) -> list[tuple[float, str]]:
    """Read retained state changes without inventing missing history."""

    raw = monitor.storage.get_meta("voltage_transition_history")
    try:
        events = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(events, list):
        return []
    rows: list[tuple[float, str]] = []
    for item in events[-48:]:
        if not isinstance(item, dict):
            continue
        state, phase = item.get("state"), item.get("phase")
        if state not in {"low", "high", "normal"}:
            continue
        if phase not in monitor.active_phases():
            continue
        try:
            stamp = float(item["at"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not since <= stamp <= now:
            continue
        stamp_text = format_stamp(stamp, monitor.cfg.timezone)
        label = tr(monitor.language, "events_state_" + state)
        message = tr(
            monitor.language,
            "events_warning",
            phase=monitor.phase_label(phase),
            state=label,
            time=stamp_text,
        )
        rows.append((stamp, message))
    return rows


def connectivity_timeline_events(
    monitor: Monitor, since: float, now: float
) -> list[tuple[float, str]]:
    """Read confirmed Shelly connectivity loss and recovery records."""

    rows: list[tuple[float, str]] = []
    for phase in monitor.active_phases():
        try:
            outages = outage_history(monitor.cfg.database_path, phase, 24)
        except (OSError, sqlite3.Error):
            continue
        for start, end in outages:
            label = monitor.phase_label(phase)
            if since <= start <= now:
                rows.append(
                    (
                        start,
                        tr(
                            monitor.language,
                            "events_connection",
                            phase=label,
                            time=format_stamp(start, monitor.cfg.timezone),
                        ),
                    )
                )
            if end is not None and since <= end <= now:
                rows.append(
                    (
                        end,
                        tr(
                            monitor.language,
                            "events_reconnected",
                            phase=label,
                            time=format_stamp(end, monitor.cfg.timezone),
                        ),
                    )
                )
    return rows


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
        self.config_lock = threading.RLock()
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
        self.language = cfg.telegram_language
        self.instability_enabled = cfg.voltage_instability_enabled
        self.notification_cooldown_min = cfg.voltage_notification_cooldown_min
        self.status_edit_window_min = cfg.voltage_status_edit_window_min
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
        self.warning_dwell = cfg.alert_dwell_sec
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
        self.last_voltage_event: (
            tuple[str, float, float, float, str] | None
        ) = None
        self.next_prune = 0.0
        self.last_summary_day: str | None = None
        self.last_summary_attempt = -300.0
        self.weekly_digest_attempts: dict[str, float] = {}
        self.last_recovery_duration: float | None = None
        self.last_recovery_at: float | None = None
        self.maintenance_until = 0.0
        self.last_loop_tick = time.monotonic()
        self.telegram_failures = 0
        self.telegram_consecutive_failures = 0
        self.telegram_last_poll_success: float | None = None
        self.last_db_maintenance = 0.0
        self.last_db_maintenance_attempt = -300.0
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
        with self.config_lock:
            self.cfg = replace(self.cfg, shelly_meter_type=meter_type)
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
        """Create states for the selected independent voltage phases."""

        if self.meter_type == "pro3em":
            for phase in ("L2", "L3"):
                self.phase_states[phase] = RuntimeState()
                self.phase_clients[phase] = self.shelly

    def active_phases(self) -> tuple[str, ...]:
        """Return stable database identifiers for the selected phases."""

        return ("L1", "L2", "L3") if self.meter_type == "pro3em" else ("L1",)

    def phase_label(self, phase: str) -> str:
        """Display L for one phase while preserving historical L1 records."""

        return "L" if phase == "L1" and self.meter_type != "pro3em" else phase

    def phase_labels(self) -> str:
        """List only the independent voltage phases of the selected model."""

        return ", ".join(self.phase_label(p) for p in self.active_phases())

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
            self.clear_voltage_event(phase)
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
            tr(self.language, "phase_sensor_offline", phase=phase),
            chat_id=self.telegram.status_chat_id,
        )

    def maybe_phase_recovery(self, phase: str, duration: float) -> None:
        """Notify when an optional phase sensor recovers."""

        if not self.availability_alerts_enabled or not self.telegram.enabled:
            return
        self.telegram.send(
            tr(
                self.language,
                "phase_sensor_recovered",
                phase=phase,
                duration=duration_text(duration, self.language),
            ),
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
            self.record_voltage_event("low", voltage, frequency, phase)
        elif voltage >= self.low_voltage_recovery_v:
            self.phase_low_candidates.pop(phase, None)
            if self.phase_low_active[phase]:
                self.phase_low_active[phase] = False
                self.record_voltage_event(
                    "recovered", voltage, frequency, phase
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
        dwell = self.level_dwell(level)
        if now - candidate[1] < dwell:
            return
        self.phase_alert_levels[phase] = level
        self.phase_alert_candidates.pop(phase, None)
        kind = "recovered"
        if level != "normal":
            side = "high" if voltage >= self.warning_high else "low"
            kind = f"{level}_{side}"
        self.record_voltage_event(kind, voltage, frequency, phase)

    def initialize(self) -> None:
        """Initialize storage and start health service."""

        self.storage.initialize()
        self.load_runtime_settings()
        self.last_summary_day = self.storage.get_meta("daily_summary_day")
        self.restore_voltage_event()
        self.restore_recovery_stability_timer()
        self.restore_voltage_status_window()
        self.restore_voltage_episode()
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
                self.telegram.set_commands(self.language)
            except Exception as exc:
                logger.warning("Telegram command registration failed: {}", exc)
            self.sync_status_pin_setting()
            self.clear_status_controls()

    def clear_voltage_event(self, phase: str | None = None) -> None:
        """Remove stale voltage transition state after a confirmed outage."""

        event = self.last_voltage_event
        if phase is not None and event is not None and event[4] != phase:
            return
        self.last_voltage_event = None
        self.storage.delete_meta("last_voltage_event")
        self.storage.delete_meta("voltage_episode")
        self.storage.delete_meta("voltage_status_edit_window_started_at")
        self.storage.delete_meta(RECOVERY_STATUS_STABLE_KEY)
        self.storage.delete_meta(RECOVERY_ROTATION_PENDING_KEY)

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
        """Poll the independent voltage phases of the selected model."""

        with self.config_lock:
            if self.meter_type == "pro3em":
                return self.poll_pro3em_once()
            return self.poll_once()

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

    def status_keyboard(self) -> None:
        """Keep the public status message free of inline buttons."""

        return None

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
            return tr(self.language, "callback_today", value=" · ".join(rows))
        if action == "outages":
            rows = []
            for phase in ("L1", "L2", "L3"):
                if not self.phase_configured(phase):
                    continue
                stats = self.storage.outage_stats(time.time(), phase)
                rows.append(
                    f"{phase} {stats['count']}/"
                    f"{duration_text(float(stats['duration']), self.language)}"
                )
            return tr(
                self.language, "callback_outages", value=" · ".join(rows)
            )
        return tr(self.language, "callback_updated")

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
                self.storage.delete_meta(
                    "voltage_status_edit_window_started_at"
                )
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
            self.storage.delete_meta("voltage_status_edit_window_started_at")
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
        if connectivity == "online" and self.storage.get_meta(
            RECOVERY_ROTATION_PENDING_KEY
        ):
            duration = self.last_recovery_duration
            if duration is None:
                duration = self.storage.latest_closed_outage_duration()
            self.rotate_status_after_recovery(duration or 0.0)
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

    def disable_status_pin(self) -> None:
        """Unpin the current status when pinning is disabled."""

        pinned = self.storage.get_meta("telegram_status_pinned_id")
        if not pinned or not self.cfg.telegram_status_chat_id:
            return
        try:
            self.telegram.unpin(int(pinned), self.telegram.status_chat_id)
        except Exception as exc:
            logger.warning("Telegram status unpin failed: {}", exc)
            return
        self.storage.delete_meta("telegram_status_pinned_id")

    def sync_status_pin_setting(self) -> None:
        """Apply the current pin setting to the existing status message."""

        current = self.storage.get_meta(self.status_message_key())
        if self.pin_status:
            if current:
                self.ensure_status_pinned(int(current))
            return
        self.disable_status_pin()

    def clear_status_controls(self) -> None:
        """Remove legacy inline buttons from the current public status."""

        if not self.cfg.telegram_status_chat_id:
            return
        current = self.storage.get_meta(self.status_message_key())
        if not current:
            return
        try:
            self.telegram.clear_reply_markup(
                int(current), self.telegram.status_chat_id
            )
        except Exception as exc:
            if not message_not_modified(exc):
                logger.warning(
                    "Telegram status control cleanup failed: {}", exc
                )

    def rotate_status_after_recovery(self, duration: float) -> None:
        """Keep the old channel status until its replacement is published."""

        if not self.telegram.enabled:
            return
        pending_at = self.storage.get_meta(RECOVERY_ROTATION_PENDING_KEY)
        self.last_recovery_duration = duration
        self.last_recovery_at = (
            float(pending_at) if pending_at else time.time()
        )
        key = self.status_message_key()
        old_id = self.storage.get_meta(key)
        try:
            new_id = self.telegram.send(
                multiphase_status_text(self),
                reply_markup=self.status_keyboard(),
                chat_id=self.telegram.status_chat_id,
            )
        except Exception as exc:
            logger.warning("Telegram recovery status publish failed: {}", exc)
            self.storage.set_meta(
                RECOVERY_ROTATION_PENDING_KEY, str(self.last_recovery_at)
            )
            self.last_telegram = 0.0
            self.last_sent_connectivity = None
            return
        self.storage.set_meta(key, str(new_id))
        self.storage.delete_meta(RECOVERY_ROTATION_PENDING_KEY)
        self.storage.delete_meta("voltage_status_edit_window_started_at")
        self.delete_previous_voltage_status(old_id, new_id)
        self.storage.delete_meta("telegram_status_pinned_id")
        self.ensure_status_pinned(new_id)
        self.finish_voltage_status_update()

    def maybe_availability_alert(
        self, state: str, duration: float | None = None
    ) -> None:
        """Send one optional alert for an availability transition."""

        if not self.availability_alerts_enabled or not self.telegram.enabled:
            return
        if state == "offline":
            text = tr(self.language, "availability_offline")
        else:
            text = tr(
                self.language,
                "availability_recovered",
                duration=duration_text(duration, self.language),
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
                str(payload.get("phase", "L1")),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning("Ignoring invalid stored voltage event metadata")

    def restore_recovery_stability_timer(self) -> None:
        """Restore the timer for a recovered event from an older version."""

        if self.storage.get_meta(RECOVERY_STATUS_STABLE_KEY):
            return
        event = self.last_voltage_event
        if not event or event[0] != "recovered":
            return
        if not self.storage.get_meta(self.status_message_key()):
            return
        self.storage.set_meta(RECOVERY_STATUS_STABLE_KEY, str(event[3]))

    def restore_voltage_episode(self) -> None:
        """Create episode metadata when upgrading an active older event."""

        if (
            self.storage.get_meta("voltage_episode")
            or not self.last_voltage_event
        ):
            return
        kind, voltage, _, happened_at, phase = self.last_voltage_event
        episode = new_episode(kind, voltage, happened_at, phase)
        raw_start = self.storage.get_meta(
            "voltage_status_edit_window_started_at"
        )
        if raw_start:
            with suppress(ValueError):
                episode["start_at"] = float(raw_start)
        self.storage.set_meta("voltage_episode", json.dumps(episode))

    def update_voltage_episode(
        self, kind: str, voltage: float, happened_at: float, phase: str
    ) -> None:
        """Persist episode detail and compact instability history."""

        raw = self.storage.get_meta("voltage_episode")
        episode = load_json_object(raw)
        episode = update_episode(
            episode,
            kind,
            voltage,
            happened_at,
            phase,
            self.status_edit_window_min * 60,
        )
        self.storage.set_meta("voltage_episode", json.dumps(episode))
        history = load_history(
            self.storage.get_meta("voltage_transition_history")
        )
        history = update_history(history, kind, phase, happened_at)
        self.storage.set_meta(
            "voltage_transition_history", json.dumps(history)
        )

    def instability_transition_count(self, now: float | None = None) -> int:
        """Return recent transitions, or zero before storage is initialized."""

        try:
            raw = self.storage.get_meta("voltage_transition_history")
        except sqlite3.Error:
            return 0
        history = load_history(raw)
        return instability_count(history, now or time.time())

    def voltage_episode_summary(self) -> dict[str, object] | None:
        """Return finalized episode statistics when metadata is available."""

        try:
            raw = self.storage.get_meta("voltage_episode")
        except sqlite3.Error:
            return None
        episode = load_json_object(raw)
        if not episode or not episode.get("finalized_at"):
            return None
        start = float(episode["start_at"])
        end = float(episode["finalized_at"])
        transitions = episode.get("transitions") or []
        stats = self.storage.range_stats(
            start, end, str(episode.get("phase", "L1"))
        )
        zone = ZoneInfo(self.cfg.timezone)
        start_text = datetime.fromtimestamp(start, zone).strftime("%H:%M")
        end_text = datetime.fromtimestamp(end, zone).strftime("%H:%M")
        return {
            "start": start_text,
            "end": end_text,
            "duration": duration_text(end - start, self.language),
            "minimum": fmt(stats["min"]),
            "maximum": fmt(stats["max"]),
            "average": fmt(stats["avg"]),
            "transitions": len(transitions),
            "starts_on_recovery": bool(
                transitions and transitions[0].get("kind") == "recovered"
            ),
            "abnormal": duration_text(
                abnormal_seconds(episode, end), self.language
            ),
        }

    def maybe_finalize_voltage_episode(self) -> None:
        """Finalize the episode and edit the shared status at expiry."""

        episode = load_json_object(self.storage.get_meta("voltage_episode"))
        if not episode or episode.get("finalized_at"):
            return
        deadline = (
            float(episode["start_at"]) + self.status_edit_window_min * 60
        )
        if time.time() < deadline:
            return
        episode["finalized_at"] = deadline
        self.storage.set_meta("voltage_episode", json.dumps(episode))
        current = self.storage.get_meta(self.status_message_key())
        if not self.telegram.enabled or not current:
            return
        try:
            self.telegram.edit(
                int(current),
                multiphase_status_text(self),
                chat_id=self.telegram.status_chat_id,
            )
        except Exception as exc:
            if not message_not_modified(exc):
                logger.warning("Telegram episode summary edit failed: {}", exc)

    def track_recovery_stability(self, kind: str, happened_at: float) -> None:
        """Start or cancel the stable-recovery replacement timer."""

        if kind == "recovered":
            self.storage.set_meta(RECOVERY_STATUS_STABLE_KEY, str(happened_at))
            return
        self.storage.delete_meta(RECOVERY_STATUS_STABLE_KEY)

    def all_voltage_phases_normal(self) -> bool:
        """Return true when every configured phase is online and normal."""

        for state in self.phase_states.values():
            snap = state.snapshot()
            if str(snap["connectivity"]) != "online":
                return False
            if snap["voltage"] is None:
                return False
            voltage = float(snap["voltage"])
            if voltage < self.low_voltage_recovery_v:
                return False
            if self.runtime_voltage_level(voltage) != "normal":
                return False
        return True

    def stable_recovery_status_due(self, now: float) -> bool:
        """Return true after 15 unchanged minutes in recovered state."""

        raw = self.storage.get_meta(RECOVERY_STATUS_STABLE_KEY)
        if not raw or not self.last_voltage_event:
            return False
        if self.last_voltage_event[0] != "recovered":
            return False
        try:
            started_at = float(raw)
        except ValueError:
            self.storage.delete_meta(RECOVERY_STATUS_STABLE_KEY)
            return False
        if now - started_at < RECOVERY_STATUS_STABLE_SECONDS:
            return False
        return self.all_voltage_phases_normal()

    def clear_stable_recovery_context(self) -> None:
        """Clear transient recovery text after stable status replacement."""

        self.last_voltage_event = None
        self.storage.delete_meta("last_voltage_event")
        self.storage.delete_meta("voltage_episode")
        self.storage.delete_meta("voltage_status_edit_window_started_at")
        self.storage.delete_meta(RECOVERY_STATUS_STABLE_KEY)

    def maybe_rotate_stable_recovery_status(self) -> None:
        """Replace an unchanged recovery post with a silent current status."""

        if not self.telegram.enabled or not self.stable_recovery_status_due(
            time.time()
        ):
            return
        key = self.status_message_key()
        old_id = self.storage.get_meta(key)
        text = multiphase_status_text(self, include_event_context=False)
        try:
            new_id = self.telegram.send(
                text, notify=False, chat_id=self.telegram.status_chat_id
            )
        except Exception as exc:
            logger.warning(
                "Telegram stable recovery replacement failed: {}", exc
            )
            return
        self.storage.set_meta(key, str(new_id))
        self.clear_stable_recovery_context()
        self.delete_previous_voltage_status(old_id, new_id)
        self.storage.delete_meta("telegram_status_pinned_id")
        self.ensure_status_pinned(new_id)
        self.finish_voltage_status_update()

    def voltage_event_notify_allowed(self, kind: str, now: float) -> bool:
        """Return whether an abnormal status replacement may make sound."""

        if kind == "recovered" or self.telegram.quiet_now():
            return False
        if self.notification_cooldown_min <= 0:
            return True
        raw = self.storage.get_meta("last_voltage_notification_at")
        if not raw:
            return True
        try:
            previous = float(raw)
        except ValueError:
            return True
        return now - previous >= self.notification_cooldown_min * 60

    def restore_voltage_status_window(self) -> None:
        """Reuse recent persisted events when upgrading older installations."""

        key = "voltage_status_edit_window_started_at"
        if self.storage.get_meta(key) is not None:
            return
        if not self.last_voltage_event:
            return
        if not self.storage.get_meta(self.status_message_key()):
            return
        # Older versions already stored the latest event and message ID.
        started_at = self.last_voltage_event[3]
        if 0 <= time.time() - started_at <= self.status_edit_window_min * 60:
            self.storage.set_meta(key, str(started_at))

    def voltage_status_edit_window_active(self, happened_at: float) -> bool:
        """Return whether the current voltage episode may reuse its message."""

        if not self.storage.get_meta(self.status_message_key()):
            return False
        raw = self.storage.get_meta("voltage_status_edit_window_started_at")
        if not raw:
            return False
        try:
            started_at = float(raw)
        except ValueError:
            self.storage.delete_meta("voltage_status_edit_window_started_at")
            return False
        age = happened_at - started_at
        return 0 <= age <= self.status_edit_window_min * 60

    def finish_voltage_status_update(self) -> None:
        """Remember the latest state after a successful Telegram update."""

        snap = self.state.snapshot()
        self.last_sent_voltage = snap["voltage"]
        self.last_sent_connectivity = str(snap["connectivity"])
        self.last_telegram = time.monotonic()

    def edit_voltage_status(self, old_id: str) -> bool | None:
        """Return success, failure, or None if the message needs replacing."""

        try:
            self.telegram.edit(
                int(old_id),
                multiphase_status_text(self),
                chat_id=self.telegram.status_chat_id,
            )
        except Exception as exc:
            if message_missing(exc):
                return None
            if not message_not_modified(exc):
                logger.warning("Telegram voltage status edit failed: {}", exc)
                return False
        self.finish_voltage_status_update()
        return True

    def replace_voltage_status(self, notify: bool, happened_at: float) -> bool:
        """Edit an active episode or safely replace the shared status."""

        key = self.status_message_key()
        old_id = self.storage.get_meta(key)
        if old_id and self.voltage_status_edit_window_active(happened_at):
            edited = self.edit_voltage_status(old_id)
            # A temporary edit failure must not create a duplicate post.
            if edited is not None:
                return edited
        try:
            new_id = self.telegram.send(
                multiphase_status_text(self),
                notify=notify,
                chat_id=self.telegram.status_chat_id,
            )
        except Exception as exc:
            logger.warning("Telegram voltage status publish failed: {}", exc)
            return False
        self.storage.set_meta(key, str(new_id))
        if notify:
            self.storage.set_meta(
                "last_voltage_notification_at", str(happened_at)
            )
        # Keep the original start through recovery and repeated transitions.
        self.storage.set_meta(
            "voltage_status_edit_window_started_at", str(happened_at)
        )
        self.delete_previous_voltage_status(old_id, new_id)
        self.storage.delete_meta("telegram_status_pinned_id")
        self.ensure_status_pinned(new_id)
        self.finish_voltage_status_update()
        return True

    def delete_previous_status(self, old_id: str) -> None:
        """Unpin only known pinned status messages before deleting them."""

        pinned_id = self.storage.get_meta("telegram_status_pinned_id")
        if pinned_id == old_id:
            try:
                self.telegram.unpin(int(old_id), self.telegram.status_chat_id)
            except Exception as exc:
                if "message to unpin not found" not in str(exc).lower():
                    logger.warning("Telegram old status unpin failed: {}", exc)
                    return
            self.storage.delete_meta("telegram_status_pinned_id")
        try:
            self.telegram.delete(int(old_id), self.telegram.status_chat_id)
        except Exception as exc:
            logger.warning("Telegram old status delete failed: {}", exc)

    def delete_previous_voltage_status(
        self, old_id: str | None, new_id: int
    ) -> None:
        """Delete the previous shared status after the replacement exists."""

        if not old_id or str(new_id) == old_id:
            return
        self.delete_previous_status(old_id)

    def record_voltage_event(
        self,
        kind: str,
        voltage: float,
        frequency: float,
        phase: str = "L1",
    ) -> None:
        """Store a voltage event and replace the shared Telegram status."""

        happened_at = time.time()
        self.last_voltage_event = (
            kind,
            voltage,
            frequency,
            happened_at,
            phase,
        )
        payload = {
            "kind": kind,
            "voltage": voltage,
            "frequency": frequency,
            "timestamp": happened_at,
            "phase": phase,
        }
        self.storage.set_meta("last_voltage_event", json.dumps(payload))
        self.track_recovery_stability(kind, happened_at)
        self.update_voltage_episode(kind, voltage, happened_at, phase)
        if not self.telegram.enabled:
            return
        notify = self.voltage_event_notify_allowed(kind, happened_at)
        self.replace_voltage_status(notify, happened_at)

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

    def level_dwell(self, level: str) -> float:
        """Choose warning, critical, or normal-state confirmation time."""

        if level == "warning":
            return self.warning_dwell
        return self.recovery_dwell if level == "normal" else self.alert_dwell

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
        dwell = self.level_dwell(level)
        if now - self.alert_candidate[1] < dwell:
            return
        self.alert_level = level
        self.alert_candidate = None
        kind = "recovered"
        if level != "normal":
            side = "high" if voltage >= self.warning_high else "low"
            kind = f"{level}_{side}"
        self.record_voltage_event(kind, voltage, frequency)

    def maintenance_active(self) -> bool:
        """Return whether alert delivery is temporarily suppressed."""

        return time.time() < self.maintenance_until

    def set_maintenance(self, minutes: int) -> str:
        """Set or clear maintenance mode."""

        self.maintenance_until = (
            0.0 if minutes <= 0 else time.time() + minutes * 60
        )
        self.storage.set_meta("maintenance_until", str(self.maintenance_until))
        if minutes <= 0:
            return tr(self.language, "state_off")
        return tr(self.language, "minutes", value=minutes)

    def quality_text(self, phase: str = "L1", hours: int = 24) -> str:
        """Render localized voltage-quality statistics."""

        q = quality_stats(
            self.cfg.database_path,
            phase,
            time.time() - hours * 3600,
            self.cfg.warning_low,
            self.cfg.critical_low,
            self.cfg.warning_high,
        )
        lang = self.language
        label = self.phase_label(phase)
        return "\n".join(
            [
                tr(lang, "quality_title", phase=label, hours=hours),
                tr(lang, "quality_samples", value=q["samples"]),
                tr(
                    lang,
                    "quality_below",
                    voltage=self.cfg.warning_low,
                    value=q["below_warning"],
                ),
                tr(
                    lang,
                    "quality_below",
                    voltage=self.cfg.critical_low,
                    value=q["below_critical"],
                ),
                tr(
                    lang,
                    "quality_above",
                    voltage=self.cfg.warning_high,
                    value=q["above_warning"],
                ),
                tr(lang, "quality_bad", value=q["bad_pct"]),
            ]
        )

    def outage_history_text(self, phase: str = "L1") -> str:
        """Render Shelly connectivity gaps, not proven power outages."""

        try:
            rows = outage_history(self.cfg.database_path, phase, 10)
        except sqlite3.Error:
            return self.outage_fallback_text(phase)
        if not rows:
            return tr(
                self.language, "no_outages", phase=self.phase_label(phase)
            )
        lines = [
            tr(self.language, "outages_title", phase=self.phase_label(phase))
        ]
        now = time.time()
        for start, end in rows:
            duration = (end or now) - start
            state = (
                tr(self.language, "outage_now")
                if end is None
                else duration_text(duration, self.language)
            )
            lines.append(f"{format_stamp(start, self.cfg.timezone)} — {state}")
        lines.append(tr(self.language, "outage_interpretation"))
        return "\n".join(lines)

    def outage_fallback_text(self, phase: str) -> str:
        """Render daily outage totals if detailed history is unavailable."""

        stats = self.storage.outage_stats(time.time(), phase)
        lang = self.language
        return "\n".join(
            [
                tr(lang, "outage_count_today", count=stats["count"]),
                tr(
                    lang,
                    "outage_unavailable",
                    duration=duration_text(float(stats["duration"]), lang),
                ),
                tr(
                    lang,
                    "outage_boundaries",
                    failures=self.cfg.offline_after_failures,
                    successes=self.cfg.recovery_after_successes,
                ),
                tr(lang, "outage_interpretation"),
            ]
        )

    def daily_summary_text(self) -> str:
        """Render localized three-phase daily operational summary."""

        now = time.time()
        local = datetime.fromtimestamp(now, ZoneInfo(self.cfg.timezone))
        since = local.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        lines = [
            tr(self.language, "summary_title"),
            report_period_line(
                self.language, "summary_period", since, now, self.cfg.timezone
            ),
        ]
        has_loss = False
        for phase in self.active_phases():
            if not self.phase_configured(phase):
                lines.append(
                    tr(self.language, "summary_unconfigured", phase=phase)
                )
                continue
            phase_line, phase_loss = daily_phase_report_line(
                self, phase, since, now
            )
            lines.append(phase_line)
            has_loss = has_loss or phase_loss
        key = (
            "outage_interpretation"
            if has_loss
            else "summary_connection_notice"
        )
        lines.append(tr(self.language, key))
        lines.append(tr(self.language, "summary_observation_notice"))
        return "\n".join(lines)

    def weekly_summary_text(self) -> str:
        """Render a read-only quality digest for the previous seven days."""

        now = time.time()
        since = now - 7 * 86400
        lines = [tr(self.language, "weekly_title")]
        lines.append(
            report_period_line(
                self.language, "weekly_period", since, now, self.cfg.timezone
            )
        )
        for phase in self.active_phases():
            if not self.phase_configured(phase):
                lines.append(
                    tr(self.language, "summary_unconfigured", phase=phase)
                )
                continue
            lines.extend(weekly_phase_lines(self, phase, since, now))
        lines.append(tr(self.language, "weekly_notice"))
        return "\n".join(lines)

    def event_timeline_text(self) -> str:
        """Display bounded recent events with an explicit history limit."""

        with self.config_lock:
            now = time.time()
            since = now - 86400
            rows = voltage_timeline_events(self, since, now)
            rows.extend(connectivity_timeline_events(self, since, now))
            rows.sort(key=lambda row: row[0], reverse=True)
            lines = [tr(self.language, "events_title")]
            if not rows:
                lines.append(tr(self.language, "events_empty"))
            else:
                lines.extend(text for _, text in rows[:20])
            lines.append(tr(self.language, "events_notice"))
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
        attempt = time.monotonic()
        if attempt - self.last_summary_attempt < 300.0:
            return
        self.last_summary_attempt = attempt
        self.telegram.send(
            self.daily_summary_text(), chat_id=self.telegram.status_chat_id
        )
        self.last_summary_day = day
        self.storage.set_meta("daily_summary_day", day)

    def maybe_weekly_private_reports(
        self, local_time: datetime | None = None
    ) -> None:
        """Send due opt-in reports only to authorized private chats."""

        if not self.telegram.enabled:
            return
        local = local_time or datetime.now(ZoneInfo(self.cfg.timezone))
        week_key = private_weekly_due_key(local)
        if week_key is None:
            return
        for admin_id in self.cfg.telegram_admin_user_ids:
            user_id = str(admin_id)
            try:
                self.send_due_private_weekly(user_id, week_key)
            except Exception as exc:
                logger.warning(
                    "Private weekly report failed: {}", type(exc).__name__
                )

    def send_due_private_weekly(self, user_id: str, week_key: str) -> None:
        """Send at most one successful weekly digest per administrator."""

        enabled = f"telegram_weekly_private_enabled_{user_id}"
        delivered = f"telegram_weekly_private_sent_{user_id}"
        if self.storage.get_meta(enabled) != "1":
            return
        if self.storage.get_meta(delivered) == week_key:
            return
        now = time.monotonic()
        if now - self.weekly_digest_attempts.get(user_id, -3600) < 3600:
            return
        self.weekly_digest_attempts[user_id] = now
        self.telegram.send(
            self.weekly_summary_text(), notify=False, chat_id=user_id
        )
        self.storage.set_meta(delivered, week_key)

    def device_health_text(self) -> str:
        """Render read-only device health for configured phases."""

        lines = [tr(self.language, "device_title")]
        for phase in self.active_phases():
            if not self.phase_configured(phase):
                lines.append(
                    tr(self.language, "device_unconfigured", phase=phase)
                )
                continue
            try:
                info = self.phase_clients[phase].device_health()
                skew = info.get("time_skew_sec")
                skew_text = localized_clock_skew(self.language, skew)
                lines.append(
                    tr(
                        self.language,
                        "device_details",
                        phase=self.phase_label(phase),
                        model=info.get("model"),
                        firmware=info.get("firmware"),
                        uptime=info.get("uptime"),
                        skew=skew_text,
                    )
                )
            except Exception as exc:
                lines.append(
                    tr(
                        self.language,
                        "device_offline",
                        phase=self.phase_label(phase),
                        error=type(exc).__name__,
                    )
                )
        return "\n".join(lines)

    def backup_health_text(self) -> str:
        """Render backup and SQLite health for diagnostics."""

        info = backup_health(self.cfg.backup_dir)
        if info["status"] != "ok":
            return tr(self.language, "backup_missing")
        stamp = format_stamp(float(info["mtime"]), self.cfg.timezone)
        size = int(info["size"]) // 1024
        return tr(
            self.language,
            "backup_ok",
            stamp=stamp,
            size=size,
            integrity=self.storage.integrity(),
        )

    def audit_text(self) -> str:
        """Render recent admin changes without exposing secrets."""

        lines = [tr(self.language, "audit_title")]
        for ts, user, setting, old, new in self.storage.audit_recent(10):
            stamp = format_stamp(float(ts), self.cfg.timezone)
            lines.append(f"{stamp} · {user} · {setting}: {old} → {new}")
        return "\n".join(lines)

    def setting_defaults(self) -> dict[str, str]:
        """Return notification defaults from environment configuration."""

        return {
            "meter_type": self.cfg.shelly_meter_type,
            "language": self.cfg.telegram_language,
            "instability_enabled": "1"
            if self.cfg.voltage_instability_enabled
            else "0",
            "silent": "1" if self.cfg.telegram_silent_default else "0",
            "quiet_enabled": "1",
            "notify_cooldown": str(self.cfg.voltage_notification_cooldown_min),
            "status_edit_window": str(self.cfg.voltage_status_edit_window_min),
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
            "warning_dwell": str(self.cfg.alert_dwell_sec),
            "recovery_dwell": str(self.cfg.alert_recovery_dwell_sec),
        }

    def setting_value(self, name: str) -> str:
        """Read one runtime setting with environment fallback."""

        stored = self.storage.get_meta(f"telegram_setting_{name}")
        if stored is not None:
            return stored
        if name == "warning_dwell":
            return self.setting_value("alert_dwell")
        return self.setting_defaults()[name]

    def load_runtime_settings(self) -> None:
        """Apply all persisted settings without restarting the service."""

        meter_type = self.setting_value("meter_type")
        if meter_type != self.meter_type:
            self.reconfigure_meter_type(meter_type)
        self.load_runtime_notification_settings()
        self.load_runtime_alert_settings()

    def load_runtime_notification_settings(self) -> None:
        """Apply Telegram delivery and display settings."""

        self.language = self.setting_value("language")
        self.instability_enabled = (
            self.setting_value("instability_enabled") == "1"
        )
        self.telegram.silent_default = self.setting_value("silent") == "1"
        self.telegram.quiet_enabled = (
            self.setting_value("quiet_enabled") == "1"
        )
        self.telegram.quiet_start_hour = int(self.setting_value("quiet_start"))
        self.telegram.quiet_end_hour = int(self.setting_value("quiet_end"))
        self.notification_cooldown_min = int(
            self.setting_value("notify_cooldown")
        )
        self.status_edit_window_min = int(
            self.setting_value("status_edit_window")
        )
        self.pin_status = self.setting_value("pin_status") == "1"

    def validated_warning_dwell(self) -> float:
        """Reject invalid saved warning dwell without disrupting alerts."""

        try:
            value = float(self.setting_value("warning_dwell"))
        except (TypeError, ValueError, OverflowError):
            return self.alert_dwell
        if not math.isfinite(value) or not 0 <= value <= 600:
            return self.alert_dwell
        return value

    def load_runtime_alert_settings(self) -> None:
        """Apply voltage thresholds, summaries, and alert timing settings."""

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
        self.alert_dwell = float(self.setting_value("alert_dwell"))
        self.warning_dwell = self.validated_warning_dwell()
        self.recovery_dwell = float(self.setting_value("recovery_dwell"))

    def is_admin(self, user_id: str) -> bool:
        """Return whether a Telegram user may change runtime settings."""

        return user_id in self.cfg.telegram_admin_user_ids

    def settings_state_labels(self) -> dict[str, str]:
        """Return short localized labels used by the settings view."""

        cooldown = (
            tr(self.language, "minutes", value=self.notification_cooldown_min)
            if self.notification_cooldown_min
            else tr(self.language, "state_off")
        )
        on = tr(self.language, "state_on")
        off = tr(self.language, "state_off")
        return {
            "silent": tr(
                self.language,
                "silent" if self.telegram.silent_default else "audible",
            ),
            "quiet": on if self.telegram.quiet_enabled else off,
            "low": on if self.low_voltage_alert_enabled else off,
            "levels": on if self.alerts_enabled else off,
            "maintenance": on if self.maintenance_active() else off,
            "availability": on if self.availability_alerts_enabled else off,
            "summary": on if self.daily_summary_enabled else off,
            "pin": on if self.pin_status else off,
            "instability": on if self.instability_enabled else off,
            "cooldown": cooldown,
            "edit_window": tr(
                self.language, "minutes", value=self.status_edit_window_min
            ),
        }

    def settings_text(self) -> str:
        """Render the localized Telegram notification policy."""

        labels = self.settings_state_labels()
        lines = [tr(self.language, "settings_title"), ""]
        lines.extend(self.settings_delivery_lines(labels))
        lines.extend(self.settings_alert_lines(labels))
        return "\n".join(lines)

    def settings_delivery_lines(self, labels: dict[str, str]) -> list[str]:
        """Render delivery, language, and edit-window setting lines."""

        lang = self.language
        quiet = (
            f"{self.telegram.quiet_start_hour:02d}:00–"
            f"{self.telegram.quiet_end_hour:02d}:00"
        )
        return [
            tr(
                lang,
                "settings_module",
                value=METER_MODELS.get(self.meter_type, self.meter_type),
            ),
            tr(
                lang,
                "settings_language",
                value={"ru": "RU", "uk": "UA", "en": "EN"}[lang],
            ),
            tr(lang, "settings_normal", value=labels["silent"]),
            tr(lang, "settings_quiet", state=labels["quiet"], hours=quiet),
            tr(lang, "settings_cooldown", value=labels["cooldown"]),
            tr(lang, "settings_edit", value=labels["edit_window"]),
            tr(lang, "settings_instability", state=labels["instability"]),
        ]

    def settings_alert_lines(self, labels: dict[str, str]) -> list[str]:
        """Render alert and schedule/status setting lines."""

        lines = self.settings_threshold_lines(labels)
        lines.extend(self.settings_schedule_lines(labels))
        return lines

    def settings_threshold_lines(self, labels: dict[str, str]) -> list[str]:
        """Render voltage threshold and maintenance setting lines."""

        lang = self.language
        voltage_range = (
            f"{self.critical_low:g}/{self.warning_low:g}–"
            f"{self.warning_high:g}/{self.critical_high:g} V"
        )
        return [
            tr(
                lang,
                "settings_low",
                state=labels["low"],
                voltage=self.low_voltage_alert_v,
            ),
            tr(lang, "settings_recovery", voltage=self.low_voltage_recovery_v),
            tr(lang, "settings_warning_dwell", dwell=self.warning_dwell),
            tr(
                lang,
                "settings_levels",
                state=labels["levels"],
                range=voltage_range,
            ),
            tr(lang, "settings_maintenance", state=labels["maintenance"]),
        ]

    def settings_schedule_lines(self, labels: dict[str, str]) -> list[str]:
        """Render phase, outage, summary, pin, and dwell setting lines."""

        lang = self.language
        summary_time = (
            f"{self.daily_summary_hour:02d}:{self.daily_summary_minute:02d}"
        )
        return [
            tr(
                lang,
                "settings_lines",
                phases=self.phase_labels(),
            ),
            tr(lang, "settings_outage", state=labels["availability"]),
            tr(
                lang,
                "settings_summary",
                state=labels["summary"],
                time=summary_time,
            ),
            tr(lang, "settings_pin", state=labels["pin"]),
            tr(
                lang,
                "settings_dwell",
                dwell=self.alert_dwell,
                recovery=self.recovery_dwell,
            ),
        ]

    def settings_main_text(self) -> str:
        """Render a compact settings dashboard."""

        labels = self.settings_state_labels()
        lang = self.language
        return "\n".join(
            [
                tr(lang, "settings_title"),
                tr(
                    lang,
                    "settings_module",
                    value=METER_MODELS.get(self.meter_type, self.meter_type),
                ),
                tr(
                    lang,
                    "settings_language",
                    value={"ru": "RU", "uk": "UA", "en": "EN"}[lang],
                ),
                tr(
                    lang,
                    "settings_levels",
                    state=labels["levels"],
                    range=self.voltage_range_text(),
                ),
                tr(lang, "settings_warning_dwell", dwell=self.warning_dwell),
                tr(lang, "settings_outage", state=labels["availability"]),
                "",
                tr(lang, "settings_choose"),
            ]
        )

    def voltage_range_text(self) -> str:
        """Return the ordered warning and critical voltage range."""

        unit = tr(self.language, "unit_voltage")
        return (
            f"{self.critical_low:g}/{self.warning_low:g}–"
            f"{self.warning_high:g}/{self.critical_high:g} {unit}"
        )

    def settings_device_text(self) -> str:
        """Render the Shelly device settings page."""

        labels = self.settings_state_labels()
        return "\n".join(
            [
                tr(self.language, "settings_section_device"),
                tr(
                    self.language,
                    "settings_module",
                    value=METER_MODELS.get(self.meter_type, self.meter_type),
                ),
                tr(
                    self.language,
                    "settings_lines",
                    phases=self.phase_labels(),
                ),
                tr(self.language, "settings_pin", state=labels["pin"]),
            ]
        )

    def settings_notifications_text(self) -> str:
        """Render notification settings without unrelated controls."""

        labels = self.settings_state_labels()
        lang = self.language
        quiet = (
            f"{self.telegram.quiet_start_hour:02d}:00–"
            f"{self.telegram.quiet_end_hour:02d}:00"
        )
        return "\n".join(
            [
                tr(lang, "settings_section_notifications"),
                tr(
                    lang,
                    "settings_language",
                    value={"ru": "RU", "uk": "UA", "en": "EN"}[lang],
                ),
                tr(lang, "settings_normal", value=labels["silent"]),
                tr(lang, "settings_quiet", state=labels["quiet"], hours=quiet),
                tr(lang, "settings_cooldown", value=labels["cooldown"]),
                tr(lang, "settings_edit", value=labels["edit_window"]),
                tr(lang, "settings_instability", state=labels["instability"]),
            ]
        )

    def settings_voltage_text(self) -> str:
        """Render voltage thresholds and dwell settings."""

        labels = self.settings_state_labels()
        lang = self.language
        return "\n".join(
            [
                tr(lang, "settings_section_voltage"),
                tr(
                    lang,
                    "settings_low",
                    state=labels["low"],
                    voltage=self.low_voltage_alert_v,
                ),
                tr(
                    lang,
                    "settings_recovery",
                    voltage=self.low_voltage_recovery_v,
                ),
                tr(
                    lang,
                    "settings_levels",
                    state=labels["levels"],
                    range=self.voltage_range_text(),
                ),
                tr(lang, "settings_warning_dwell", dwell=self.warning_dwell),
                tr(
                    lang,
                    "settings_dwell",
                    dwell=self.alert_dwell,
                    recovery=self.recovery_dwell,
                ),
            ]
        )

    def settings_schedule_text(self) -> str:
        """Render outage, summary, and status schedule settings."""

        labels = self.settings_state_labels()
        summary_time = (
            f"{self.daily_summary_hour:02d}:{self.daily_summary_minute:02d}"
        )
        return "\n".join(
            [
                tr(self.language, "settings_section_schedule"),
                tr(
                    self.language,
                    "settings_outage",
                    state=labels["availability"],
                ),
                tr(
                    self.language,
                    "settings_summary",
                    state=labels["summary"],
                    time=summary_time,
                ),
                tr(self.language, "settings_pin", state=labels["pin"]),
            ]
        )

    def settings_service_text(self) -> str:
        """Render maintenance and local storage health."""

        labels = self.settings_state_labels()
        return "\n".join(
            [
                tr(self.language, "settings_section_service"),
                tr(
                    self.language,
                    "settings_maintenance",
                    state=labels["maintenance"],
                ),
                self.backup_health_text(),
            ]
        )

    def settings_title_value_text(
        self, title_key: str, value_key: str, **values: object
    ) -> str:
        """Render a focused page with a title and one current value."""

        return "\n".join(
            [
                tr(self.language, title_key),
                tr(self.language, value_key, **values),
            ]
        )

    def settings_meter_page_text(self) -> str:
        """Render the Shelly model selector summary."""

        value = METER_MODELS.get(self.meter_type, self.meter_type)
        return self.settings_title_value_text(
            "settings_section_meter", "settings_module", value=value
        )

    def settings_language_page_text(self) -> str:
        """Render the language selector summary."""

        value = {"ru": "RU", "uk": "UA", "en": "EN"}[self.language]
        return self.settings_title_value_text(
            "settings_section_language", "settings_language", value=value
        )

    def settings_quiet_page_text(self) -> str:
        """Render quiet-hours status and current window."""

        state = self.settings_state_labels()["quiet"]
        start = self.telegram.quiet_start_hour
        end = self.telegram.quiet_end_hour
        return self.settings_title_value_text(
            "settings_section_quiet",
            "settings_quiet",
            state=state,
            hours=f"{start:02d}:00–{end:02d}:00",
        )

    def settings_policy_page_text(self, page: str) -> str:
        """Render cooldown or edit-window current value."""

        labels = self.settings_state_labels()
        mapping = {
            "cooldown": (
                "settings_section_cooldown",
                "settings_cooldown",
                labels["cooldown"],
            ),
            "edit": (
                "settings_section_edit",
                "settings_edit",
                labels["edit_window"],
            ),
        }
        title, value_key, value = mapping[page]
        return self.settings_title_value_text(title, value_key, value=value)

    def settings_level_page_text(self, page: str) -> str:
        """Render low or high ordered voltage thresholds."""

        key = (
            "settings_section_low"
            if page == "low"
            else "settings_section_high"
        )
        labels = self.settings_state_labels()
        return self.settings_title_value_text(
            key,
            "settings_levels",
            state=labels["levels"],
            range=self.voltage_range_text(),
        )

    def settings_legacy_low_page_text(self) -> str:
        """Render legacy low-voltage alert thresholds."""

        labels = self.settings_state_labels()
        lines = [
            tr(self.language, "settings_section_legacy_low"),
            tr(
                self.language,
                "settings_low",
                state=labels["low"],
                voltage=self.low_voltage_alert_v,
            ),
            tr(
                self.language,
                "settings_recovery",
                voltage=self.low_voltage_recovery_v,
            ),
        ]
        return "\n".join(lines)

    def settings_dwell_page_text(self) -> str:
        """Render alert and recovery dwell values."""

        return self.settings_title_value_text(
            "settings_section_dwell",
            "settings_dwell",
            dwell=self.alert_dwell,
            recovery=self.recovery_dwell,
        )

    def settings_summary_time_page_text(self) -> str:
        """Render daily summary state and schedule."""

        labels = self.settings_state_labels()
        time_value = (
            f"{self.daily_summary_hour:02d}:{self.daily_summary_minute:02d}"
        )
        return self.settings_title_value_text(
            "settings_section_summary_time",
            "settings_summary",
            state=labels["summary"],
            time=time_value,
        )

    def settings_simple_page_text(self, page: str) -> str:
        """Render one focused settings subpage."""

        handlers = {
            "meter": self.settings_meter_page_text,
            "language": self.settings_language_page_text,
            "quiet": self.settings_quiet_page_text,
            "cooldown": lambda: self.settings_policy_page_text("cooldown"),
            "edit": lambda: self.settings_policy_page_text("edit"),
            "tests": lambda: tr(self.language, "settings_section_tests"),
            "low": lambda: self.settings_level_page_text("low"),
            "high": lambda: self.settings_level_page_text("high"),
            "legacy_low": self.settings_legacy_low_page_text,
            "dwell": self.settings_dwell_page_text,
            "summary_time": self.settings_summary_time_page_text,
            "reset": lambda: tr(self.language, "settings_reset_confirm"),
        }
        if page not in handlers:
            raise ValueError("unknown settings page")
        return handlers[page]()

    def settings_page_text(self, page: str = "main") -> str:
        """Render one compact settings page."""

        pages = {
            "main": self.settings_main_text,
            "device": self.settings_device_text,
            "notifications": self.settings_notifications_text,
            "voltage": self.settings_voltage_text,
            "schedule": self.settings_schedule_text,
            "service": self.settings_service_text,
        }
        if page in pages:
            return pages[page]()
        return self.settings_simple_page_text(page)

    def setting_button(self, text: str, data: str) -> dict[str, str]:
        """Build one Telegram settings callback button."""

        return {"text": text, "callback_data": f"cfg:{data}"}

    def translated_button(
        self, key: str, data: str, **values: object
    ) -> dict[str, str]:
        """Build one localized Telegram settings button."""

        return self.setting_button(tr(self.language, key, **values), data)

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
        """Build localized notification settings rows."""

        b = self.setting_button
        rows = [[b("RU", "lang_ru"), b("UA", "lang_uk"), b("EN", "lang_en")]]
        rows.extend(self.notification_policy_rows())
        rows.extend(self.notification_test_rows())
        rows.extend(self.notification_threshold_rows())
        return rows

    def notification_policy_rows(self) -> list[list[dict[str, str]]]:
        """Build quiet-hours, cooldown, and edit-window controls."""

        tb = self.translated_button
        return [
            [tb("button_silent", "silent_toggle")],
            [tb("button_quiet", "quiet_toggle")],
            [
                tb("button_cooldown_off", "cooldown_0"),
                tb("button_cooldown_min", "cooldown_3", value=3),
            ],
            [
                tb("button_cooldown_min", "cooldown_5", value=5),
                tb("button_cooldown_min", "cooldown_10", value=10),
            ],
            [
                tb("button_edit", "editwindow_3", value=3),
                tb("button_edit", "editwindow_5", value=5),
                tb("button_edit", "editwindow_10", value=10),
            ],
            [tb("button_instability", "instability_toggle")],
        ]

    def notification_test_rows(self) -> list[list[dict[str, str]]]:
        """Build explicit Telegram delivery and edit test controls."""

        tb = self.translated_button
        return [
            [
                tb("button_test_loud", "test_loud"),
                tb("button_test_silent", "test_silent"),
            ],
            [tb("button_test_edit", "test_edit")],
        ]

    def notification_threshold_rows(self) -> list[list[dict[str, str]]]:
        """Build quiet-hour and legacy low-voltage controls."""

        tb = self.translated_button
        return [
            [
                tb("button_start_dec", "start_dec"),
                tb("button_start_inc", "start_inc"),
            ],
            [tb("button_end_dec", "end_dec"), tb("button_end_inc", "end_inc")],
            [tb("button_low", "low_toggle")],
            [
                tb("button_threshold", "low_dec", delta="-5V"),
                tb("button_threshold", "low_inc", delta="+5V"),
            ],
            [
                tb("button_recovery", "rec_dec", delta="-5V"),
                tb("button_recovery", "rec_inc", delta="+5V"),
            ],
        ]

    def warning_dwell_setting_rows(self) -> list[list[dict[str, str]]]:
        """Build localizable warning-only confirmation presets."""

        button = self.setting_button
        return [
            [
                button(
                    tr(self.language, "button_warning_dwell", seconds=n),
                    f"warnwait_{n}",
                )
                for n in pair
            ]
            for pair in ((15, 60), (180, 300))
        ]

    def voltage_setting_rows(self) -> list[list[dict[str, str]]]:
        """Build warning and critical voltage setting rows."""

        button = self.setting_button
        return [
            [button("⚠️ Уровни ON/OFF", "alerts_toggle")],
            *self.warning_dwell_setting_rows(),
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
            [
                button(
                    tr(self.language, "button_outage"), "availability_toggle"
                )
            ],
            [button(tr(self.language, "button_summary"), "daily_toggle")],
            [
                button("Summary -1h", "dailyhour_dec"),
                button("Summary +1h", "dailyhour_inc"),
            ],
            [
                button("Summary -5m", "dailymin_dec"),
                button("Summary +5m", "dailymin_inc"),
            ],
            [button(tr(self.language, "button_pin"), "pin_toggle")],
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
            [button(tr(self.language, "button_reset"), "reset")],
        ]

    def settings_nav_button(self, key: str, page: str) -> dict[str, str]:
        """Build one compact settings navigation button."""

        return {
            "text": tr(self.language, key),
            "callback_data": f"cfg:nav:{page}",
        }

    def settings_action_button(
        self, text: str, page: str, action: str
    ) -> dict[str, str]:
        """Build one page-preserving settings action button."""

        return {
            "text": text,
            "callback_data": f"cfg:set:{page}:{action}",
        }

    def settings_back_button(self, page: str) -> dict[str, str]:
        """Build one localized settings Back button."""

        return self.settings_nav_button("settings_back", page)

    def selected_choice(self, label: str, selected: bool) -> str:
        """Prefix the active settings choice with a check mark."""

        return f"✅ {label}" if selected else label

    def settings_main_rows(self) -> list[list[dict[str, str]]]:
        """Build the five compact top-level settings sections."""

        nav = self.settings_nav_button
        return [
            [
                nav("settings_section_device", "device"),
                nav("settings_section_voltage", "voltage"),
            ],
            [
                nav("settings_section_notifications", "notifications"),
                nav("settings_section_schedule", "schedule"),
            ],
            [nav("settings_section_service", "service")],
        ]

    def settings_device_rows(self) -> list[list[dict[str, str]]]:
        """Build device settings navigation."""

        return [
            [self.settings_nav_button("settings_change_meter", "meter")],
            [self.settings_back_button("main")],
        ]

    def settings_meter_rows(self) -> list[list[dict[str, str]]]:
        """Build a compact Shelly model selector."""

        choices = [
            ("Pro EM-50", "meter_em50", "pro_em50"),
            ("EM Gen3", "meter_em_gen3", "em_gen3"),
            ("PM Mini G3", "meter_pm_mini_gen3", "pm_mini_gen3"),
            ("Plus PM Mini", "meter_plus_pm_mini", "plus_pm_mini"),
            ("Plus 1PM", "meter_plus_1pm", "plus_1pm"),
            ("1PM Gen3", "meter_1pm_gen3", "1pm_gen3"),
            ("1PM Gen4", "meter_1pm_gen4", "1pm_gen4"),
            ("1PM Mini G3", "meter_1pm_mini_gen3", "1pm_mini_gen3"),
            ("1PM Mini G4", "meter_1pm_mini_gen4", "1pm_mini_gen4"),
            ("Pro 3EM", "meter_pro3em", "pro3em"),
        ]
        buttons = [
            self.settings_action_button(
                self.selected_choice(label, self.meter_type == value),
                "device",
                action,
            )
            for label, action, value in choices
        ]
        rows = [
            buttons[index : index + 2] for index in range(0, len(buttons), 2)
        ]
        rows.append([self.settings_back_button("device")])
        return rows

    def settings_notification_rows(self) -> list[list[dict[str, str]]]:
        """Build compact notification settings controls."""

        nav = self.settings_nav_button
        action = self.settings_action_button
        return [
            [
                nav("settings_section_language", "language"),
                action(
                    tr(self.language, "button_silent"),
                    "notifications",
                    "silent_toggle",
                ),
            ],
            [
                nav("settings_section_quiet", "quiet"),
                nav("settings_section_cooldown", "cooldown"),
            ],
            [
                nav("settings_section_edit", "edit"),
                action(
                    tr(self.language, "button_instability"),
                    "notifications",
                    "instability_toggle",
                ),
            ],
            [nav("settings_section_tests", "tests")],
            [self.settings_back_button("main")],
        ]

    def settings_language_rows(self) -> list[list[dict[str, str]]]:
        """Build language choices and return navigation."""

        choices = [("RU", "ru"), ("UA", "uk"), ("EN", "en")]
        buttons = [
            self.settings_action_button(
                self.selected_choice(label, self.language == value),
                "notifications",
                f"lang_{value}",
            )
            for label, value in choices
        ]
        return [buttons, [self.settings_back_button("notifications")]]

    def settings_quiet_rows(self) -> list[list[dict[str, str]]]:
        """Build quiet-hours controls."""

        action = self.settings_action_button

        def tb(key: str, act: str) -> dict[str, str]:
            """Build one quiet-hours action button."""

            return action(tr(self.language, key), "quiet", act)

        return [
            [tb("button_quiet", "quiet_toggle")],
            [
                tb("button_start_dec", "start_dec"),
                tb("button_start_inc", "start_inc"),
            ],
            [tb("button_end_dec", "end_dec"), tb("button_end_inc", "end_inc")],
            [self.settings_back_button("notifications")],
        ]

    def settings_choice_rows(
        self, page: str, prefix: str, values: tuple[int, ...]
    ) -> list[list[dict[str, str]]]:
        """Build two-column minute choices for cooldown or edit window."""

        current = (
            self.notification_cooldown_min
            if prefix == "cooldown"
            else self.status_edit_window_min
        )
        buttons = []
        for value in values:
            label = (
                "OFF"
                if value == 0
                else tr(self.language, "minutes", value=value)
            )
            buttons.append(
                self.settings_action_button(
                    self.selected_choice(label, current == value),
                    "notifications",
                    f"{prefix}_{value}",
                )
            )
        rows = [
            buttons[index : index + 2] for index in range(0, len(buttons), 2)
        ]
        rows.append([self.settings_back_button("notifications")])
        return rows

    def settings_test_rows(self) -> list[list[dict[str, str]]]:
        """Build explicit notification test controls."""

        action = self.settings_action_button
        return [
            [
                action(
                    tr(self.language, "button_test_loud"), "tests", "test_loud"
                ),
                action(
                    tr(self.language, "button_test_silent"),
                    "tests",
                    "test_silent",
                ),
            ],
            [
                action(
                    tr(self.language, "button_test_edit"), "tests", "test_edit"
                )
            ],
            [self.settings_back_button("notifications")],
        ]

    def settings_voltage_rows(self) -> list[list[dict[str, str]]]:
        """Build compact voltage threshold navigation."""

        nav = self.settings_nav_button
        action = self.settings_action_button
        return [
            [
                action(
                    tr(self.language, "button_levels"),
                    "voltage",
                    "alerts_toggle",
                )
            ],
            [
                nav("settings_section_low", "low"),
                nav("settings_section_high", "high"),
            ],
            [
                nav("settings_section_legacy_low", "legacy_low"),
                nav("settings_section_dwell", "dwell"),
            ],
            [self.settings_back_button("main")],
        ]

    def settings_low_rows(self) -> list[list[dict[str, str]]]:
        """Build low warning and critical threshold controls."""

        action = self.settings_action_button
        return [
            [
                action("Crit -5V", "low", "critlow_dec"),
                action("Crit +5V", "low", "critlow_inc"),
            ],
            [
                action("Warn -5V", "low", "warnlow_dec"),
                action("Warn +5V", "low", "warnlow_inc"),
            ],
            [self.settings_back_button("voltage")],
        ]

    def settings_high_rows(self) -> list[list[dict[str, str]]]:
        """Build high warning and critical threshold controls."""

        action = self.settings_action_button
        return [
            [
                action("Warn -5V", "high", "warnhigh_dec"),
                action("Warn +5V", "high", "warnhigh_inc"),
            ],
            [
                action("Crit -5V", "high", "crithigh_dec"),
                action("Crit +5V", "high", "crithigh_inc"),
            ],
            [self.settings_back_button("voltage")],
        ]

    def settings_legacy_low_rows(self) -> list[list[dict[str, str]]]:
        """Build the legacy low-voltage alert controls."""

        action = self.settings_action_button

        def tb(key: str, act: str, **values: object) -> dict[str, str]:
            """Build one legacy low-voltage action button."""

            text = tr(self.language, key, **values)
            return action(text, "legacy_low", act)

        return [
            [tb("button_low", "low_toggle")],
            [
                tb("button_threshold", "low_dec", delta="-5V"),
                tb("button_threshold", "low_inc", delta="+5V"),
            ],
            [
                tb("button_recovery", "rec_dec", delta="-5V"),
                tb("button_recovery", "rec_inc", delta="+5V"),
            ],
            [self.settings_back_button("voltage")],
        ]

    def settings_dwell_rows(self) -> list[list[dict[str, str]]]:
        """Build dwell and recovery-delay controls."""

        action = self.settings_action_button
        return [
            [
                action("Dwell -5s", "dwell", "dwell_dec"),
                action("Dwell +5s", "dwell", "dwell_inc"),
            ],
            [
                action("Recovery -5s", "dwell", "recdwell_dec"),
                action("Recovery +5s", "dwell", "recdwell_inc"),
            ],
            [self.settings_back_button("voltage")],
        ]

    def settings_schedule_rows(self) -> list[list[dict[str, str]]]:
        """Build schedule and status controls."""

        action = self.settings_action_button
        nav = self.settings_nav_button
        return [
            [
                action(
                    tr(self.language, "button_outage"),
                    "schedule",
                    "availability_toggle",
                )
            ],
            [
                action(
                    tr(self.language, "button_summary"),
                    "schedule",
                    "daily_toggle",
                )
            ],
            [nav("settings_section_summary_time", "summary_time")],
            [
                action(
                    tr(self.language, "button_pin"), "schedule", "pin_toggle"
                )
            ],
            [self.settings_back_button("main")],
        ]

    def settings_summary_time_rows(self) -> list[list[dict[str, str]]]:
        """Build daily summary time controls."""

        action = self.settings_action_button
        return [
            [
                action("Hour -1", "summary_time", "dailyhour_dec"),
                action("Hour +1", "summary_time", "dailyhour_inc"),
            ],
            [
                action("Minute -5", "summary_time", "dailymin_dec"),
                action("Minute +5", "summary_time", "dailymin_inc"),
            ],
            [self.settings_back_button("schedule")],
        ]

    def settings_service_rows(self) -> list[list[dict[str, str]]]:
        """Build maintenance and reset controls."""

        action = self.settings_action_button
        return [
            [
                action("🛠 Maintenance 60m", "service", "maint_60"),
                action("Maintenance OFF", "service", "maint_off"),
            ],
            [self.settings_nav_button("button_reset", "reset")],
            [self.settings_back_button("main")],
        ]

    def settings_reset_rows(self) -> list[list[dict[str, str]]]:
        """Build explicit reset confirmation controls."""

        return [
            [
                self.settings_action_button(
                    tr(self.language, "settings_reset_yes"), "service", "reset"
                )
            ],
            [self.settings_nav_button("settings_cancel", "service")],
        ]

    def settings_keyboard(self, page: str = "main") -> dict:
        """Build one compact settings keyboard page."""

        handlers = {
            "main": self.settings_main_rows,
            "device": self.settings_device_rows,
            "meter": self.settings_meter_rows,
            "notifications": self.settings_notification_rows,
            "language": self.settings_language_rows,
            "quiet": self.settings_quiet_rows,
            "cooldown": lambda: self.settings_choice_rows(
                "cooldown", "cooldown", (0, 3, 5, 10)
            ),
            "edit": lambda: self.settings_choice_rows(
                "edit", "editwindow", (3, 5, 10)
            ),
            "tests": self.settings_test_rows,
            "voltage": self.settings_voltage_rows,
            "low": self.settings_low_rows,
            "high": self.settings_high_rows,
            "legacy_low": self.settings_legacy_low_rows,
            "dwell": self.settings_dwell_rows,
            "schedule": self.settings_schedule_rows,
            "summary_time": self.settings_summary_time_rows,
            "service": self.settings_service_rows,
            "reset": self.settings_reset_rows,
        }
        if page not in handlers:
            raise ValueError("unknown settings page")
        return {"inline_keyboard": handlers[page]()}

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
        """Apply toggles, choices, and quiet-hour actions."""

        if self.apply_toggle_or_language(action, values):
            return True
        if action.startswith("cooldown_"):
            return self.apply_minute_choice(
                action,
                values,
                "cooldown_",
                "notify_cooldown",
                {"0", "3", "5", "10"},
            )
        if action.startswith("editwindow_"):
            return self.apply_minute_choice(
                action,
                values,
                "editwindow_",
                "status_edit_window",
                {"3", "5", "10"},
            )
        if action not in {"start_dec", "start_inc", "end_dec", "end_inc"}:
            return False
        key = "quiet_start" if action.startswith("start") else "quiet_end"
        step = -1 if action.endswith("dec") else 1
        values[key] = str((int(values[key]) + step) % 24)
        return True

    def apply_toggle_or_language(
        self, action: str, values: dict[str, str]
    ) -> bool:
        """Apply one boolean toggle or language selection."""

        toggles = {
            "silent_toggle": "silent",
            "quiet_toggle": "quiet_enabled",
            "low_toggle": "low_enabled",
            "alerts_toggle": "alerts_enabled",
            "availability_toggle": "availability_enabled",
            "daily_toggle": "daily_enabled",
            "pin_toggle": "pin_status",
            "instability_toggle": "instability_enabled",
        }
        if action in toggles:
            key = toggles[action]
            values[key] = "0" if values[key] == "1" else "1"
            return True
        if not action.startswith("lang_"):
            return False
        language = action.removeprefix("lang_")
        if language not in LANGUAGES:
            raise ValueError("unsupported language")
        values["language"] = language
        return True

    def apply_minute_choice(
        self,
        action: str,
        values: dict[str, str],
        prefix: str,
        key: str,
        allowed: set[str],
    ) -> bool:
        """Apply one validated minute-based settings choice."""

        minutes = action.removeprefix(prefix)
        if minutes not in allowed:
            raise ValueError("unsupported minute setting")
        values[key] = minutes
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
            previous = values[key]
            values[key] = (
                f"{max(0.0, min(300.0, float(values[key]) + step)):g}"
            )
            if key == "alert_dwell" and values["warning_dwell"] == previous:
                values["warning_dwell"] = values[key]
            return True
        return False

    def apply_warning_dwell_setting(
        self, action: str, values: dict[str, str]
    ) -> bool:
        """Configure warning-only dwell, keeping critical speed unchanged."""

        if not action.startswith("warnwait_"):
            return False
        seconds = action.removeprefix("warnwait_")
        if seconds not in {"15", "60", "180", "300"}:
            raise ValueError("unsupported warning dwell choice")
        values["warning_dwell"] = seconds
        return True

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
            or self.apply_warning_dwell_setting(action, values)
            or self.apply_level_setting(action, values)
        ):
            raise ValueError("unknown settings action")
        self.persist_settings(values, original, user_id)
        if original.get("pin_status") != values.get("pin_status"):
            self.sync_status_pin_setting()
        if original.get("language") != values.get("language"):
            self.telegram.set_commands(self.language)
            self.last_telegram = 0.0
            self.last_sent_connectivity = None
            self.update_status()

    def run_notification_test(self, action: str) -> None:
        """Run an explicit private Telegram delivery/edit test."""

        if action == "test_loud":
            self.telegram.send_test(tr(self.language, "test_loud"), True)
            return
        if action == "test_silent":
            self.telegram.send_test(tr(self.language, "test_silent"), False)
            return
        if action != "test_edit":
            raise ValueError("unknown notification test")
        message_id = self.telegram.send_test(
            tr(self.language, "test_edit_start"), False
        )
        self.telegram.edit(message_id, tr(self.language, "test_edit_done"))

    def handle_callback(self, update: dict) -> None:
        """Handle one localized admin settings callback."""

        query = update.get("callback_query") or {}
        if str(query.get("data", "")).startswith("apt:list:"):
            self.handle_apartment_callback(query)
            return
        if str(query.get("data", "")).startswith("menu:"):
            self.handle_menu_callback(query)
            return
        if str(query.get("data", "")).startswith("view:"):
            self.handle_status_callback(query)
            return
        context = self.settings_callback_context(query)
        if context is None:
            return
        callback_id, user_id, message, action = context
        try:
            page, setting = self.settings_callback_action(action)
            if setting is not None:
                self.apply_settings_callback(setting, user_id)
                self.telegram.answer_callback(
                    callback_id, tr(self.language, "saved")
                )
            else:
                self.telegram.answer_callback(callback_id)
            self.telegram.edit(
                int(message["message_id"]),
                self.settings_page_text(page),
                self.settings_keyboard(page),
            )
        except Exception as exc:
            logger.warning("Telegram settings callback failed: {}", exc)
            self.telegram.answer_callback(
                callback_id, tr(self.language, "settings_error")
            )

    def settings_callback_action(self, action: str) -> tuple[str, str | None]:
        """Return target page and optional mutation from callback data."""

        if action.startswith("nav:"):
            return action.split(":", 1)[1], None
        if action.startswith("set:"):
            parts = action.split(":", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                raise ValueError("invalid settings callback")
            return parts[1], parts[2]
        return "main", action

    def settings_callback_context(self, query: dict) -> tuple | None:
        """Validate one settings callback and return its execution context."""

        message = query.get("message") or {}
        if (
            str(message.get("chat", {}).get("id", ""))
            != self.cfg.telegram_chat_id
        ):
            return None
        callback_id = str(query.get("id", ""))
        user_id = str((query.get("from") or {}).get("id", ""))
        if not self.is_admin(user_id):
            self.telegram.answer_callback(
                callback_id, tr(self.language, "only_admin")
            )
            return None
        data = str(query.get("data", ""))
        if not data.startswith("cfg:"):
            self.telegram.answer_callback(callback_id)
            return None
        return callback_id, user_id, message, data.split(":", 1)[1]

    def apply_settings_callback(self, action: str, user_id: str) -> None:
        """Run a settings mutation or explicit notification test."""

        if action.startswith("test_"):
            self.run_notification_test(action)
            return
        self.update_setting(action, user_id)

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
            self.maybe_finalize_voltage_episode()
            self.maybe_rotate_stable_recovery_status()
            self.update_status()
            self.run_periodic_tasks()
            STOP.wait(self.cfg.poll_interval)

    def run_periodic_tasks(self) -> None:
        """Run independent jobs despite one Telegram or SQLite failure."""

        jobs = (
            ("daily_summary", self.maybe_daily_summary),
            ("weekly_private", self.maybe_weekly_private_reports),
            ("database", self.maybe_database_maintenance),
        )
        for name, task in jobs:
            try:
                task()
            except Exception as exc:
                logger.warning(
                    "Periodic {} failed: {}", name, type(exc).__name__
                )

    def maybe_database_maintenance(self) -> None:
        """Run rollup/checkpoint/optimize no more than once per day."""

        now = time.time()
        if now - self.last_db_maintenance < 86400:
            return
        attempt = time.monotonic()
        if attempt - self.last_db_maintenance_attempt < 300.0:
            return
        self.last_db_maintenance_attempt = attempt
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
                self.telegram_last_poll_success = time.monotonic()
                self.telegram_consecutive_failures = 0
            except Exception as exc:
                self.telegram_failures += 1
                self.telegram_consecutive_failures += 1
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
            self.telegram.send(
                self.settings_page_text("main"), reply_markup=keyboard
            )
        except Exception as exc:
            logger.warning("Telegram settings response failed: {}", exc)

    def send_command_response(self, command: str) -> None:
        """Send one simple text response for a Telegram command."""

        text = self.command_text(command)
        if not text:
            return
        keyboard = self.main_menu_keyboard() if command == "/start" else None
        try:
            if keyboard is None:
                self.telegram.send(text)
            else:
                self.telegram.send(text, reply_markup=keyboard)
        except Exception as exc:
            logger.warning("Telegram command response failed: {}", exc)

    def main_menu_keyboard(self) -> dict:
        """Build localized navigation for the private control chat."""

        rows = (
            ("status", "events"),
            ("today", "summary"),
            ("chart", "settings"),
            ("outages", "help"),
            ("apartments", "updates"),
        )
        return {
            "inline_keyboard": [
                [
                    {
                        "text": tr(self.language, "menu_" + action),
                        "callback_data": "menu:" + action,
                    }
                    for action in row
                ]
                for row in rows
            ]
        }

    def handle_menu_callback(self, query: dict) -> None:
        """Authorize menu navigation using the control chat access rules."""

        message = query.get("message") or {}
        user = query.get("from") or {}
        if (
            str(message.get("chat", {}).get("id", ""))
            != self.cfg.telegram_chat_id
        ):
            return
        if not self.message_sender_allowed({"from": user}):
            return
        action = str(query.get("data", "")).removeprefix("menu:")
        if action not in MENU_ALLOWED_ACTIONS:
            return
        self.telegram.answer_callback(str(query.get("id", "")))
        if action == "apartments":
            self.handle_apartments_command(
                str(user.get("id", "")), ["/apartments", "list"]
            )
        elif action == "updates":
            self.handle_updates_command(str(user.get("id", "")))
        elif action == "settings":
            self.handle_settings_command(str(user.get("id", "")))
        elif action == "chart":
            self.handle_file_command("/chart", ["/chart"])
        else:
            text = self.command_text("/" + action)
            self.edit_menu_response(int(message["message_id"]), text)

    def edit_menu_response(self, message_id: int, text: str) -> None:
        """Allow repeated clicks when Telegram already has the same text."""

        try:
            self.telegram.edit(
                message_id, text, reply_markup=self.main_menu_keyboard()
            )
        except Exception as exc:
            if not message_not_modified(exc):
                raise

    def full_status_text(self) -> str:
        """Combine voltage readings with the active safe settings."""

        with self.config_lock:
            return multiphase_status_text(self) + "\n\n" + self.settings_text()

    def handle_private_admin_message(self, msg: dict) -> bool:
        """Allow administrator-only commands in a direct chat."""

        chat = msg.get("chat") or {}
        sender_id = str((msg.get("from") or {}).get("id", ""))
        if (
            chat.get("type") != "private"
            or str(chat.get("id", "")) != sender_id
            or not self.is_admin(sender_id)
        ):
            return False
        parts = str(msg.get("text", "")).split()
        if not parts:
            return False
        command = parts[0].split("@")[0].lower()
        if command == "/updates":
            self.handle_updates_command(sender_id)
        elif command == "/apartments":
            self.handle_apartments_command(sender_id, parts)
        elif command == "/schedule":
            self.telegram.send(
                self.schedule_text(), notify=False, chat_id=sender_id
            )
        elif command == "/preview":
            self.send_compact_preview(sender_id)
        elif command == "/view":
            self.change_private_preview_layout(sender_id, parts)
        elif command == "/week":
            self.handle_private_weekly(sender_id, parts)
        else:
            return False
        return True

    def handle_private_weekly(self, sender_id: str, parts: list[str]) -> None:
        """Serve manual reports and explicit weekly private subscriptions."""

        action = parts[1].lower() if len(parts) == 2 else ""
        if len(parts) == 1:
            message = self.weekly_summary_text()
        elif action not in {"subscribe", "unsubscribe"}:
            message = tr(self.language, "weekly_private_usage")
        else:
            try:
                self.save_private_weekly_preference(
                    sender_id, action == "subscribe"
                )
                key = (
                    "weekly_private_enabled"
                    if action == "subscribe"
                    else "weekly_private_disabled"
                )
                message = tr(self.language, key)
            except sqlite3.Error as exc:
                logger.warning(
                    "Weekly private preference failed: {}",
                    type(exc).__name__,
                )
                message = tr(self.language, "weekly_private_failed")
        self.telegram.send(message, notify=False, chat_id=sender_id)

    def save_private_weekly_preference(
        self, sender_id: str, enabled: bool
    ) -> None:
        """Persist one opt-in with an audit record, never global settings."""

        key = f"telegram_weekly_private_enabled_{sender_id}"
        value = "1" if enabled else "0"
        with self.config_lock:
            old = self.storage.get_meta(key) or "0"
            if value == old:
                return
            self.storage.set_meta(key, value)
            self.storage.audit_setting(
                sender_id, "private_weekly_digest", old, value
            )

    def private_preview_layout(self, sender_id: str) -> str:
        """Read one administrator's saved view without changing the channel."""

        if not self.storage.path.is_file():
            return "compact"
        try:
            value = self.storage.get_meta(
                f"telegram_private_preview_layout_{sender_id}"
            )
        except sqlite3.Error:
            return "compact"
        return value if value in {"compact", "detailed"} else "compact"

    def change_private_preview_layout(
        self, sender_id: str, parts: list[str]
    ) -> None:
        """Persist a validated private-chat view for this administrator."""

        layout = parts[1].lower() if len(parts) == 2 else ""
        if layout not in {"compact", "detailed"}:
            message = tr(self.language, "preview_layout_usage")
            self.telegram.send(message, notify=False, chat_id=sender_id)
            return
        try:
            with self.config_lock:
                old = self.private_preview_layout(sender_id)
                if old != layout:
                    key = f"telegram_private_preview_layout_{sender_id}"
                    self.storage.set_meta(key, layout)
                    self.storage.audit_setting(
                        sender_id, "private_preview_layout", old, layout
                    )
        except sqlite3.Error as exc:
            logger.warning(
                "Private preview preference failed: {}", type(exc).__name__
            )
            self.telegram.send(
                tr(self.language, "preview_layout_failed"),
                notify=False,
                chat_id=sender_id,
            )
            return
        self.send_compact_preview(
            sender_id,
            tr(
                self.language,
                "preview_layout_saved",
                mode=tr(self.language, "preview_layout_" + layout),
            ),
        )

    def send_compact_preview(self, sender_id: str, notice: str = "") -> None:
        """Send the selected private status silently, never to the channel."""

        try:
            layout = self.private_preview_layout(sender_id)
            message = (
                self.full_status_text()
                if layout == "detailed"
                else compact_preview_text(self)
            )
            if notice:
                message = notice + "\n\n" + message
            self.telegram.send(message, notify=False, chat_id=sender_id)
        except Exception as exc:
            logger.warning(
                "Private status preview failed: {}", exc.__class__.__name__
            )

    def handle_update(self, update: dict) -> None:
        """Handle one authorized Telegram command."""

        msg = update.get("message") or update.get("channel_post")
        if not msg:
            return
        if self.handle_private_admin_message(msg):
            return
        if str(msg["chat"]["id"]) != self.cfg.telegram_chat_id:
            return
        if not self.message_sender_allowed(msg):
            return
        parts = str(msg.get("text", "")).split()
        if not parts:
            return
        command = parts[0].split("@")[0].lower()
        sender_id = str((msg.get("from") or {}).get("id", ""))
        if command == "/schedule":
            return  # Private admin only, never a public-channel reply.
        if command == "/week" and len(parts) != 1:
            return
        if command in {"/chart", "/export"}:
            self.handle_file_command(command, parts)
        elif command in {"/quality", "/history", "/outages"}:
            self.handle_phase_command(command, parts)
        elif command == "/apartments":
            self.handle_apartments_command(sender_id, parts)
        elif command == "/updates":
            self.handle_updates_command(sender_id)
        elif command == "/maintenance":
            self.handle_maintenance(sender_id, parts)
        elif command == "/settings":
            self.handle_settings_command(sender_id)
        else:
            self.send_command_response(command)

    def handle_updates_command(self, sender_id: str) -> None:
        """Send release information only to a Telegram administrator."""

        if not sender_id or not self.is_admin(sender_id):
            return
        try:
            message = release_notice(VERSION, self.language)
        except (ValueError, OSError, KeyError) as exc:
            logger.warning(
                "GitHub release check failed: {}", exc.__class__.__name__
            )
            message = tr(self.language, "updates_check_failed")
        try:
            self.telegram.send(message, notify=False, chat_id=sender_id)
        except Exception as exc:
            logger.warning(
                "Private update check response failed: {}",
                exc.__class__.__name__,
            )

    def apartment_phase_keyboard(self) -> dict:
        """Build a phase selector for administrator private messages."""

        phases = ["all", *self.active_phases()]
        return {
            "inline_keyboard": [
                [
                    {
                        "text": phase.upper() if phase != "all" else "All",
                        "callback_data": "apt:list:" + phase,
                    }
                    for phase in phases
                ]
            ]
        }

    def handle_apartment_callback(self, query: dict) -> None:
        """Show a selected phase only in the administrator private chat."""

        user_id = str((query.get("from") or {}).get("id", ""))
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        if not self.is_admin(user_id):
            return
        if str(chat.get("id", "")) != user_id:
            return
        phase = str(query.get("data", "")).removeprefix("apt:list:")
        if phase != "all" and phase not in self.active_phases():
            return
        parts = ["/apartments", "list"]
        if phase != "all":
            parts.append(phase)
        registry = ApartmentRegistry(self.cfg.database_path)
        registry.initialize()
        response = apartment_command(
            registry, parts, set(self.active_phases())
        )
        self.telegram.answer_callback(str(query.get("id", "")))
        self.telegram.edit(
            int(message["message_id"]),
            response,
            reply_markup=self.apartment_phase_keyboard(),
            chat_id=user_id,
        )

    def handle_apartments_command(
        self, sender_id: str, parts: list[str]
    ) -> None:
        """Manage apartment mappings in administrator direct messages."""

        if not sender_id or not self.is_admin(sender_id):
            return
        registry = ApartmentRegistry(self.cfg.database_path)
        try:
            registry.initialize()
            active = set(self.active_phases())
            response = apartment_command(registry, parts, active)
        except (ValueError, sqlite3.Error) as exc:
            response = f"Apartment mapping error: {exc}"
        try:
            self.telegram.send(
                response,
                notify=False,
                chat_id=sender_id,
                reply_markup=self.apartment_phase_keyboard(),
            )
        except Exception as exc:
            logger.warning(
                "Apartment mapping private response failed: {}",
                exc.__class__.__name__,
            )

    def command_phase_hours(self, parts: list[str]) -> tuple[str, int]:
        """Parse optional phase and hour arguments for export/chart."""

        phase = parts[1].upper() if len(parts) > 1 else "L1"
        phase = "L1" if phase == "L" else phase
        hours = int(parts[2]) if len(parts) > 2 else 24
        if not self.phase_configured(phase):
            raise ValueError("phase is not active for the selected model")
        if phase not in {"L1", "L2", "L3"}:
            raise ValueError("phase must be L1, L2 or L3")
        return phase, max(1, min(hours, 168))

    def handle_phase_command(self, command: str, parts: list[str]) -> None:
        """Handle phase-aware text analytics commands."""

        phase = parts[1].upper() if len(parts) > 1 else "L1"
        phase = "L1" if phase == "L" else phase
        if phase not in {"L1", "L2", "L3"}:
            self.telegram.send(tr(self.language, "phase_invalid"))
            return
        if not self.phase_configured(phase):
            self.telegram.send(
                tr(self.language, "phase_not_configured", phase=phase)
            )
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
            if command == "/chart":
                path = self.send_chart_file(phase, hours)
            else:
                path = self.send_export_file(phase, hours)
            path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Telegram file command failed: {}", exc)
            self.telegram.send(
                tr(self.language, "command_error", error=type(exc).__name__)
            )

    def send_chart_file(self, phase: str, hours: int) -> Path:
        """Create and send one localized chart file."""

        path = Path(f"/tmp/shelly-{phase.lower()}-{hours}h.png")
        count = chart_png(
            self.cfg.database_path,
            phase,
            hours,
            path,
            warning_low=self.warning_low,
            timezone=self.cfg.timezone,
        )
        caption = tr(
            self.language,
            "chart_caption",
            phase=self.phase_label(phase),
            hours=hours,
            count=count,
        )
        self.telegram.send_photo(path, caption)
        return path

    def send_export_file(self, phase: str, hours: int) -> Path:
        """Create and send one localized CSV export."""

        path = Path(f"/tmp/shelly-{phase.lower()}-{hours}h.csv")
        count = export_csv(
            self.cfg.database_path,
            phase,
            time.time() - hours * 3600,
            path,
        )
        caption = tr(
            self.language,
            "export_caption",
            phase=self.phase_label(phase),
            hours=hours,
            count=count,
        )
        self.telegram.send_document(path, caption)
        return path

    def handle_maintenance(self, sender_id: str, parts: list[str]) -> None:
        """Change maintenance mode for an authorized admin."""

        if not self.is_admin(sender_id):
            self.telegram.send(tr(self.language, "maintenance_admin_only"))
            return
        try:
            minutes = (
                0
                if len(parts) < 2 or parts[1].lower() == "off"
                else int(parts[1])
            )
            value = self.set_maintenance(max(0, min(minutes, 1440)))
            self.storage.audit_setting(sender_id, "maintenance", None, value)
            self.telegram.send(
                tr(self.language, "maintenance_status", value=value)
            )
        except ValueError:
            self.telegram.send(tr(self.language, "maintenance_usage"))

    def schedule_text(self) -> str:
        """Render an optional, read-only DTEK forecast."""

        return format_schedule(
            self.language,
            os.getenv("DTEK_REGION", ""),
            os.getenv("DTEK_GROUP", ""),
        )

    def command_text(self, command: str) -> str | None:
        """Render localized command responses."""

        if command == "/start":
            return tr(self.language, "menu_title")
        if command == "/help":
            return tr(self.language, "help")
        if command == "/status":
            return self.full_status_text()
        if command == "/health":
            return self.health_text()
        reports = {
            "/quality": self.quality_text,
            "/summary": self.daily_summary_text,
            "/events": self.event_timeline_text,
            "/week": self.weekly_summary_text,
            "/audit": self.audit_text,
            "/device": self.device_health_text,
            "/schedule": self.schedule_text,
        }
        if command in reports:
            return reports[command]()
        known = {"/voltage", "/today", "/minmax", "/history", "/outages"}
        if command not in known:
            return None
        if self.state.snapshot()["voltage"] is None:
            return tr(self.language, "no_current")
        if command == "/voltage":
            return (
                tr(self.language, "phases_title")
                + "\n"
                + "\n".join(phase_line(self, p) for p in self.active_phases())
            )
        if command in {"/today", "/minmax"}:
            return multiphase_status_text(self)
        if command == "/history":
            return self.history_text()
        if command == "/outages":
            return self.outage_history_text()
        return None

    def health_text(self) -> str:
        """Render health for active voltage lines and the latest backup."""

        with self.config_lock:
            phases = "; ".join(
                phase_line(self, phase) for phase in self.active_phases()
            )
            return tr(
                self.language,
                "service_ok",
                phases=phases,
                backup=self.backup_health_text(),
            )

    def history_text(self, phase: str = "L1") -> str:
        """Render localized recent measurements for one phase."""

        lines = [
            tr(self.language, "history_title", phase=self.phase_label(phase))
        ]
        for ts, voltage, frequency in self.storage.recent(12, phase):
            stamp = format_time(ts, self.cfg).split()[1]
            unit_v = tr(self.language, "unit_voltage")
            unit_f = tr(self.language, "unit_frequency")
            lines.append(
                f"{stamp} — {voltage:.1f} {unit_v} / {frequency:.2f} {unit_f}"
            )
        return "\n".join(lines)


OUTAGE_COUNT_HELP = (
    "# HELP shelly_outages_total Legacy name for Shelly connectivity "
    "losses after hysteresis; not verified mains outages."
)
OUTAGE_DURATION_HELP = (
    "# HELP shelly_outage_duration_today_seconds Legacy name for "
    "connectivity-loss duration; not electricity downtime."
)


def connectivity_metric_lines(
    outage_count: int, stats: dict[str, float]
) -> list[str]:
    """Keep legacy Prometheus series while adding clear source semantics."""

    count, seconds = stats["count"], stats["duration"]
    return [
        "# HELP shelly_connectivity_losses_total "
        "Shelly connection-loss transitions after hysteresis.",
        f"shelly_connectivity_losses_total {outage_count}",
        "# HELP shelly_connectivity_losses_today "
        "Shelly connection-loss intervals today.",
        f"shelly_connectivity_losses_today {count}",
        "# HELP shelly_connectivity_loss_duration_today_seconds "
        "Connectivity-loss duration, not measured mains downtime.",
        f"shelly_connectivity_loss_duration_today_seconds {seconds:.3f}",
        OUTAGE_COUNT_HELP,
        f"shelly_outages_total {outage_count}",
        "# HELP shelly_outages_today Legacy connectivity-loss count today.",
        f"shelly_outages_today {count}",
        OUTAGE_DURATION_HELP,
        f"shelly_outage_duration_today_seconds {seconds:.3f}",
    ]


def live_metric_value(snap: dict[str, object], key: str) -> str:
    """Return a live metric value or NaN when the sample is stale."""

    if snap["connectivity"] not in {"online", "recovering"}:
        return "NaN"
    value = snap[key]
    return "NaN" if value is None else str(value)


def phase_metrics(monitor: Monitor, phase: str) -> list[str]:
    """Render Prometheus metrics for one configured phase."""

    if not monitor.phase_configured(phase):
        return [f'shelly_phase_configured{{phase="{phase}"}} 0']
    snap = monitor.phase_states[phase].snapshot()
    voltage = live_metric_value(snap, "voltage")
    frequency = live_metric_value(snap, "frequency")
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
        f'shelly_connectivity_losses_total{{phase="{phase}"}} '
        f"{snap['outage_count']}",
        f"shelly_connectivity_state{{{state_labels}}} 1",
    ]


def telegram_poll_success_age(monitor: Monitor) -> float | None:
    """Report age of last completed Telegram command poll, if known."""

    last = monitor.telegram_last_poll_success
    return None if last is None else max(0.0, time.monotonic() - last)


def metrics_text(monitor: Monitor) -> str:
    """Render legacy-compatible and three-phase Prometheus metrics."""
    snap = monitor.state.snapshot()
    now = time.time()
    last = snap["last_success"]
    age = -1 if last is None else max(0.0, now - float(last))
    voltage = live_metric_value(snap, "voltage")
    frequency = live_metric_value(snap, "frequency")
    outage_stats = monitor.storage.outage_stats(now, "L1")
    maintenance = int(monitor.maintenance_active())
    loop_age = time.monotonic() - monitor.last_loop_tick
    poll_age = telegram_poll_success_age(monitor)
    poll_age_metric = "NaN" if poll_age is None else f"{poll_age:.3f}"
    lines = [
        f"shelly_voltage_volts {voltage}",
        f"shelly_frequency_hz {frequency}",
        f"shelly_last_success_age_seconds {age:.3f}",
        *connectivity_metric_lines(snap["outage_count"], outage_stats),
        f"shelly_telegram_enabled {int(monitor.telegram.enabled)}",
        f"shelly_telegram_failures_total {monitor.telegram_failures}",
        "shelly_telegram_consecutive_failures "
        f"{monitor.telegram_consecutive_failures}",
        f"shelly_telegram_poll_success_age_seconds {poll_age_metric}",
        f"shelly_maintenance_active {maintenance}",
        f"shelly_monitor_loop_age_seconds {loop_age:.3f}",
    ]
    for state in ("starting", "online", "recovering", "offline"):
        active = 1 if snap["connectivity"] == state else 0
        lines.append(f'shelly_connectivity{{state="{state}"}} {active}')
    for phase in ("L1", "L2", "L3"):
        lines.extend(phase_metrics(monitor, phase))
    return "\n".join(lines) + "\n"


def add_live_measurement_fields(
    payload: dict[str, object], stale_after: float
) -> None:
    """Expose current readings without changing last-known history fields."""

    sample_at = payload["last_success"]
    age = None if sample_at is None else max(0.0, time.time() - sample_at)
    fresh = (
        payload["connectivity"] in {"online", "recovering"}
        and age is not None
        and age <= stale_after
    )
    payload["sample_age_seconds"] = age
    payload["sample_is_stale"] = not fresh
    payload["live_voltage"] = payload["voltage"] if fresh else None
    payload["live_frequency"] = payload["frequency"] if fresh else None


def phase_service_snapshot(monitor: Monitor, phase: str) -> dict[str, object]:
    """Add safe current values for each configured voltage phase."""

    if not monitor.phase_configured(phase):
        return {"connectivity": "not_configured"}
    phase_data = monitor.phase_states[phase].snapshot()
    add_live_measurement_fields(phase_data, monitor.cfg.stale_after)
    return phase_data


def service_snapshot(monitor: Monitor) -> dict[str, object]:
    """Return liveness plus phase/watchdog state for HTTP diagnostics."""

    payload = monitor.state.snapshot()
    add_live_measurement_fields(payload, monitor.cfg.stale_after)
    payload["phases"] = {
        phase: phase_service_snapshot(monitor, phase)
        for phase in ("L1", "L2", "L3")
    }
    payload["line_labels"] = monitor.phase_labels().split(", ")
    payload["watchdog"] = {
        "loop_age": time.monotonic() - monitor.last_loop_tick,
        "telegram_failures": monitor.telegram_failures,
        "telegram_consecutive_failures": monitor.telegram_consecutive_failures,
        "telegram_poll_success_age_seconds": telegram_poll_success_age(
            monitor
        ),
    }
    payload["maintenance"] = monitor.maintenance_active()
    return payload


def dashboard_rows(monitor: Monitor) -> list[dict[str, object]]:
    """Show only fresh live measurements in local dashboard cards."""

    rows = []
    for phase in monitor.active_phases():
        label = monitor.phase_label(phase)
        snap = phase_service_snapshot(monitor, phase)
        if snap["connectivity"] == "not_configured":
            rows.append(
                {"phase": label, "state": "not configured", "voltage": None}
            )
            continue
        state = snap["connectivity"]
        if snap["sample_is_stale"] and state in {"online", "recovering"}:
            state = "stale"
        rows.append(
            {
                "phase": label,
                "state": state,
                "voltage": snap["live_voltage"],
                "frequency": snap["live_frequency"],
                "sample_age_seconds": snap["sample_age_seconds"],
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
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
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


def telegram_destinations_check(cfg: Config) -> int:
    """Read-only Telegram API check without showing IDs or sending posts."""

    data = safe_telegram_doctor(Telegram(cfg))
    status = data.get("status")
    if status == "disabled":
        print("TELEGRAM_DESTINATIONS=DISABLED")
        return 0
    if status != "ok":
        error = data.get("error_type", "invalid_response")
        print(f"TELEGRAM_DESTINATIONS=FAIL reason={error}")
        return 2
    control = data.get("control_chat_id")
    target = data.get("status_chat_id")
    kind = data.get("status_chat_type")
    valid_type = kind in {"private", "group", "supergroup", "channel"}
    if not data.get("bot") or control in (None, "", "None"):
        print("TELEGRAM_DESTINATIONS=FAIL reason=incomplete_api_reply")
        return 2
    if target in (None, "", "None") or not valid_type:
        print("TELEGRAM_DESTINATIONS=FAIL reason=incomplete_api_reply")
        return 2
    separate = "yes" if control != target else "no"
    print(
        "TELEGRAM_DESTINATIONS=PASS "
        f"bot=ok control=ok status=ok type={kind} separate={separate}"
    )
    print("Subscriber-visible message delivery is not verified.")
    return 0


def channel_membership_permissions(member: object) -> str:
    """Classify the bot's channel post/edit rights without user data."""

    if not isinstance(member, dict):
        return "invalid_membership"
    if member.get("status") == "creator":
        return "pass"
    if member.get("status") != "administrator":
        return "not_administrator"
    if member.get("can_post_messages") is not True:
        return "posting_not_allowed"
    if member.get("can_edit_messages") is not True:
        return "editing_not_allowed"
    return "pass"


def telegram_channel_rights_check(cfg: Config) -> int:
    """Check own channel permissions without sending any messages."""

    bot = Telegram(cfg)
    if not bot.enabled:
        print("TELEGRAM_CHANNEL_RIGHTS=DISABLED")
        return 0
    try:
        identity = bot.get_me()
        destination = bot.get_chat(bot.status_chat_id)
        user_id = identity.get("id") if isinstance(identity, dict) else None
        if type(user_id) is not int or user_id <= 0:
            raise ValueError("invalid bot identity")
        if not isinstance(destination, dict):
            raise ValueError("invalid status destination")
        if destination.get("type") != "channel":
            print("TELEGRAM_CHANNEL_RIGHTS=SKIP reason=not_channel")
            return 2
        member = bot.call(
            "getChatMember",
            {"chat_id": bot.status_chat_id, "user_id": user_id},
        )
        state = channel_membership_permissions(member)
    except Exception as exc:
        print(f"TELEGRAM_CHANNEL_RIGHTS=FAIL reason={type(exc).__name__}")
        return 2
    if state != "pass":
        print(f"TELEGRAM_CHANNEL_RIGHTS=FAIL reason={state}")
        return 2
    print("TELEGRAM_CHANNEL_RIGHTS=PASS post=yes edit=yes")
    print("Subscriber-visible message delivery is not verified.")
    return 0


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
            "telegram-check",
            "telegram-channel-rights",
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
    if args.command == "telegram-check":
        return telegram_destinations_check(cfg)
    if args.command == "telegram-channel-rights":
        return telegram_channel_rights_check(cfg)
    install_signals()
    Monitor(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
