"""Unit tests for the hardware-validated single-file service."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

APP_PATH = Path(
    os.getenv("SVT_APP_PATH", Path(__file__).resolve().parents[1] / "app.py")
)
SPEC = importlib.util.spec_from_file_location("svt_app", APP_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot load /app/app.py")
APP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = APP
SPEC.loader.exec_module(APP)


class ConfigTests(unittest.TestCase):
    """Validate configuration and secret-file behavior."""

    def test_secret_file_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "token"
            secret.write_text("abc123\n")
            with patch.dict(
                os.environ,
                {
                    "TEST_DIRECT": "",
                    "TEST_FILE": str(secret),
                },
                clear=False,
            ):
                self.assertEqual(
                    APP.secret_setting("TEST_DIRECT", "TEST_FILE"),
                    "abc123",
                )

    def test_timeout_validation(self):
        with (
            patch.dict(
                os.environ,
                {
                    "SHELLY_HOST": "192.0.2.10",
                    "SHELLY_TIMEOUT_SEC": "0",
                },
                clear=False,
            ),
            self.assertRaisesRegex(ValueError, "SHELLY_TIMEOUT_SEC"),
        ):
            APP.load_config()


class ShellyClientTests(unittest.TestCase):
    """Validate RPC request construction and configured timeout."""

    def test_read_uses_configured_timeout(self):
        cfg = config(shelly_timeout=7.5)
        client = APP.ShellyClient(cfg)
        response = Mock()
        response.json.return_value = {"voltage": 231.2, "freq": 50.0}
        response.raise_for_status.return_value = None
        client.session.get = Mock(return_value=response)

        voltage, frequency, _latency = client.read()

        self.assertEqual(voltage, 231.2)
        self.assertEqual(frequency, 50.0)
        self.assertEqual(client.session.get.call_args.kwargs["timeout"], 7.5)
        self.assertEqual(
            client.session.get.call_args.kwargs["params"], {"id": 0}
        )
        self.assertEqual(
            client.session.get.call_args.args[0],
            "http://192.0.2.10/rpc/EM1.GetStatus",
        )

    def test_digest_auth_is_used_only_with_password(self):
        plain = APP.ShellyClient(config(shelly_password=""))
        self.assertIsNone(plain.auth())

        protected = APP.ShellyClient(
            config(shelly_user="monitor", shelly_password="secret-marker")
        )
        auth = protected.auth()
        self.assertIsInstance(auth, APP.HTTPDigestAuth)
        self.assertEqual(auth.username, "monitor")
        self.assertEqual(auth.password, "secret-marker")

        response = Mock()
        response.json.return_value = {"voltage": 229.9, "freq": 50.0}
        response.raise_for_status.return_value = None
        protected.session.get = Mock(return_value=response)
        protected.read()
        call = protected.session.get.call_args
        self.assertNotIn("secret-marker", call.args[0])
        self.assertIsInstance(call.kwargs["auth"], APP.HTTPDigestAuth)

    def test_single_phase_model_rpc_mapping(self):
        mapping = {
            "em_gen3": "EM1.GetStatus",
            "pm_mini_gen3": "PM1.GetStatus",
            "plus_pm_mini": "PM1.GetStatus",
            "plus_1pm": "Switch.GetStatus",
            "1pm_gen3": "Switch.GetStatus",
            "1pm_gen4": "Switch.GetStatus",
            "1pm_mini_gen3": "Switch.GetStatus",
            "1pm_mini_gen4": "Switch.GetStatus",
        }
        for meter_type, method in mapping.items():
            with self.subTest(meter_type=meter_type):
                client = APP.RpcMeterClient(
                    config(shelly_meter_type=meter_type), method
                )
                client.rpc = Mock(
                    return_value={"voltage": 229.5, "freq": 50.0}
                )
                voltage, frequency, _ = client.read()
                self.assertEqual((voltage, frequency), (229.5, 50.0))
                client.rpc.assert_called_once_with(method, {"id": 0})

    def test_single_phase_rpc_rejects_missing_measurement(self):
        client = APP.RpcMeterClient(config(), "PM1.GetStatus")
        client.rpc = Mock(return_value={"voltage": None, "freq": 50.0})
        with self.assertRaisesRegex(RuntimeError, "no voltage/frequency"):
            client.read()

    def test_pro3em_reads_all_phases_from_one_em_status(self):
        client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
        client.rpc = Mock(
            return_value={
                "a_voltage": 231.1,
                "a_freq": 49.98,
                "b_voltage": 229.8,
                "b_freq": 50.01,
                "c_voltage": 232.4,
                "c_freq": 50.00,
            }
        )

        result = client.read_phases()

        client.rpc.assert_called_once_with("EM.GetStatus", {"id": 0})
        self.assertEqual(result["L1"][:2], (231.1, 49.98))
        self.assertEqual(result["L2"][:2], (229.8, 50.01))
        self.assertEqual(result["L3"][:2], (232.4, 50.0))

    def test_pro3em_common_component_errors_fail_closed(self):
        """Block three-phase samples despite plausible voltage readings."""

        for code in (
            "power_meter_failure",
            "phase_sequence",
            "ct_type_not_set",
        ):
            with self.subTest(code=code):
                client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
                client.rpc = Mock(
                    return_value={
                        "a_voltage": 230.0,
                        "a_freq": 50.0,
                        "b_voltage": 231.0,
                        "b_freq": 50.0,
                        "c_voltage": 232.0,
                        "c_freq": 50.0,
                        "errors": [code],
                    }
                )
                with self.assertRaisesRegex(RuntimeError, code):
                    client.read_phases()

    def test_pro3em_phase_errors_fail_closed(self):
        """An individual phase error also invalidates the RPC cycle."""

        for field in ("a_errors", "b_errors", "c_errors", "n_errors"):
            with self.subTest(field=field):
                client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
                client.rpc = Mock(
                    return_value={
                        "a_voltage": 230.0,
                        "a_freq": 50.0,
                        "b_voltage": 231.0,
                        "b_freq": 50.0,
                        "c_voltage": 232.0,
                        "c_freq": 50.0,
                        field: ["out_of_range:voltage"],
                    }
                )
                with self.assertRaisesRegex(RuntimeError, field):
                    client.read_phases()

    def test_pro3em_malformed_errors_fail_closed(self):
        """Do not silently ignore malformed common fault fields."""

        for raw in ("power_meter_failure", None, {"invalid": "shape"}):
            with self.subTest(error_type=type(raw).__name__):
                client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
                client.rpc = Mock(return_value={"errors": raw})
                with self.assertRaisesRegex(RuntimeError, "invalid_format"):
                    client.read_phases()

    def test_pro3em_unknown_errors_are_redacted(self):
        """Unknown error text must reject samples without leaking data."""

        marker = "private-device-marker"
        client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
        client.rpc = Mock(return_value={"errors": [marker]})
        with self.assertRaisesRegex(RuntimeError, "unknown_error") as err:
            client.read_phases()
        self.assertNotIn(marker, str(err.exception))

    def test_pro3em_empty_errors_and_flags_remain_valid(self):
        """Empty errors and separate alarm flags are not meter failures."""

        client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
        client.rpc = Mock(
            return_value={
                "a_voltage": 230.0,
                "a_freq": 50.0,
                "b_voltage": 231.0,
                "b_freq": 50.0,
                "c_voltage": 232.0,
                "c_freq": 50.0,
                "errors": [],
                "a_errors": [],
                "b_errors": [],
                "c_errors": [],
                "a_flags": ["undervoltage"],
            }
        )
        readings = client.read_phases()
        self.assertEqual(sorted(readings), ["L1", "L2", "L3"])

    def test_pro3em_legacy_error_field_fails_closed(self):
        """Do not silently trust nonstandard legacy status fields."""

        client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
        client.rpc = Mock(
            return_value={"total_errors": ["power_meter_failure"]}
        )
        with self.assertRaisesRegex(RuntimeError, "total_errors"):
            client.read_phases()

    def test_pro3em_skips_missing_phase_instead_of_zero(self):
        client = APP.Pro3EMClient(config(shelly_meter_type="pro3em"))
        client.rpc = Mock(
            return_value={
                "a_voltage": 230.0,
                "a_freq": 50.0,
                "b_voltage": None,
                "b_freq": 50.0,
                "c_voltage": 231.0,
                "c_freq": 50.0,
            }
        )

        result = client.read_phases()

        self.assertEqual(set(result), {"L1", "L3"})

    def test_production_code_contains_no_shelly_control_rpc(self):
        source = APP_PATH.read_text()
        forbidden = (
            "Switch.Set",
            "Switch.Toggle",
            "Shelly.Reboot",
            "Sys.SetConfig",
            "EM1.SetConfig",
            "EMData.SetConfig",
        )
        for rpc in forbidden:
            self.assertNotIn(rpc, source)


class StorageTests(unittest.TestCase):
    """Validate SQLite schema, samples, stats, and pruning."""

    def test_storage_stats_and_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = APP.Storage(
                Path(directory) / "data.sqlite3", "Europe/Kyiv"
            )
            storage.initialize()
            now = 1793389200.0
            storage.add(now, 220.0, 50.0)
            storage.add(now + 1, 230.0, 50.0)
            storage.add(now + 2, 240.0, 50.0)
            stats = storage.day_stats(now + 2)

            self.assertEqual(storage.integrity(), "ok")
            self.assertEqual(storage.get_meta("schema_version"), "3")
            self.assertEqual(stats["count"], 3)
            self.assertEqual(stats["min"], 220.0)
            self.assertEqual(stats["max"], 240.0)
            self.assertEqual(stats["avg"], 230.0)


class OutageStorageTests(unittest.TestCase):
    """Validate persistent outage lifecycle and daily statistics."""

    def test_schema_v1_migrates_without_losing_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            with closing(APP.sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE measurements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL, voltage REAL NOT NULL,
                        frequency REAL NOT NULL
                    );
                    CREATE TABLE meta (
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    INSERT INTO measurements(ts, voltage, frequency)
                    VALUES(100, 219.5, 50.0);
                    INSERT INTO meta(key, value) VALUES('schema_version', '1');
                    """
                )
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            self.assertEqual(storage.get_meta("schema_version"), "3")
            self.assertEqual(storage.latest(), (100.0, 219.5, 50.0))
            self.assertTrue(storage.begin_outage(200.0))

    def test_future_schema_is_rejected_without_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            with closing(APP.sqlite3.connect(path)) as db, db:
                db.executescript(
                    """
                    CREATE TABLE meta (
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    INSERT INTO meta(key, value)
                    VALUES('schema_version', '99');
                    """
                )
            storage = APP.Storage(path, "Europe/Kyiv")
            with self.assertRaises(RuntimeError):
                storage.initialize()
            with closing(APP.sqlite3.connect(path)) as db, db:
                value = db.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
            self.assertEqual(value, "99")

    def test_overnight_outage_counts_today(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = APP.Storage(
                Path(directory) / "data.sqlite3", "Europe/Kyiv"
            )
            storage.initialize()
            zone = APP.ZoneInfo("Europe/Kyiv")
            now_dt = APP.datetime(2026, 10, 31, 10, 0, tzinfo=zone)
            prior = APP.datetime(2026, 10, 30, 23, 30, tzinfo=zone)
            self.assertTrue(storage.begin_outage(prior.timestamp()))
            stats = storage.outage_stats(now_dt.timestamp())
            self.assertEqual(stats["count"], 1)
            self.assertEqual(stats["duration"], 10 * 3600.0)

    def test_outage_journal_is_idempotent_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = APP.Storage(
                Path(directory) / "data.sqlite3", "Europe/Kyiv"
            )
            storage.initialize()
            now = 1793441400.0
            self.assertTrue(storage.begin_outage(now - 30))
            self.assertFalse(storage.begin_outage(now - 10))
            self.assertEqual(storage.outage_total(), 1)
            self.assertEqual(storage.open_outage_started(), now - 30)
            stats = storage.outage_stats(now)
            self.assertEqual(stats["count"], 1)
            self.assertEqual(stats["duration"], 30.0)
            self.assertEqual(storage.end_outage(now), 30.0)
            self.assertIsNone(storage.end_outage(now + 1))
            self.assertIsNone(storage.open_outage_started())

    def test_latest_closed_outage_duration_ignores_open_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = APP.Storage(
                Path(directory) / "data.sqlite3", "Europe/Kyiv"
            )
            storage.initialize()
            self.assertIsNone(storage.latest_closed_outage_duration())
            self.assertTrue(storage.begin_outage(100.0))
            self.assertEqual(storage.end_outage(130.0), 30.0)
            self.assertEqual(storage.latest_closed_outage_duration(), 30.0)
            self.assertTrue(storage.begin_outage(200.0))
            self.assertEqual(storage.latest_closed_outage_duration(), 30.0)
            self.assertEqual(storage.end_outage(250.0), 50.0)
            self.assertEqual(storage.latest_closed_outage_duration(), 50.0)


class TelegramTransportTests(unittest.TestCase):
    """Validate Telegram transport timeouts."""

    def test_identical_edit_is_successful(self):
        """An unchanged Telegram message must not interrupt command polling."""

        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        telegram.call = Mock(
            side_effect=RuntimeError("Bad Request: message is not modified")
        )
        telegram.edit(42, "same text")
        telegram.call.assert_called_once()
        self.assertEqual(telegram.call.call_args.args[0], "editMessageText")

    def test_identical_markup_clear_is_successful(self):
        """Repeated removal of inline controls is harmless."""

        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        telegram.call = Mock(
            side_effect=RuntimeError("Bad Request: message is not modified")
        )
        telegram.clear_reply_markup(42)
        self.assertEqual(
            telegram.call.call_args.args[0], "editMessageReplyMarkup"
        )

    def test_real_telegram_edit_errors_still_propagate(self):
        """Only already-current messages may be treated as success."""

        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        telegram.call = Mock(side_effect=RuntimeError("Forbidden"))
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            telegram.edit(42, "new text")
        with self.assertRaisesRegex(RuntimeError, "Forbidden"):
            telegram.clear_reply_markup(42)

    def test_get_updates_http_timeout_exceeds_long_poll(self):
        cfg = config(telegram_token="token", telegram_chat_id="123")
        telegram = APP.Telegram(cfg)
        response = Mock()
        response.json.return_value = {"ok": True, "result": []}
        telegram.updates_session.post = Mock(return_value=response)

        telegram.updates(None)

        call = telegram.updates_session.post.call_args
        self.assertEqual(call.kwargs["json"]["timeout"], 20)
        self.assertGreater(call.kwargs["timeout"], 20)


class TelegramNotificationPolicyTests(unittest.TestCase):
    """Validate silent mode, quiet hours and low-voltage alerts."""

    def _stable_recovery_monitor(self, path: Path):
        """Create one monitor ready for stable recovery rotation tests."""

        monitor = APP.Monitor(
            config(
                database_path=path,
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-100999",
            )
        )
        monitor.storage.initialize()
        monitor.state.success(230.0, 50.0, 1.0)
        monitor.storage.add(900.0, 230.0, 50.0, "L1")
        monitor.telegram.quiet_now = Mock(return_value=False)
        monitor.telegram.edit = Mock()
        monitor.telegram.unpin = Mock()
        monitor.telegram.delete = Mock()
        return monitor

    def test_quiet_hours_wrap_midnight(self):
        cfg = config()
        zone = APP.ZoneInfo(cfg.timezone)
        self.assertFalse(
            APP.telegram_quiet_hours(
                cfg, APP.datetime(2026, 11, 1, 22, 59, tzinfo=zone)
            )
        )
        self.assertTrue(
            APP.telegram_quiet_hours(
                cfg, APP.datetime(2026, 11, 1, 23, 0, tzinfo=zone)
            )
        )
        self.assertTrue(
            APP.telegram_quiet_hours(
                cfg, APP.datetime(2026, 11, 2, 6, 59, tzinfo=zone)
            )
        )
        self.assertFalse(
            APP.telegram_quiet_hours(
                cfg, APP.datetime(2026, 11, 2, 7, 0, tzinfo=zone)
            )
        )

    def test_default_messages_are_silent(self):
        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 11}}
        telegram.api_session.post = Mock(return_value=response)
        with patch.object(telegram, "quiet_now", return_value=False):
            telegram.send("status")
        payload = telegram.api_session.post.call_args.kwargs["json"]
        self.assertTrue(payload["disable_notification"])

    def test_low_voltage_can_notify_during_day(self):
        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 12}}
        telegram.api_session.post = Mock(return_value=response)
        with patch.object(telegram, "quiet_now", return_value=False):
            telegram.send("low", notify=True)
        payload = telegram.api_session.post.call_args.kwargs["json"]
        self.assertFalse(payload["disable_notification"])

    def test_quiet_hours_override_low_voltage_notification(self):
        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 13}}
        telegram.api_session.post = Mock(return_value=response)
        with patch.object(telegram, "quiet_now", return_value=True):
            telegram.send("low", notify=True)
        payload = telegram.api_session.post.call_args.kwargs["json"]
        self.assertTrue(payload["disable_notification"])

    def test_low_voltage_alert_is_one_shot_until_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "low-voltage.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    alert_dwell_sec=0,
                )
            )
            monitor.storage.initialize()
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.replace_voltage_status = Mock(return_value=True)
            monitor.maybe_low_voltage_alert(179.9, 50.0)
            monitor.maybe_low_voltage_alert(175.0, 50.0)
            self.assertEqual(monitor.replace_voltage_status.call_count, 1)
            self.assertEqual(monitor.last_voltage_event[0], "low")
            monitor.maybe_low_voltage_alert(185.0, 50.0)
            self.assertEqual(monitor.replace_voltage_status.call_count, 2)
            self.assertEqual(monitor.last_voltage_event[0], "recovered")
            self.assertFalse(monitor.low_voltage_active)

    def test_voltage_event_updates_status_channel_not_private_chat(self):
        """Send the low-voltage header to the stable status chat."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status-destination.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                    alert_dwell_sec=0,
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(APP.time.time(), 179.3, 50.0)
            monitor.storage.day_stats = Mock(
                return_value={
                    "count": 1,
                    "min": 179.3,
                    "max": 179.3,
                    "avg": 179.3,
                }
            )
            monitor.telegram.send = Mock(return_value=77)
            monitor.telegram.edit = Mock()
            monitor.maybe_low_voltage_alert(179.3, 50.0)
            self.assertEqual(monitor.telegram.send.call_count, 1)
            kwargs = monitor.telegram.send.call_args.kwargs
            self.assertEqual(kwargs["chat_id"], "-100999")
            self.assertTrue(
                monitor.telegram.send.call_args.args[0].startswith(
                    "🚨 Низкое напряжение сети"
                )
            )

    def test_voltage_event_survives_restart(self):
        """Restore the latest low-voltage event from SQLite metadata."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            monitor.update_status = Mock()
            with patch.object(APP.time, "time", return_value=1234.5):
                monitor.record_voltage_event("low", 179.3, 50.0)
            restarted = APP.Monitor(config(database_path=path))
            restarted.storage.initialize()
            restarted.restore_voltage_event()
            self.assertEqual(
                restarted.last_voltage_event,
                ("low", 179.3, 50.0, 1234.5, "L1"),
            )

    def test_legacy_voltage_event_defaults_to_l1(self):
        """Restore pre-phase event metadata as an L1 transition."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-event.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            monitor.storage.set_meta(
                "last_voltage_event",
                '{"kind":"low","voltage":179.3,'
                '"frequency":50.0,"timestamp":1234.5}',
            )
            monitor.restore_voltage_event()
            self.assertEqual(
                monitor.last_voltage_event,
                ("low", 179.3, 50.0, 1234.5, "L1"),
            )

    def test_secondary_low_voltage_uses_status_header(self):
        """Route L2 low/recovery transitions through the stable status."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secondary-event.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    shelly_meter_type="pro3em",
                    telegram_token="token",
                    telegram_chat_id="123",
                    alert_dwell_sec=0,
                )
            )
            monitor.storage.initialize()
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.replace_voltage_status = Mock(return_value=True)
            monitor.maybe_secondary_low("L2", 179.0, 50.0, 100.0)
            self.assertEqual(monitor.last_voltage_event[0], "low")
            self.assertEqual(monitor.last_voltage_event[4], "L2")
            monitor.maybe_secondary_low("L2", 186.0, 50.0, 101.0)
            self.assertEqual(monitor.replace_voltage_status.call_count, 2)
            lines = APP.voltage_event_lines(monitor)
            self.assertTrue(
                lines[0].startswith("✅ L2: Напряжение восстановилось")
            )

    def test_low_voltage_event_is_rendered_in_status_header(self):
        """Recent recovered voltage remains visible in status context."""

        monitor = APP.Monitor(config())
        stamp = APP.time.time()
        monitor.state.restore(stamp, 186.1, 50.0)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 179.3, "max": 220.0, "avg": 205.0}
        )
        monitor.last_voltage_event = ("recovered", 186.1, 50.0, stamp, "L1")
        text = APP.multiphase_status_text(monitor)
        when = APP.datetime.fromtimestamp(
            stamp, APP.ZoneInfo("Europe/Kyiv")
        ).strftime("%H:%M")
        self.assertTrue(
            text.startswith(f"✅ Напряжение восстановилось · {when}")
        )
        self.assertIn("186.1 V · 50.00 Гц", text.splitlines()[1])

    def test_old_voltage_recovery_header_is_not_repeated(self):
        """A fresh status cannot repeat an old recovery notification."""

        monitor = APP.Monitor(config())
        stamp = APP.time.time()
        monitor.state.restore(stamp, 226.0, 50.0)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 200.0, "max": 230.0, "avg": 220.0}
        )
        monitor.last_voltage_event = (
            "recovered",
            226.0,
            50.0,
            stamp - 3600,
            "L1",
        )
        text = APP.multiphase_status_text(monitor)
        self.assertNotIn("✅ Напряжение восстановилось", text)
        self.assertIn("L: 226.0 V", text)

    def test_voltage_status_notification_cooldown(self):
        """Only the first abnormal shared status is loud inside cooldown."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cooldown.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                    voltage_notification_cooldown_min=5,
                    voltage_status_edit_window_min=3,
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(900.0, 260.0, 50.0)
            monitor.storage.add(900.0, 260.0, 50.0, "L1")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(side_effect=[101, 102, 103])
            monitor.telegram.unpin = Mock()
            monitor.telegram.delete = Mock()
            with patch.object(
                APP.time, "time", side_effect=[1000.0, 1240.0, 1421.0]
            ):
                monitor.record_voltage_event("warning_high", 254.0, 50.0)
                monitor.record_voltage_event("critical_high", 256.0, 50.0)
                monitor.record_voltage_event("warning_high", 254.0, 50.0)
            notifications = [
                call.kwargs["notify"]
                for call in monitor.telegram.send.call_args_list
            ]
            self.assertEqual(notifications, [True, False, True])
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "103"
            )
            self.assertEqual(monitor.telegram.delete.call_count, 2)

    def test_voltage_status_edits_same_message_inside_window(self):
        """Edit one alert post through recovery inside the edit window."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit-window.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                    voltage_status_edit_window_min=10,
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(900.0, 230.0, 50.0)
            monitor.storage.add(900.0, 230.0, 50.0, "L1")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(return_value=301)
            monitor.telegram.edit = Mock()
            monitor.telegram.unpin = Mock()
            monitor.telegram.delete = Mock()
            with patch.object(APP.time, "time", side_effect=[1000.0, 1599.0]):
                monitor.record_voltage_event("critical_high", 256.0, 50.0)
                monitor.record_voltage_event("recovered", 230.0, 50.0)
            self.assertEqual(monitor.telegram.send.call_count, 1)
            monitor.telegram.edit.assert_called_once()
            self.assertEqual(monitor.telegram.edit.call_args.args[0], 301)
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "301"
            )
            self.assertEqual(
                monitor.storage.get_meta(
                    "voltage_status_edit_window_started_at"
                ),
                "1000.0",
            )
            monitor.telegram.delete.assert_not_called()

    def test_voltage_status_replaces_message_after_window_expires(self):
        """Create a new recovery post after the edit window has expired."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit-window-expired.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                    voltage_status_edit_window_min=10,
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(900.0, 230.0, 50.0)
            monitor.storage.add(900.0, 230.0, 50.0, "L1")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(side_effect=[401, 402])
            monitor.telegram.edit = Mock()
            monitor.telegram.unpin = Mock()
            monitor.telegram.delete = Mock()
            with patch.object(APP.time, "time", side_effect=[1000.0, 1601.0]):
                monitor.record_voltage_event("critical_high", 256.0, 50.0)
                monitor.record_voltage_event("recovered", 230.0, 50.0)
            self.assertEqual(monitor.telegram.send.call_count, 2)
            monitor.telegram.edit.assert_not_called()
            monitor.telegram.unpin.assert_called_once_with(
                401, monitor.telegram.status_chat_id
            )
            monitor.telegram.delete.assert_called_once_with(
                401, monitor.telegram.status_chat_id
            )
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "402"
            )

    def test_recovery_preserves_voltage_status_edit_window(self):
        """Keep repeated alert and recovery transitions in the same post."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edit-window-reset.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                    voltage_status_edit_window_min=10,
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(900.0, 230.0, 50.0)
            monitor.storage.add(900.0, 230.0, 50.0, "L1")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(side_effect=[501, 502])
            monitor.telegram.edit = Mock()
            monitor.telegram.unpin = Mock()
            monitor.telegram.delete = Mock()
            with patch.object(
                APP.time, "time", side_effect=[1000.0, 1060.0, 1070.0]
            ):
                monitor.record_voltage_event("critical_high", 256.0, 50.0)
                monitor.record_voltage_event("recovered", 230.0, 50.0)
                monitor.record_voltage_event("warning_high", 254.0, 50.0)
            self.assertEqual(monitor.telegram.send.call_count, 1)
            self.assertEqual(monitor.telegram.edit.call_count, 2)
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "501"
            )

    def test_recovery_shared_status_is_always_silent(self):
        """Recovery replaces the shared status without making a sound."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-silent.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(900.0, 230.0, 50.0)
            monitor.storage.add(900.0, 230.0, 50.0, "L1")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(return_value=201)
            monitor.record_voltage_event("recovered", 230.0, 50.0)
            kwargs = monitor.telegram.send.call_args.kwargs
            self.assertFalse(kwargs["notify"])

    def test_stable_recovery_waits_full_fifteen_minutes(self):
        """Keep the recovery post before the stable period expires."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-wait.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            monitor.telegram.send = Mock(return_value=301)
            with patch.object(APP.time, "time", return_value=1000.0):
                monitor.record_voltage_event("recovered", 230.0, 50.0)
            with patch.object(APP.time, "time", return_value=1899.0):
                monitor.maybe_rotate_stable_recovery_status()
            self.assertEqual(monitor.telegram.send.call_count, 1)
            monitor.telegram.delete.assert_not_called()

    def test_stable_recovery_rotates_to_silent_current_status(self):
        """Replace a 15-minute recovery post with a silent plain status."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-stable.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            calls = []
            monitor.telegram.send = Mock(
                side_effect=lambda *a, **k: calls.append("send")
                or (301 if len(calls) == 1 else 302)
            )
            monitor.telegram.unpin.side_effect = lambda *a, **k: calls.append(
                "unpin"
            )
            monitor.telegram.delete.side_effect = lambda *a, **k: calls.append(
                "delete"
            )
            monitor.storage.set_meta("last_voltage_notification_at", "777")
            with patch.object(APP.time, "time", return_value=1000.0):
                monitor.record_voltage_event("recovered", 230.0, 50.0)
            with patch.object(APP.time, "time", return_value=1900.0):
                monitor.maybe_rotate_stable_recovery_status()
            latest = monitor.telegram.send.call_args_list[-1]
            self.assertFalse(latest.kwargs["notify"])
            self.assertNotIn("восстанов", latest.args[0].lower())
            self.assertEqual(calls, ["send", "send", "unpin", "delete"])
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "302"
            )
            self.assertIsNone(monitor.last_voltage_event)
            self.assertIsNone(
                monitor.storage.get_meta(APP.RECOVERY_STATUS_STABLE_KEY)
            )
            self.assertEqual(
                monitor.storage.get_meta("last_voltage_notification_at"), "777"
            )

    def test_new_abnormal_transition_cancels_stable_recovery(self):
        """Cancel the 15-minute timer when voltage changes again."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-reset.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            monitor.telegram.send = Mock(return_value=301)
            with patch.object(APP.time, "time", side_effect=[1000.0, 1100.0]):
                monitor.record_voltage_event("recovered", 230.0, 50.0)
                monitor.record_voltage_event("warning_high", 254.0, 50.0)
            with patch.object(APP.time, "time", return_value=2100.0):
                monitor.maybe_rotate_stable_recovery_status()
            self.assertIsNone(
                monitor.storage.get_meta(APP.RECOVERY_STATUS_STABLE_KEY)
            )
            self.assertEqual(monitor.telegram.send.call_count, 1)

    def test_legacy_recovery_restores_stable_timer(self):
        """Migrate a pre-0.5.9 recovered event into the stable timer."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-recovery.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            monitor.storage.set_meta("telegram_status_message_id", "301")
            payload = {
                "kind": "recovered",
                "voltage": 230.0,
                "frequency": 50.0,
                "timestamp": 1000.0,
                "phase": "L1",
            }
            monitor.storage.set_meta(
                "last_voltage_event", APP.json.dumps(payload)
            )
            monitor.restore_voltage_event()
            monitor.restore_recovery_stability_timer()
            self.assertEqual(
                monitor.storage.get_meta(APP.RECOVERY_STATUS_STABLE_KEY),
                "1000.0",
            )

    def test_legacy_abnormal_event_does_not_restore_recovery_timer(self):
        """Do not migrate an abnormal event into the recovery timer."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-abnormal.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            monitor.storage.set_meta("telegram_status_message_id", "301")
            payload = {
                "kind": "warning_high",
                "voltage": 254.0,
                "frequency": 50.0,
                "timestamp": 1000.0,
                "phase": "L1",
            }
            monitor.storage.set_meta(
                "last_voltage_event", APP.json.dumps(payload)
            )
            monitor.restore_voltage_event()
            monitor.restore_recovery_stability_timer()
            self.assertIsNone(
                monitor.storage.get_meta(APP.RECOVERY_STATUS_STABLE_KEY)
            )

    def test_stable_recovery_timer_survives_restart(self):
        """Resume the persisted recovery timer after a process restart."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-restart.sqlite3"
            first = self._stable_recovery_monitor(path)
            first.telegram.send = Mock(return_value=301)
            with patch.object(APP.time, "time", return_value=1000.0):
                first.record_voltage_event("recovered", 230.0, 50.0)
            second = self._stable_recovery_monitor(path)
            second.telegram.send = Mock(return_value=302)
            second.restore_voltage_event()
            with patch.object(APP.time, "time", return_value=1900.0):
                second.maybe_rotate_stable_recovery_status()
            self.assertEqual(second.telegram.send.call_count, 1)
            self.assertEqual(
                second.storage.get_meta("telegram_status_message_id"), "302"
            )

    def test_stable_recovery_requires_current_normal_voltage(self):
        """Do not clear recovery text while current voltage is abnormal."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-abnormal.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            monitor.telegram.send = Mock(return_value=301)
            with patch.object(APP.time, "time", return_value=1000.0):
                monitor.record_voltage_event("recovered", 230.0, 50.0)
            monitor.state.restore(1200.0, 260.0, 50.0)
            with patch.object(APP.time, "time", return_value=2000.0):
                monitor.maybe_rotate_stable_recovery_status()
            self.assertEqual(monitor.telegram.send.call_count, 1)
            self.assertIsNotNone(
                monitor.storage.get_meta(APP.RECOVERY_STATUS_STABLE_KEY)
            )

    def test_failed_stable_recovery_publish_keeps_old_status(self):
        """Keep recovery state when silent replacement cannot be sent."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-failure.sqlite3"
            monitor = self._stable_recovery_monitor(path)
            monitor.telegram.send = Mock(return_value=301)
            with patch.object(APP.time, "time", return_value=1000.0):
                monitor.record_voltage_event("recovered", 230.0, 50.0)
            monitor.telegram.send.side_effect = RuntimeError("failed")
            with patch.object(APP.time, "time", return_value=1900.0):
                monitor.maybe_rotate_stable_recovery_status()
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "301"
            )
            self.assertIsNotNone(
                monitor.storage.get_meta(APP.RECOVERY_STATUS_STABLE_KEY)
            )
            monitor.telegram.delete.assert_not_called()

    def test_high_voltage_warning_critical_and_recovery_dwell(self):
        """Issue warnings only after their configured steady durations."""

        monitor = APP.Monitor(
            config(
                alerts_enabled=True,
                telegram_token="token",
                telegram_chat_id="123",
                alert_dwell_sec=15.0,
                alert_recovery_dwell_sec=30.0,
            )
        )
        monitor.record_voltage_event = Mock()
        self.assertEqual(monitor.runtime_voltage_level(252.9), "normal")
        self.assertEqual(monitor.runtime_voltage_level(253.0), "warning")
        self.assertEqual(monitor.runtime_voltage_level(255.0), "critical")
        readings = [
            (100.0, 253.5),
            (114.0, 253.5),
            (115.0, 253.5),
            (130.0, 255.5),
            (145.0, 255.5),
            (160.0, 250.0),
            (189.0, 250.0),
            (190.0, 250.0),
        ]
        for index, (now, voltage) in enumerate(readings):
            with patch.object(APP.time, "monotonic", return_value=now):
                monitor.maybe_alert(voltage, 50.0)
            expected = (0, 0, 1, 1, 2, 2, 2, 3)[index]
            self.assertEqual(monitor.record_voltage_event.call_count, expected)
        events = [
            call.args[0]
            for call in monitor.record_voltage_event.call_args_list
        ]
        self.assertEqual(
            events, ["warning_high", "critical_high", "recovered"]
        )

    def test_short_high_voltage_spike_does_not_alert(self):
        """Do not notify for a brief spike below the 15 second dwell."""

        monitor = APP.Monitor(
            config(
                alerts_enabled=True,
                telegram_token="token",
                telegram_chat_id="123",
                alert_dwell_sec=15.0,
            )
        )
        monitor.record_voltage_event = Mock()
        for now, voltage in ((100.0, 253.5), (110.0, 253.5), (111.0, 252.9)):
            with patch.object(APP.time, "monotonic", return_value=now):
                monitor.maybe_alert(voltage, 50.0)
        monitor.record_voltage_event.assert_not_called()
        self.assertIsNone(monitor.alert_candidate)

    def test_high_level_alert_routes_to_shared_status(self):
        """Route high-voltage transitions through shared status."""

        monitor = APP.Monitor(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                alerts_enabled=True,
                alert_dwell_sec=0,
            )
        )
        monitor.record_voltage_event = Mock()
        with patch.object(APP.time, "monotonic", side_effect=[100.0, 100.0]):
            monitor.maybe_alert(254.0, 50.0)
            monitor.maybe_alert(254.0, 50.0)
        monitor.record_voltage_event.assert_called_once_with(
            "warning_high", 254.0, 50.0
        )

    def test_failed_status_publish_keeps_previous_message(self):
        """Keep the old shared status if replacement publishing fails."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publish-failure.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                )
            )
            monitor.storage.initialize()
            monitor.storage.set_meta("telegram_status_message_id", "44")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(side_effect=RuntimeError("failed"))
            monitor.telegram.delete = Mock()
            monitor.record_voltage_event("warning_high", 254.0, 50.0)
            current = monitor.storage.get_meta("telegram_status_message_id")
            self.assertEqual(current, "44")
            monitor.telegram.delete.assert_not_called()

    def test_status_replacement_publishes_before_delete(self):
        """Publish the replacement before deleting the previous status."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publish-order.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    telegram_status_chat_id="-100999",
                )
            )
            monitor.storage.initialize()
            monitor.storage.set_meta("telegram_status_message_id", "44")
            monitor.telegram.quiet_now = Mock(return_value=False)
            calls = []
            monitor.telegram.send = Mock(
                side_effect=lambda *args, **kwargs: calls.append("send") or 55
            )
            monitor.telegram.unpin = Mock(
                side_effect=lambda *args, **kwargs: calls.append("unpin")
            )
            monitor.telegram.delete = Mock(
                side_effect=lambda *args, **kwargs: calls.append("delete")
            )
            monitor.record_voltage_event("warning_high", 254.0, 50.0)
            self.assertEqual(calls, ["send", "unpin", "delete"])
            current = monitor.storage.get_meta("telegram_status_message_id")
            self.assertEqual(current, "55")


class TelegramDestinationTests(unittest.TestCase):
    """Validate separate control-chat and status-channel destinations."""

    def test_status_chat_falls_back_to_control_chat(self):
        tg = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="123")
        )
        self.assertEqual(tg.status_chat_id, "123")

    def test_status_chat_can_be_separate_channel(self):
        tg = APP.Telegram(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-100999",
            )
        )
        self.assertEqual(tg.status_chat_id, "-100999")

    def test_send_target_override_uses_channel(self):
        tg = APP.Telegram(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-100999",
            )
        )
        response = Mock()
        response.json.return_value = {"ok": True, "result": {"message_id": 5}}
        tg.api_session.post = Mock(return_value=response)
        with patch.object(tg, "quiet_now", return_value=False):
            tg.send("status", chat_id=tg.status_chat_id)
        payload = tg.api_session.post.call_args.kwargs["json"]
        self.assertEqual(payload["chat_id"], "-100999")

    def test_update_status_uses_separate_meta_key_and_channel(self):
        monitor = APP.Monitor(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-100999",
            )
        )
        monitor.state.restore(APP.time.time(), 220.0, 50.0)
        monitor.storage.get_meta = Mock(return_value=None)
        monitor.storage.set_meta = Mock()
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 220.0, "max": 220.0, "avg": 220.0}
        )
        monitor.telegram.send = Mock(return_value=77)
        monitor.update_status()
        monitor.telegram.send.assert_called_once()
        self.assertEqual(
            monitor.telegram.send.call_args.kwargs["chat_id"], "-100999"
        )
        monitor.storage.set_meta.assert_called_with(
            "telegram_status_message_id", "77"
        )


class TelegramSettingsTests(unittest.TestCase):
    """Validate admin-only runtime Telegram settings."""

    def _monitor(self, admin_ids=("7",)):
        monitor = APP.Monitor(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                telegram_admin_user_ids=admin_ids,
            )
        )
        defaults = monitor.setting_defaults()
        monitor.storage.get_meta = Mock(
            side_effect=lambda key: defaults.get(
                key.removeprefix("telegram_setting_")
            )
            if key.startswith("telegram_setting_")
            else None
        )
        monitor.storage.set_meta = Mock()
        return monitor

    def _callback(self, data: str, message_id: int = 10) -> dict:
        """Build one authorized settings callback update."""

        return {
            "callback_query": {
                "id": "cb",
                "from": {"id": 7},
                "data": data,
                "message": {"message_id": message_id, "chat": {"id": 123}},
            }
        }

    def test_settings_command_admin_gets_keyboard(self):
        monitor = self._monitor()
        monitor.telegram.send = Mock(return_value=1)
        update = {
            "message": {
                "chat": {"id": 123},
                "from": {"id": 7},
                "text": "/settings",
            }
        }
        monitor.handle_update(update)
        kwargs = monitor.telegram.send.call_args.kwargs
        self.assertIn("reply_markup", kwargs)
        self.assertIsNotNone(kwargs["reply_markup"])

    def test_settings_command_non_admin_is_read_only(self):
        monitor = self._monitor()
        monitor.telegram.send = Mock(return_value=1)
        update = {
            "message": {
                "chat": {"id": 123},
                "from": {"id": 8},
                "text": "/settings",
            }
        }
        monitor.handle_update(update)
        self.assertIsNone(
            monitor.telegram.send.call_args.kwargs.get("reply_markup", None)
        )

    def test_non_admin_callback_cannot_change_settings(self):
        monitor = self._monitor()
        monitor.telegram.answer_callback = Mock()
        update = {
            "callback_query": {
                "id": "cb1",
                "from": {"id": 8},
                "data": "cfg:silent_toggle",
                "message": {"message_id": 9, "chat": {"id": 123}},
            }
        }
        monitor.handle_callback(update)
        monitor.storage.set_meta.assert_not_called()
        monitor.telegram.answer_callback.assert_called_once_with(
            "cb1", "Только администратор"
        )

    def test_admin_callback_persists_and_applies_setting(self):
        monitor = self._monitor()
        values = monitor.setting_defaults().copy()
        monitor.storage.get_meta.side_effect = (
            lambda key: values.get(key.removeprefix("telegram_setting_"))
            if key.startswith("telegram_setting_")
            else None
        )

        def save(key, value):
            if key.startswith("telegram_setting_"):
                values[key.removeprefix("telegram_setting_")] = value

        monitor.storage.set_meta.side_effect = save
        monitor.telegram.answer_callback = Mock()
        monitor.telegram.edit = Mock()
        update = {
            "callback_query": {
                "id": "cb2",
                "from": {"id": 7},
                "data": "cfg:silent_toggle",
                "message": {"message_id": 10, "chat": {"id": 123}},
            }
        }
        monitor.handle_callback(update)
        self.assertFalse(monitor.telegram.silent_default)
        self.assertEqual(values["silent"], "0")
        monitor.telegram.edit.assert_called_once()

    def test_compact_main_settings_has_five_buttons(self):
        monitor = self._monitor()
        rows = monitor.settings_keyboard()["inline_keyboard"]
        buttons = [button for row in rows for button in row]
        callbacks = {button["callback_data"] for button in buttons}
        self.assertEqual(len(buttons), 5)
        self.assertEqual(
            callbacks,
            {
                "cfg:nav:device",
                "cfg:nav:voltage",
                "cfg:nav:notifications",
                "cfg:nav:schedule",
                "cfg:nav:service",
            },
        )

    def test_navigation_does_not_persist_settings(self):
        monitor = self._monitor()
        monitor.telegram.answer_callback = Mock()
        monitor.telegram.edit = Mock()
        monitor.handle_callback(self._callback("cfg:nav:notifications"))
        monitor.storage.set_meta.assert_not_called()
        monitor.telegram.answer_callback.assert_called_once_with("cb")
        text = monitor.telegram.edit.call_args.args[1]
        self.assertIn("Уведомления", text)

    def test_contextual_action_stays_on_current_page(self):
        monitor = self._monitor()
        values = monitor.setting_defaults().copy()
        monitor.storage.get_meta.side_effect = (
            lambda key: values.get(key.removeprefix("telegram_setting_"))
            if key.startswith("telegram_setting_")
            else None
        )
        monitor.storage.set_meta.side_effect = (
            lambda key, value: values.__setitem__(
                key.removeprefix("telegram_setting_"), value
            )
        )
        monitor.telegram.answer_callback = Mock()
        monitor.telegram.edit = Mock()
        data = "cfg:set:notifications:silent_toggle"
        monitor.handle_callback(self._callback(data))
        self.assertEqual(values["silent"], "0")
        self.assertIn("Уведомления", monitor.telegram.edit.call_args.args[1])

    def test_reset_requires_confirmation_page(self):
        monitor = self._monitor()
        service = monitor.settings_keyboard("service")["inline_keyboard"]
        service_data = {
            button["callback_data"] for row in service for button in row
        }
        self.assertIn("cfg:nav:reset", service_data)
        self.assertNotIn("cfg:set:service:reset", service_data)
        reset = monitor.settings_keyboard("reset")["inline_keyboard"]
        reset_data = {
            button["callback_data"] for row in reset for button in row
        }
        self.assertIn("cfg:set:service:reset", reset_data)
        self.assertIn("cfg:nav:service", reset_data)

    def test_legacy_settings_callback_remains_supported(self):
        monitor = self._monitor()
        values = monitor.setting_defaults().copy()
        monitor.storage.get_meta.side_effect = (
            lambda key: values.get(key.removeprefix("telegram_setting_"))
            if key.startswith("telegram_setting_")
            else None
        )
        monitor.storage.set_meta.side_effect = (
            lambda key, value: values.__setitem__(
                key.removeprefix("telegram_setting_"), value
            )
        )
        monitor.telegram.answer_callback = Mock()
        monitor.telegram.edit = Mock()
        monitor.handle_callback(self._callback("cfg:silent_toggle"))
        self.assertEqual(values["silent"], "0")
        self.assertIn(
            "Выберите раздел", monitor.telegram.edit.call_args.args[1]
        )

    def test_compact_pages_render_all_languages(self):
        monitor = self._monitor()
        expected = {
            "ru": "Уведомления",
            "uk": "Сповіщення",
            "en": "Notifications",
        }
        for language, title in expected.items():
            with self.subTest(language=language):
                monitor.language = language
                text = monitor.settings_page_text("notifications")
                self.assertIn(title, text)

    def test_reset_restores_environment_defaults(self):
        monitor = self._monitor()
        values = monitor.setting_defaults().copy()
        values["low_v"] = "170"
        monitor.storage.get_meta.side_effect = (
            lambda key: values.get(key.removeprefix("telegram_setting_"))
            if key.startswith("telegram_setting_")
            else None
        )
        monitor.storage.set_meta.side_effect = (
            lambda key, value: values.__setitem__(
                key.removeprefix("telegram_setting_"), value
            )
        )
        monitor.update_setting("reset")
        self.assertEqual(monitor.low_voltage_alert_v, 180.0)
        self.assertEqual(values["low_v"], "180.0")


class TelegramLogicTests(unittest.TestCase):
    """Validate threshold and deleted-message detection logic."""

    def test_voltage_levels(self):
        cfg = config()
        self.assertEqual(APP.voltage_level(230.0, cfg), "normal")
        self.assertEqual(APP.voltage_level(205.0, cfg), "warning")
        self.assertEqual(APP.voltage_level(180.0, cfg), "critical")
        self.assertEqual(APP.voltage_level(256.0, cfg), "critical")

    def test_message_missing(self):
        self.assertTrue(
            APP.message_missing(RuntimeError("message to edit not found"))
        )
        self.assertFalse(
            APP.message_missing(RuntimeError("Too Many Requests"))
        )

    def test_message_not_modified(self):
        exc = RuntimeError("Bad Request: message is not modified")
        self.assertTrue(APP.message_not_modified(exc))
        self.assertFalse(APP.message_not_modified(RuntimeError("other")))

    def test_rate_limit_is_retried_once(self):
        telegram = APP.Telegram(
            config(telegram_token="token", telegram_chat_id="-100123")
        )
        limited = Mock()
        limited.json.return_value = {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 2},
        }
        success = Mock()
        success.json.return_value = {"ok": True, "result": {"id": 7}}
        telegram.api_session.post = Mock(side_effect=[limited, success])
        with patch.object(APP.time, "sleep") as sleeper:
            result = telegram.call("getMe", {})
        self.assertEqual(result, {"id": 7})
        self.assertEqual(telegram.api_session.post.call_count, 2)
        sleeper.assert_called_once_with(2.0)

    def test_user_id_parser(self):
        self.assertEqual(APP.parse_user_ids("7, 8"), ("7", "8"))
        with self.assertRaises(ValueError):
            APP.parse_user_ids("7,alice")

    def test_transport_error_does_not_expose_token(self):
        token = "redaction-marker-token"
        telegram = APP.Telegram(
            config(telegram_token=token, telegram_chat_id="-100123")
        )
        url = f"https://api.telegram.org/bot{token}/getMe"
        telegram.api_session.post = Mock(
            side_effect=APP.requests.ConnectionError(url)
        )
        with self.assertRaises(RuntimeError) as raised:
            telegram.call("getMe", {})
        self.assertIn("ConnectionError", str(raised.exception))
        self.assertNotIn(token, str(raised.exception))


class MonitorRegressionTests(unittest.TestCase):
    """Protect outage and stable-message behavior."""

    def test_failed_poll_does_not_store_fake_sample(self):
        monitor = APP.Monitor(config())
        monitor.shelly.read = Mock(side_effect=RuntimeError("unreachable"))
        monitor.storage.add = Mock()

        self.assertFalse(monitor.poll_once())
        monitor.storage.add.assert_not_called()
        self.assertEqual(monitor.state.snapshot()["last_error"], "unreachable")

    def _telegram_monitor(self):
        monitor = APP.Monitor(
            config(telegram_token="token", telegram_chat_id="-100123")
        )
        monitor.state.success(231.7, 50.0, 12.0)
        monitor.storage.day_stats = Mock(
            return_value={
                "count": 1,
                "min": 231.7,
                "max": 231.7,
                "avg": 231.7,
            }
        )
        monitor.telegram.enabled = True
        monitor.telegram.send = Mock(return_value=99)
        monitor.telegram.edit = Mock()
        return monitor

    def test_existing_status_message_is_edited(self):
        monitor = self._telegram_monitor()
        monitor.storage.get_meta = Mock(return_value="42")
        monitor.storage.set_meta = Mock()

        monitor.update_status()

        monitor.telegram.edit.assert_called_once()
        self.assertEqual(monitor.telegram.edit.call_args.args[0], 42)
        monitor.telegram.send.assert_not_called()
        monitor.storage.set_meta.assert_not_called()

    def test_message_not_modified_is_successful_update(self):
        monitor = self._telegram_monitor()
        monitor.storage.get_meta = Mock(return_value="42")
        monitor.storage.set_meta = Mock()
        monitor.telegram.edit.side_effect = RuntimeError(
            "Bad Request: message is not modified"
        )

        monitor.update_status()

        monitor.telegram.send.assert_not_called()
        self.assertEqual(monitor.last_sent_voltage, 231.7)
        self.assertEqual(monitor.last_sent_connectivity, "online")
        self.assertGreater(monitor.last_telegram, 0)

    def test_minimum_edit_interval_throttles_status(self):
        monitor = self._telegram_monitor()
        monitor.last_sent_voltage = 231.7
        monitor.last_sent_connectivity = "online"
        monitor.last_telegram = APP.time.monotonic()
        self.assertFalse(monitor.telegram_due(240.0, "offline"))

    def test_update_offset_persistence(self):
        monitor = APP.Monitor(config())
        monitor.storage.get_meta = Mock(ret