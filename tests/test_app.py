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
        monitor = APP.Monitor(config())
        monitor.state.restore(APP.time.time(), 186.1, 50.0)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 179.3, "max": 220.0, "avg": 205.0}
        )
        monitor.last_voltage_event = (
            "recovered",
            186.1,
            50.0,
            APP.datetime(
                2026, 11, 2, 7, 9, tzinfo=APP.ZoneInfo("Europe/Kyiv")
            ).timestamp(),
            "L1",
        )
        text = APP.multiphase_status_text(monitor)
        self.assertTrue(
            text.startswith("✅ Напряжение восстановилось · 07:09")
        )
        self.assertIn("186.1 V · 50.00 Hz", text.splitlines()[1])

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
                )
            )
            monitor.storage.initialize()
            monitor.state.restore(900.0, 260.0, 50.0)
            monitor.storage.add(900.0, 260.0, 50.0, "L1")
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.telegram.send = Mock(side_effect=[101, 102, 103])
            monitor.telegram.delete = Mock()
            with patch.object(
                APP.time, "time", side_effect=[1000.0, 1060.0, 1301.0]
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
            monitor.telegram.delete = Mock(
                side_effect=lambda *args, **kwargs: calls.append("delete")
            )
            monitor.record_voltage_event("warning_high", 254.0, 50.0)
            self.assertEqual(calls, ["send", "delete"])
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
        monitor.storage.get_meta = Mock(return_value="123")
        monitor.storage.set_meta = Mock()
        self.assertEqual(monitor.telegram_offset(), 123)
        monitor.remember_telegram_offset(124)
        monitor.storage.set_meta.assert_called_once_with(
            "telegram_update_offset", "124"
        )

    def test_command_acl_and_empty_message(self):
        monitor = APP.Monitor(
            config(
                telegram_token="token",
                telegram_chat_id="-100123",
                telegram_allowed_user_ids=("7",),
            )
        )
        monitor.telegram.send = Mock()
        denied = {
            "message": {
                "chat": {"id": -100123},
                "from": {"id": 8},
                "text": "/help",
            }
        }
        allowed = {
            "message": {
                "chat": {"id": -100123},
                "from": {"id": 7},
                "text": "/help",
            }
        }
        empty = {
            "message": {
                "chat": {"id": -100123},
                "from": {"id": 7},
                "text": "",
            }
        }
        monitor.handle_update(denied)
        monitor.handle_update(empty)
        monitor.telegram.send.assert_not_called()
        monitor.handle_update(allowed)
        monitor.telegram.send.assert_called_once()

    def test_availability_alert_is_sent_once_on_offline_transition(self):
        monitor = APP.Monitor(
            config(
                telegram_token="token",
                telegram_chat_id="-100123",
                availability_alerts_enabled=True,
            )
        )
        monitor.storage.initialize()
        monitor.telegram.send = Mock(return_value=1)
        monitor.shelly.read = Mock(side_effect=RuntimeError("unreachable"))
        monitor.storage.begin_outage = Mock(return_value=True)
        monitor.poll_once()
        monitor.poll_once()
        monitor.poll_once()
        monitor.poll_once()
        monitor.storage.begin_outage.assert_called_once()
        monitor.telegram.send.assert_called_once()
        self.assertIn("недоступен", monitor.telegram.send.call_args.args[0])

    def _recovery_monitor(self, path: Path):
        """Build a monitor with an open outage and mocked Telegram."""

        monitor = APP.Monitor(
            config(
                database_path=path,
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-100123",
                availability_alerts_enabled=True,
            )
        )
        monitor.storage.initialize()
        started = APP.time.time() - 60
        monitor.storage.begin_outage(started)
        monitor.state.restore_outage(started, 1)
        monitor.storage.set_meta("telegram_status_message_id", "675")
        monitor.shelly.read = Mock(return_value=(230.0, 50.0, 5.0))
        monitor.telegram.delete = Mock()
        monitor.telegram.send = Mock(return_value=900)
        monitor.telegram.edit = Mock()
        monitor.next_prune = APP.time.monotonic() + 1000
        return monitor

    def test_recovery_rotates_channel_status_message(self):
        """Replace the old status message after confirmed recovery."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite3"
            monitor = self._recovery_monitor(path)
            monitor.poll_once()
            self.assertEqual(
                monitor.state.snapshot()["connectivity"], "recovering"
            )
            monitor.telegram.delete.assert_not_called()
            monitor.poll_once()
            self.assertEqual(
                monitor.state.snapshot()["connectivity"], "online"
            )
            monitor.telegram.delete.assert_called_once_with(
                675, monitor.telegram.status_chat_id
            )
            self.assertEqual(
                monitor.storage.get_meta("telegram_status_message_id"), "900"
            )
            self.assertIsNotNone(monitor.last_recovery_duration)
            sent_text = monitor.telegram.send.call_args.args[0]
            self.assertIn("Питание восстановлено", sent_text)

    def test_confirmed_outage_clears_old_voltage_event(self):
        """Do not keep an old recovery header during a new outage."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "outage-event.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            monitor.last_voltage_event = (
                "recovered",
                186.1,
                50.0,
                100.0,
                "L1",
            )
            monitor.storage.set_meta(
                "last_voltage_event",
                '{"kind":"recovered","voltage":186.1,'
                '"frequency":50.0,"timestamp":100.0}',
            )
            for _ in range(monitor.cfg.offline_after_failures):
                monitor.record_poll_failure(RuntimeError("unreachable"))
            self.assertIsNone(monitor.last_voltage_event)
            self.assertIsNone(monitor.storage.get_meta("last_voltage_event"))

    def test_secondary_outage_clears_matching_voltage_event(self):
        """Clear an L2 voltage header when L2 becomes unavailable."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secondary-outage.sqlite3"
            monitor = APP.Monitor(
                config(database_path=path, phase_l2_host="192.0.2.20")
            )
            monitor.storage.initialize()
            monitor.last_voltage_event = ("low", 179.0, 50.0, 100.0, "L2")
            monitor.storage.set_meta(
                "last_voltage_event",
                '{"kind":"low","voltage":179.0,"frequency":50.0,'
                '"timestamp":100.0,"phase":"L2"}',
            )
            state = monitor.phase_states["L2"]
            for _ in range(monitor.cfg.offline_after_failures):
                monitor.record_secondary_failure(
                    "L2", state, RuntimeError("unreachable")
                )
            self.assertIsNone(monitor.last_voltage_event)
            self.assertIsNone(monitor.storage.get_meta("last_voltage_event"))

    def test_outages_command_uses_daily_journal(self):
        monitor = APP.Monitor(config())
        monitor.state.restore(100.0, 220.0, 50.0)
        monitor.storage.outage_stats = Mock(
            return_value={"count": 2, "duration": 3900.0}
        )
        with patch.object(
            APP, "outage_history", side_effect=APP.sqlite3.Error("fixture")
        ):
            text = monitor.command_text("/outages")
        self.assertIn("2", text)
        self.assertIn("1ч 5м", text)
        self.assertIn("Подтверждённая недоступность", text)
        self.assertIn("3 ошибок", text)
        self.assertIn("2 успешных опросов", text)

    def test_deleted_status_message_is_recreated(self):
        monitor = self._telegram_monitor()
        monitor.storage.get_meta = Mock(return_value="42")
        monitor.storage.set_meta = Mock()
        monitor.telegram.edit.side_effect = RuntimeError(
            "message to edit not found"
        )

        monitor.update_status()

        monitor.telegram.send.assert_called_once()
        monitor.storage.set_meta.assert_called_once_with(
            "telegram_message_id", "99"
        )


class RestartObservabilityTests(unittest.TestCase):
    """Restore persisted outage observability after process restart."""

    def test_initialize_restores_last_closed_outage_with_open_outage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            self.assertTrue(storage.begin_outage(100.0))
            self.assertEqual(storage.end_outage(140.0), 40.0)
            self.assertTrue(storage.begin_outage(200.0))
            monitor = APP.Monitor(config(database_path=path))
            with patch.object(APP, "start_health_server"):
                monitor.initialize()
            snap = monitor.state.snapshot()
            self.assertEqual(snap["outage_count"], 2)
            self.assertEqual(snap["connectivity"], "offline")
            self.assertEqual(snap["offline_since"], 200.0)
            self.assertEqual(snap["last_outage_duration"], 40.0)


class CliTests(unittest.TestCase):
    """Validate config-independent CLI commands."""

    def test_version_does_not_load_runtime_config(self):
        with (
            patch.object(sys, "argv", ["app.py", "version"]),
            patch.object(
                APP,
                "load_config",
                side_effect=AssertionError("config must not load"),
            ),
            patch("builtins.print") as printer,
        ):
            result = APP.main()
        self.assertEqual(result, 0)
        expected = (
            (Path(__file__).resolve().parents[1] / "VERSION")
            .read_text()
            .strip()
        )
        printer.assert_called_once_with(expected)

    def test_db_check_does_not_load_shelly_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.sqlite3"
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            with (
                patch.object(sys, "argv", ["app.py", "db-check"]),
                patch.dict(
                    os.environ, {"DATABASE_PATH": str(path)}, clear=False
                ),
                patch.object(
                    APP,
                    "load_config",
                    side_effect=AssertionError("config must not load"),
                ),
                patch("builtins.print") as printer,
            ):
                result = APP.main()
            self.assertEqual(result, 0)
            printer.assert_called_once_with("ok")

    def test_telegram_discover_lists_chat_without_exposing_token(self):
        cfg = config(telegram_token="secret-token")
        updates = [
            {
                "update_id": 12,
                "message": {
                    "chat": {
                        "id": 12345,
                        "type": "private",
                        "first_name": "User",
                    },
                    "text": "/start",
                },
            }
        ]
        with (
            patch.object(
                APP.Telegram, "get_me", return_value={"username": "bot"}
            ),
            patch.object(APP.Telegram, "call", return_value=updates),
            patch("builtins.print") as printer,
        ):
            result = APP.telegram_discover(cfg)
        payload = json.loads(printer.call_args.args[0])
        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["chats"][0]["chat_id"], "12345")
        self.assertNotIn("secret-token", printer.call_args.args[0])

    def test_telegram_discover_waits_when_no_updates(self):
        cfg = config(telegram_token="secret-token")
        with (
            patch.object(
                APP.Telegram, "get_me", return_value={"username": "bot"}
            ),
            patch.object(APP.Telegram, "call", return_value=[]),
            patch("builtins.print") as printer,
        ):
            result = APP.telegram_discover(cfg)
        payload = json.loads(printer.call_args.args[0])
        self.assertEqual(result, 2)
        self.assertEqual(payload["status"], "waiting_for_update")
        self.assertEqual(payload["chats"], [])

    def test_db_check_missing_database_is_fail_closed_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"
            with (
                patch.object(sys, "argv", ["app.py", "db-check"]),
                patch.dict(
                    os.environ, {"DATABASE_PATH": str(path)}, clear=False
                ),
                patch.object(
                    APP,
                    "load_config",
                    side_effect=AssertionError("config must not load"),
                ),
                patch("sys.stderr"),
            ):
                result = APP.main()
            self.assertEqual(result, 2)
            self.assertFalse(path.exists())


class DoctorTests(unittest.TestCase):
    """Validate partial diagnostics during a real-world Shelly outage."""

    def test_doctor_continues_when_shelly_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = config(database_path=Path(directory) / "doctor.sqlite3")
            with (
                patch.object(
                    APP.ShellyClient,
                    "read",
                    side_effect=RuntimeError("offline"),
                ),
                patch("builtins.print") as printer,
            ):
                result = APP.doctor(cfg)
            payload = json.loads(printer.call_args.args[0])
            self.assertEqual(result, 2)
            self.assertEqual(payload["shelly"]["status"], "error")
            self.assertEqual(payload["sqlite"]["status"], "ok")
            self.assertEqual(payload["sqlite"]["quick_check"], "ok")
            self.assertEqual(payload["telegram"]["status"], "disabled")


class ConnectivityStateTests(unittest.TestCase):
    """Protect outage hysteresis and last-good restoration."""

    def test_offline_and_recovery_hysteresis(self):
        state = APP.RuntimeState()
        state.success(230.0, 50.0, 10.0, recovery_after=2)
        self.assertEqual(state.snapshot()["connectivity"], "online")
        state.error("one", offline_after=3)
        state.error("two", offline_after=3)
        self.assertEqual(state.snapshot()["connectivity"], "online")
        state.error("three", offline_after=3)
        self.assertEqual(state.snapshot()["connectivity"], "offline")
        state.success(231.0, 50.0, 9.0, recovery_after=2)
        self.assertEqual(state.snapshot()["connectivity"], "recovering")
        state.success(232.0, 50.0, 8.0, recovery_after=2)
        snap = state.snapshot()
        self.assertEqual(snap["connectivity"], "online")
        self.assertEqual(snap["outage_count"], 1)

    def test_restore_keeps_last_good_without_marking_online(self):
        state = APP.RuntimeState()
        state.restore(123.0, 219.5, 50.0)
        snap = state.snapshot()
        self.assertEqual(snap["last_success"], 123.0)
        self.assertEqual(snap["voltage"], 219.5)
        self.assertEqual(snap["connectivity"], "starting")

    def test_storage_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = APP.Storage(
                Path(directory) / "data.sqlite3", "Europe/Kyiv"
            )
            storage.initialize()
            self.assertIsNone(storage.latest())
            storage.add(10.0, 220.0, 50.0)
            storage.add(20.0, 221.0, 49.9)
            self.assertEqual(storage.latest(), (20.0, 221.0, 49.9))

    def test_offline_status_hides_last_valid_sample(self):
        state = APP.RuntimeState()
        state.restore(1793435408.0, 214.0, 50.0)
        state.error("unreachable", offline_after=1)
        storage = Mock()
        text = APP.status_text(state, storage, config())
        self.assertIn("Shelly: недоступен", text)
        self.assertNotIn("214.0 V", text)
        self.assertNotIn("50.00 Hz", text)
        self.assertIn("Последние данные", text)

    def test_multiphase_status_hides_offline_voltage_then_recovers(self):
        monitor = APP.Monitor(config(recovery_after_successes=2))
        monitor.state.restore(1793435408.0, 214.0, 50.0)
        monitor.state.error("unreachable", offline_after=1)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 214.0, "max": 214.0, "avg": 214.0}
        )
        text = APP.multiphase_status_text(monitor)
        self.assertIn("🔴 L1: недоступна", text)
        self.assertNotIn("L1: 214.0 V", text)
        monitor.state.success(228.5, 50.0, 4.0, recovery_after=2)
        text = APP.multiphase_status_text(monitor)
        self.assertIn("🟡 L1: 228.5 V (восстанавливается)", text)

    def test_readiness_distinguishes_offline_from_liveness(self):
        monitor = APP.Monitor(config())
        monitor.state.restore(1793435408.0, 214.0, 50.0)
        monitor.state.error("unreachable", offline_after=1)
        code, payload = APP.readiness(monitor)
        self.assertEqual(code, 503)
        self.assertEqual(payload["reason"], "shelly offline")
        self.assertEqual(payload["connectivity"], "offline")

    def test_metrics_keep_recovering_sample_live(self):
        monitor = APP.Monitor(config(recovery_after_successes=2))
        monitor.state.restore(1793435408.0, 214.0, 50.0)
        monitor.state.error("unreachable", offline_after=1)
        monitor.state.success(228.5, 50.0, 4.0, recovery_after=2)
        monitor.storage.outage_stats = Mock(
            return_value={"count": 1, "duration": 30.0}
        )
        metrics = APP.metrics_text(monitor)
        self.assertIn("shelly_voltage_volts 228.5", metrics)
        self.assertIn("shelly_frequency_hz 50.0", metrics)
        self.assertIn('shelly_connectivity{state="recovering"} 1', metrics)

    def test_cold_start_blackout_reports_shelly_offline(self):
        monitor = APP.Monitor(config())
        monitor.state.error("unreachable", offline_after=1)
        code, payload = APP.readiness(monitor)
        self.assertEqual(code, 503)
        self.assertEqual(payload["reason"], "shelly offline")
        self.assertEqual(payload["connectivity"], "offline")
        self.assertNotIn("age", payload)

    def test_metrics_expose_connectivity(self):
        monitor = APP.Monitor(config())
        monitor.state.restore(1793435408.0, 214.0, 50.0)
        monitor.state.error("unreachable", offline_after=1)
        monitor.storage.outage_stats = Mock(
            return_value={"count": 1, "duration": 30.0}
        )
        metrics = APP.metrics_text(monitor)
        self.assertIn('shelly_connectivity{state="offline"} 1', metrics)
        self.assertIn("shelly_voltage_volts NaN", metrics)
        self.assertIn("shelly_frequency_hz NaN", metrics)
        self.assertIn('shelly_voltage_volts{phase="L1"} NaN', metrics)
        self.assertIn("shelly_outages_total 1", metrics)
        self.assertIn("shelly_outages_today 1", metrics)
        self.assertIn("shelly_outage_duration_today_seconds 30.000", metrics)
        self.assertIn(
            "Confirmed connectivity outages after hysteresis", metrics
        )
        self.assertIn("not exact mains-loss boundaries", metrics)


def shelly_config_values() -> dict[str, object]:
    """Return default Shelly device values used by tests."""

    return {
        "shelly_host": "192.0.2.10",
        "shelly_meter_type": "pro_em50",
        "em1_id": 0,
        "shelly_user": "admin",
        "shelly_password": "",
        "shelly_timeout": 4.0,
        "phase_l2_host": "",
        "phase_l2_em1_id": 0,
        "phase_l2_user": "admin",
        "phase_l2_password": "",
        "phase_l3_host": "",
        "phase_l3_em1_id": 0,
        "phase_l3_user": "admin",
        "phase_l3_password": "",
    }


def telegram_config_values() -> dict[str, object]:
    """Return default Telegram values used by tests."""

    return {
        "telegram_token": "",
        "telegram_chat_id": "",
        "telegram_status_chat_id": "",
        "telegram_interval": 60.0,
        "telegram_min_edit_interval": 15.0,
        "telegram_allowed_user_ids": (),
        "telegram_admin_user_ids": (),
        "persist_update_offset": True,
        "telegram_silent_default": True,
        "telegram_quiet_start_hour": 23,
        "telegram_quiet_end_hour": 7,
        "voltage_notification_cooldown_min": 5,
    }


def monitor_config_values() -> dict[str, object]:
    """Return storage, health, and connectivity values used by tests."""

    return {
        "poll_interval": 10.0,
        "significant_change": 0.5,
        "retention_days": 90,
        "database_path": Path("/tmp/svt-test.sqlite3"),
        "timezone": "Europe/Kyiv",
        "health_host": "127.0.0.1",
        "health_port": 8080,
        "stale_after": 45.0,
        "offline_after_failures": 3,
        "recovery_after_successes": 2,
        "offline_log_interval": 300.0,
    }


def alert_config_values() -> dict[str, object]:
    """Return alert, summary, and backup values used by tests."""

    return {
        "low_voltage_alert_enabled": True,
        "low_voltage_alert_v": 180.0,
        "low_voltage_recovery_v": 185.0,
        "availability_alerts_enabled": False,
        "alerts_enabled": False,
        "warning_low": 207.0,
        "warning_high": 253.0,
        "critical_low": 190.0,
        "critical_high": 255.0,
        "alert_dwell_sec": 15.0,
        "alert_recovery_dwell_sec": 30.0,
        "daily_summary_enabled": True,
        "daily_summary_hour": 22,
        "daily_summary_minute": 55,
        "backup_dir": Path("/tmp"),
        "telegram_pin_status": False,
    }


def config(**overrides):
    """Build a complete test configuration from small setting groups."""

    values = shelly_config_values()
    values.update(telegram_config_values())
    values.update(monitor_config_values())
    values.update(alert_config_values())
    values.update(overrides)
    return APP.Config(**values)


class AdvancedFeatureTests(unittest.TestCase):
    """Validate multi-phase, analytics, export and operational features."""

    def test_schema_v2_migrates_all_rows_to_l1(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2.sqlite3"
            with closing(APP.sqlite3.connect(path)) as db, db:
                db.executescript("""
                    CREATE TABLE measurements(id INTEGER PRIMARY KEY, ts REAL,
                        voltage REAL, frequency REAL);
                    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
                    INSERT INTO meta VALUES('schema_version','2');
                    INSERT INTO measurements VALUES(1,1,220,50);
                    CREATE TABLE outages(
                        id INTEGER PRIMARY KEY, started_at REAL,
                        ended_at REAL);
                    INSERT INTO outages VALUES(1,1,2);
                """)
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            with closing(APP.sqlite3.connect(path)) as db, db:
                self.assertEqual(
                    db.execute("SELECT phase FROM measurements").fetchone()[0],
                    "L1",
                )
                self.assertEqual(
                    db.execute("SELECT phase FROM outages").fetchone()[0], "L1"
                )
                self.assertEqual(storage.get_meta("schema_version"), "3")

    def test_unconfigured_phases_are_visible(self):
        monitor = APP.Monitor(config())
        monitor.state.restore(APP.time.time(), 220.0, 50.0)
        monitor.storage.day_stats = Mock(
            return_value={"count": 1, "min": 220.0, "max": 220.0, "avg": 220.0}
        )
        text = APP.multiphase_status_text(monitor)
        self.assertIn("L1: 220.0 V", text)
        self.assertIn("L2: не настроена", text)
        self.assertIn("L3: не настроена", text)

    def test_pro3em_poll_persists_three_phases_with_same_cycle(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pro3em.sqlite3"
            monitor = APP.Monitor(
                config(database_path=path, shelly_meter_type="pro3em")
            )
            monitor.storage.initialize()
            monitor.shelly.read_phases = Mock(
                return_value={
                    "L1": (230.0, 50.0, 4.0),
                    "L2": (231.0, 50.0, 4.0),
                    "L3": (232.0, 50.0, 4.0),
                }
            )
            monitor.next_prune = APP.time.monotonic() + 1000

            self.assertTrue(monitor.poll_pro3em_once())

            with closing(APP.sqlite3.connect(path)) as db, db:
                rows = db.execute(
                    "SELECT phase, voltage, ts FROM measurements "
                    "ORDER BY phase"
                ).fetchall()
            self.assertEqual([row[0] for row in rows], ["L1", "L2", "L3"])
            self.assertEqual([row[1] for row in rows], [230.0, 231.0, 232.0])
            self.assertEqual(len({row[2] for row in rows}), 1)

    def test_l2_poll_is_stored_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phase.sqlite3"
            monitor = APP.Monitor(
                config(database_path=path, phase_l2_host="192.0.2.20")
            )
            monitor.storage.initialize()
            monitor.phase_clients["L2"].read = Mock(
                return_value=(221.5, 50.0, 4.0)
            )
            self.assertTrue(monitor.poll_secondary_phase("L2"))
            self.assertEqual(monitor.storage.latest("L2")[1], 221.5)
            self.assertIsNone(monitor.storage.latest("L1"))

    def test_low_voltage_requires_dwell(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "low-dwell.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    alert_dwell_sec=15.0,
                )
            )
            monitor.storage.initialize()
            monitor.telegram.quiet_now = Mock(return_value=False)
            monitor.replace_voltage_status = Mock(return_value=True)
            with patch.object(
                APP.time, "monotonic", side_effect=[100.0, 114.0, 115.0]
            ):
                monitor.maybe_low_voltage_alert(179.0, 50.0)
                monitor.maybe_low_voltage_alert(178.0, 50.0)
                monitor.maybe_low_voltage_alert(177.0, 50.0)
            monitor.replace_voltage_status.assert_called_once()
            self.assertEqual(monitor.last_voltage_event[0], "low")

    def test_chart_and_csv_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "data.sqlite3"
            storage = APP.Storage(db_path, "Europe/Kyiv")
            storage.initialize()
            now = APP.time.time()
            for index in range(12):
                storage.add(now - (11 - index) * 60, 215.0 + index, 50.0, "L1")
            png = root / "chart.png"
            csv_path = root / "export.csv"
            points = APP.chart_png(db_path, "L1", 1, png)
            rows = APP.export_csv(db_path, "L1", now - 3600, csv_path)
            self.assertGreaterEqual(points, 2)
            self.assertTrue(png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertEqual(rows, 12)
            self.assertIn("voltage_v", csv_path.read_text().splitlines()[0])

    def test_dashboard_hides_stale_voltage_while_offline(self):
        monitor = APP.Monitor(config(recovery_after_successes=2))
        monitor.state.restore(APP.time.time(), 230.0, 50.0)
        monitor.state.error("unreachable", offline_after=1)
        rows = APP.dashboard_rows(monitor)
        self.assertEqual(rows[0]["state"], "offline")
        self.assertIsNone(rows[0]["voltage"])
        monitor.state.success(228.5, 50.0, 4.0, recovery_after=2)
        rows = APP.dashboard_rows(monitor)
        self.assertEqual(rows[0]["state"], "recovering")
        self.assertEqual(rows[0]["voltage"], 228.5)

    def test_quality_and_dashboard_are_phase_aware(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "q.sqlite3"
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            now = APP.time.time()
            for value in (170.0, 200.0, 230.0, 260.0):
                storage.add(now, value, 50.0, "L1")
            q = APP.quality_stats(path, "L1", now - 10, 207, 190, 253)
            self.assertEqual(q["samples"], 4)
            self.assertEqual(q["below_critical"], 1)
            monitor = APP.Monitor(config(database_path=path))
            monitor.state.restore(now, 230.0, 50.0)
            page = APP.dashboard_html("test", APP.dashboard_rows(monitor))
            self.assertIn("L1", page)
            self.assertIn("L2", page)
            self.assertIn("L3", page)

    def test_daily_summary_is_sent_once_per_day(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    daily_summary_hour=0,
                    daily_summary_minute=0,
                )
            )
            monitor.storage.initialize()
            monitor.storage.add(APP.time.time(), 220.0, 50.0, "L1")
            monitor.telegram.send = Mock(return_value=1)
            monitor.maybe_daily_summary()
            monitor.maybe_daily_summary()
            monitor.telegram.send.assert_called_once()

    def test_maintenance_persists_and_suppresses_low_alert(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "maint.sqlite3"
            monitor = APP.Monitor(
                config(
                    database_path=path,
                    telegram_token="token",
                    telegram_chat_id="123",
                    alert_dwell_sec=0,
                )
            )
            monitor.storage.initialize()
            monitor.telegram.send = Mock(return_value=1)
            monitor.set_maintenance(60)
            monitor.maybe_low_voltage_alert(170.0, 50.0)
            monitor.telegram.send.assert_not_called()
            self.assertGreater(
                float(monitor.storage.get_meta("maintenance_until")),
                APP.time.time(),
            )

    def test_audit_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.sqlite3"
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            storage.audit_setting("7", "quiet_start", "23", "22")
            row = storage.audit_recent(1)[0]
            self.assertEqual(row[1:], ("7", "quiet_start", "23", "22"))

    def test_l2_uses_independent_credentials(self):
        monitor = APP.Monitor(
            config(
                phase_l2_host="192.0.2.20",
                phase_l2_user="phase2",
                phase_l2_password="secret2",
            )
        )
        client = monitor.phase_clients["L2"]
        self.assertEqual(client.user, "phase2")
        self.assertEqual(client.password, "secret2")
        self.assertIsInstance(client.auth(), APP.HTTPDigestAuth)

    def test_file_upload_is_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "x.csv"
            path.write_text("a,b\n1,2\n")
            telegram = APP.Telegram(
                config(telegram_token="token", telegram_chat_id="123")
            )
            response = Mock()
            response.json.return_value = {
                "ok": True,
                "result": {"message_id": 3},
            }
            telegram.api_session.post = Mock(return_value=response)
            telegram.send_document(path, "test")
            data = telegram.api_session.post.call_args.kwargs["data"]
            self.assertEqual(data["disable_notification"], "true")

    def test_pin_and_device_health_use_read_only_calls(self):
        telegram = APP.Telegram(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-1009",
            )
        )
        telegram.call = Mock(return_value={})
        telegram.pin(77)
        payload = telegram.call.call_args.args[1]
        self.assertTrue(payload["disable_notification"])
        client = APP.ShellyClient(config())
        client.rpc = Mock(
            side_effect=[
                {"model": "SPEM", "ver": "2.1.1", "mac": "x"},
                {"uptime": 123, "unixtime": APP.time.time()},
            ]
        )
        result = client.device_health()
        self.assertEqual(result["firmware"], "2.1.1")
        self.assertEqual(
            client.rpc.call_args_list[0].args[0], "Shelly.GetDeviceInfo"
        )

    def test_settings_offer_all_supported_shelly_models(self):
        monitor = APP.Monitor(config())
        keyboard = monitor.settings_keyboard()["inline_keyboard"]
        callbacks = {
            button["callback_data"] for row in keyboard for button in row
        }
        expected = {
            "cfg:meter_em50",
            "cfg:meter_em_gen3",
            "cfg:meter_pm_mini_gen3",
            "cfg:meter_plus_pm_mini",
            "cfg:meter_plus_1pm",
            "cfg:meter_1pm_gen3",
            "cfg:meter_1pm_gen4",
            "cfg:meter_1pm_mini_gen3",
            "cfg:meter_1pm_mini_gen4",
            "cfg:meter_pro3em",
        }
        self.assertTrue(expected.issubset(callbacks))

    def test_each_single_phase_model_can_be_selected_and_persisted(self):
        actions = {
            "meter_em_gen3": "em_gen3",
            "meter_pm_mini_gen3": "pm_mini_gen3",
            "meter_plus_pm_mini": "plus_pm_mini",
            "meter_plus_1pm": "plus_1pm",
            "meter_1pm_gen3": "1pm_gen3",
            "meter_1pm_gen4": "1pm_gen4",
            "meter_1pm_mini_gen3": "1pm_mini_gen3",
            "meter_1pm_mini_gen4": "1pm_mini_gen4",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            for action, meter_type in actions.items():
                with self.subTest(meter_type=meter_type):
                    monitor.update_setting(action, "7")
                    self.assertEqual(monitor.meter_type, meter_type)
                    self.assertIsInstance(monitor.shelly, APP.RpcMeterClient)
                    self.assertEqual(
                        monitor.shelly.rpc_method, APP.METER_RPC[meter_type]
                    )
                    self.assertEqual(
                        monitor.storage.get_meta(
                            "telegram_setting_meter_type"
                        ),
                        meter_type,
                    )

    def test_admin_can_select_pro3em_backend_from_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meter-settings.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()

            monitor.update_setting("meter_pro3em", "7")

            self.assertEqual(monitor.meter_type, "pro3em")
            self.assertIsInstance(monitor.shelly, APP.Pro3EMClient)
            self.assertEqual(set(monitor.phase_states), {"L1", "L2", "L3"})
            self.assertEqual(
                monitor.storage.get_meta("telegram_setting_meter_type"),
                "pro3em",
            )
            keyboard = monitor.settings_keyboard()["inline_keyboard"]
            data = [
                button["callback_data"] for row in keyboard for button in row
            ]
            self.assertIn("cfg:meter_em50", data)
            self.assertIn("cfg:meter_pro3em", data)
            self.assertIn("Shelly Pro 3EM", monitor.settings_text())

    def test_runtime_settings_reload_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restart-settings.sqlite3"
            storage = APP.Storage(path, "Europe/Kyiv")
            storage.initialize()
            storage.set_meta("telegram_setting_meter_type", "pro3em")
            storage.set_meta("telegram_setting_silent", "0")

            monitor = APP.Monitor(config(database_path=path))
            with patch.object(APP, "start_health_server"):
                monitor.initialize()

            self.assertEqual(monitor.meter_type, "pro3em")
            self.assertIsInstance(monitor.shelly, APP.Pro3EMClient)
            self.assertFalse(monitor.telegram.silent_default)
            self.assertEqual(set(monitor.phase_states), {"L1", "L2", "L3"})

    def test_new_notification_settings_apply_at_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            monitor.update_setting("daily_toggle", "7")
            monitor.update_setting("dailyhour_inc", "7")
            monitor.update_setting("availability_toggle", "7")
            monitor.update_setting("pin_toggle", "7")
            monitor.update_setting("dwell_inc", "7")
            monitor.update_setting("cooldown_10", "7")
            self.assertFalse(monitor.daily_summary_enabled)
            self.assertEqual(monitor.daily_summary_hour, 23)
            self.assertTrue(monitor.availability_alerts_enabled)
            self.assertTrue(monitor.pin_status)
            self.assertEqual(monitor.alert_dwell, 20.0)
            self.assertEqual(monitor.notification_cooldown_min, 10)

    def test_channel_status_has_read_only_buttons(self):
        monitor = APP.Monitor(
            config(
                telegram_token="token",
                telegram_chat_id="123",
                telegram_status_chat_id="-1009",
            )
        )
        keyboard = monitor.status_keyboard()
        data = [
            button["callback_data"]
            for button in keyboard["inline_keyboard"][0]
        ]
        self.assertEqual(data, ["view:refresh", "view:today", "view:outages"])

    def test_prune_rolls_up_old_raw_samples_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "retention.sqlite3"
            monitor = APP.Monitor(config(database_path=path, retention_days=1))
            monitor.storage.initialize()
            old = APP.time.time() - 2 * 86400
            monitor.storage.add(old, 210.0, 50.0, "L1")
            monitor.next_prune = 0
            monitor.maybe_prune()
            with closing(APP.sqlite3.connect(path)) as db, db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM measurements").fetchone()[
                        0
                    ],
                    0,
                )
                self.assertEqual(
                    db.execute(
                        "SELECT COUNT(*) FROM hourly_aggregates"
                    ).fetchone()[0],
                    1,
                )

    def test_phase_metrics_and_watchdog(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.sqlite3"
            monitor = APP.Monitor(config(database_path=path))
            monitor.storage.initialize()
            monitor.state.restore(APP.time.time(), 222.0, 50.0)
            text = APP.metrics_text(monitor)
            self.assertIn('shelly_phase_configured{phase="L1"} 1', text)
            self.assertIn('shelly_phase_configured{phase="L2"} 0', text)
            snap = APP.service_snapshot(monitor)
            self.assertEqual(
                snap["phases"]["L2"]["connectivity"], "not_configured"
            )
            self.assertIn("loop_age", snap["watchdog"])


if __name__ == "__main__":
    unittest.main()
