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
    title = tr(monitor.language, f"event_{kind}")
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
         