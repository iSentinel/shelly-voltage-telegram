"""Tests for the read-only release checker and fail-closed updater gate."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import github_update as updater


class ReleaseUpdaterTests(unittest.TestCase):
    """Verify release selection, status persistence and power safety."""

    def setUp(self):
        """Create an isolated installation and monitor database."""

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.state = self.base / "state"
        self.db = self.base / "voltage.sqlite3"
        (self.root / "VERSION").write_text("0.5.12-hw\n")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE outages (ended_at REAL)")
            db.execute("CREATE TABLE meta (key TEXT, value TEXT)")
            db.execute("CREATE TABLE measurements (ts REAL, voltage REAL)")
            db.execute(
                "INSERT INTO measurements VALUES(?, 230)", (time.time(),)
            )
        self.latest = {
            "tag_name": "v0.5.13-hw",
            "draft": False,
            "prerelease": False,
            "html_url": (
                "https://github.com/iSentinel/shelly-voltage-telegram/"
                "releases/tag/v0.5.13-hw"
            ),
            "assets": [
                {"name": "shelly-voltage-telegram-0.5.13-hw.tar.gz"},
                {"name": "SHA256SUMS"},
            ],
        }

    def test_release_version_rejects_tags_and_nonsense(self):
        """Allow only explicit stable versions."""

        self.assertEqual(updater.version_tuple("v0.5.13-hw"), (0, 5, 13))
        for value in ("latest", "v0.5.13-rc1", "0.5.13;reboot", "1.2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                updater.version_tuple(value)

    def test_daily_check_does_not_install(self):
        """Report a published update without changing the installation."""

        with patch.object(
            updater, "published_release", return_value=self.latest
        ):
            status = updater.check(self.root, self.state)
        self.assertTrue(status["available"])
        self.assertFalse(status["auto_install"])
        self.assertEqual(status["installed"], "0.5.12-hw")
        self.assertEqual((self.root / "VERSION").read_text(), "0.5.12-hw\n")

    def test_release_metadata_precedes_unpublished_candidate(self):
        """Do not treat a checked-out candidate as the running release."""

        (self.root / "VERSION").write_text("0.5.13-hw\n")
        (self.root / "RELEASE_VERSION").write_text("0.5.12-hw\n")
        with patch.object(
            updater, "published_release", return_value=self.latest
        ):
            status = updater.check(self.root, self.state)
        self.assertTrue(status["available"])
        self.assertEqual(status["installed"], "0.5.12-hw")
        self.assertEqual(
            updater.installed_version(self.root, self.state), "0.5.12-hw"
        )

    def test_recorded_deployment_takes_precedence(self):
        """Compare against the running signed-image version after upgrade."""

        self.state.mkdir()
        (self.state / "deployed.json").write_text(
            json.dumps({"version": "0.5.13-hw", "digest": "sha256:abc"})
        )
        with patch.object(
            updater, "published_release", return_value=self.latest
        ):
            self.assertFalse(updater.check(self.root, self.state)["available"])

    def test_scheduled_check_respects_version_input_priority(self):
        """Use the deployed version, then release metadata, then VERSION."""
        credentials = self.base / "credentials"
        credentials.mkdir()
        deployed = credentials / "deployed.json"
        release = credentials / "RELEASE_VERSION"
        (credentials / "VERSION").write_text("0.5.10-hw\n")
        values = (
            (json.dumps({"version": "0.5.12-hw"}), "0.5.11-hw", "0.5.12-hw"),
            (updater.ABSENT_CREDENTIAL, "0.5.11-hw", "0.5.11-hw"),
            (
                updater.ABSENT_CREDENTIAL,
                updater.ABSENT_CREDENTIAL,
                "0.5.10-hw",
            ),
        )
        with patch.object(
            updater, "published_release", return_value=self.latest
        ):
            for receipt, marker, expected in values:
                deployed.write_text(receipt)
                release.write_text(marker)
                status = updater.check(self.root, self.state, credentials)
                self.assertEqual(status["installed"], expected)
                self.assertFalse(status["auto_install"])

    def test_invalid_installed_receipt_does_not_fall_back(self):
        """An existing malformed receipt is still an error."""
        self.state.mkdir()
        (self.state / "deployed.json").write_text("")
        with self.assertRaises(ValueError):
            updater.installed_version(self.root, self.state)

    def test_checker_can_write_status_outside_the_input_directory(self):
        """Create the report without writing into its read-only inputs."""
        self.state.mkdir()
        receipt = self.state / "deployed.json"
        version = json.dumps({"version": "0.5.12-hw"})
        receipt.write_text(version)
        output = self.base / "checker-state" / "status.json"
        args = [
            "github_update.py",
            "check",
            "--credentials",
            str(self.state),
            "--status-file",
            str(output),
        ]
        with (
            patch.object(sys, "argv", args),
            patch.object(
                updater, "published_release", return_value=self.latest
            ),
            patch("builtins.print"),
        ):
            self.assertEqual(updater.main(), 0)
        status = json.loads(output.read_text())
        self.assertEqual(status["installed"], "0.5.12-hw")
        self.assertFalse(status["auto_install"])
        self.assertEqual(receipt.read_text(), version)
        self.assertFalse((self.state / "status.json").exists())

    def test_refuse_unpublished_or_prerelease(self):
        """A draft or unlisted archive cannot become an update."""

        with (
            patch.object(
                updater,
                "request_json",
                return_value={**self.latest, "prerelease": True},
            ),
            self.assertRaises(ValueError),
        ):
            updater.published_release()
        with (
            patch.object(
                updater,
                "request_json",
                return_value={**self.latest, "assets": []},
            ),
            self.assertRaises(ValueError),
        ):
            updater.published_release()

    def test_host_api_rejects_oversized_or_unexpected_json(self):
        """The host checker must fail closed on untrusted JSON responses."""

        for body in (
            ["unexpected"],
            {"tag_name": "v" + "a" * 140000},
            None,
        ):
            with (
                self.subTest(body_type=type(body).__name__),
                patch("urllib.request.urlopen") as request,
                self.assertRaises(ValueError),
            ):
                request.return_value.__enter__.return_value = _JsonReply(body)
                updater.request_json(updater.API + "/latest")

    def test_host_release_rejects_invalid_asset_shapes(self):
        """Missing checksums and malformed asset names cannot be published."""

        archive = "shelly-voltage-telegram-0.5.13-hw.tar.gz"
        for assets in (
            None,
            "not-a-list",
            [{"name": archive}],
            [{"name": archive}, {"name": ["SHA256SUMS"]}],
            [{"name": archive}, {"name": {"invalid": True}}],
        ):
            with (
                self.subTest(assets=type(assets).__name__),
                patch.object(
                    updater,
                    "request_json",
                    return_value={**self.latest, "assets": assets},
                ),
                self.assertRaises(ValueError),
            ):
                updater.published_release()

    def test_host_release_rejects_nondict_and_wrong_tag_types(self):
        """Require a valid JSON object and a canonical stable tag."""

        for result in ([], None, {**self.latest, "tag_name": ["v0.5.13-hw"]}):
            with (
                patch.object(updater, "request_json", return_value=result),
                self.assertRaises((ValueError, AttributeError)),
            ):
                updater.published_release()

    def test_local_readiness_rejects_malformed_json_shape(self):
        """No install can proceed when the local health data is invalid."""

        with (
            patch("urllib.request.urlopen") as request,
            self.assertRaises(ValueError),
        ):
            request.return_value.__enter__.return_value = _JsonReply(
                [{"ready": True, "connectivity": "online"}]
            )
            updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_preflight_online_and_no_outages(self):
        """Approve a healthy local monitor without any active alarms."""

        status = {"ready": True, "connectivity": "online"}
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_preflight_rejects_offline_and_active_outage(self):
        """Never upgrade while the monitor is stale or outage is open."""

        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(
                {"ready": False, "connectivity": "offline"}
            )
            with self.assertRaises(ValueError):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO outages VALUES(NULL)")
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(
                {"ready": True, "connectivity": "online"}
            )
            with self.assertRaisesRegex(ValueError, "outage"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_preflight_rejects_active_voltage_episode(self):
        """Even online Shelly cannot approve an active voltage alarm."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO meta VALUES('voltage_episode','critical')")
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(
                {"ready": True, "connectivity": "online"}
            )
            with self.assertRaisesRegex(ValueError, "alarm"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def settled_three_phase_fixture(self) -> dict:
        """Build a finalized recovery with fresh, normal per-phase samples."""
        now = time.time()
        event_at = now - 960
        values = {
            "voltage_episode": json.dumps(
                {
                    "last_at": event_at,
                    "finalized_at": event_at + 300,
                    "transitions": [{"kind": "recovered", "at": event_at}],
                }
            ),
            "last_voltage_event": json.dumps(
                {
                    "kind": "recovered",
                    "phase": "L2",
                    "timestamp": event_at,
                }
            ),
            "voltage_recovery_stable_since": str(event_at),
        }
        phases = {}
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("ALTER TABLE measurements ADD COLUMN phase TEXT")
            db.execute("DELETE FROM measurements")
            for phase in ("L1", "L2", "L3"):
                phases[phase] = {"ready": True, "connectivity": "online"}
                db.execute(
                    "INSERT INTO measurements VALUES(?, 222, ?)", (now, phase)
                )
                values[f"voltage_detector_{phase}"] = (
                    '{"level":"normal","low":false}'
                )
            db.executemany("INSERT INTO meta VALUES(?, ?)", values.items())
        return {"ready": True, "connectivity": "online", "phases": phases}

    def test_preflight_accepts_finalized_three_phase_recovery(self):
        """A settled electrical episode does not need Telegram rotation."""
        status = self.settled_three_phase_fixture()
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_preflight_separately_blocks_unresolved_telegram_state(self):
        """An unresolved send blocks even when electrical recovery is done."""
        status = self.settled_three_phase_fixture()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES(?, '')",
                ("telegram_graphic_publish_pending",),
            )
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            with self.assertRaisesRegex(ValueError, "Telegram.*unresolved"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_preflight_blocks_unresolved_scheduled_report(self):
        """Do not downgrade while an ambiguous report may be delivered."""
        status = self.settled_three_phase_fixture()
        for key in (
            "telegram_report_pending:daily_summary_day",
            "telegram_report_pending:telegram_weekly_private_sent_7",
        ):
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("INSERT INTO meta VALUES(?,?)", (key, "period"))
            with (
                patch("urllib.request.urlopen") as request,
                self.assertRaisesRegex(ValueError, "report delivery"),
            ):
                request.return_value.__enter__.return_value = _JsonReply(
                    status
                )
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")
            with closing(sqlite3.connect(self.db)) as db, db:
                db.execute("DELETE FROM meta WHERE key=?", (key,))

    def test_preflight_checks_the_low_phase_even_if_another_is_newer(self):
        """A healthy aggregate cannot hide a phase outside the upgrade band."""
        status = self.settled_three_phase_fixture()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "UPDATE measurements SET voltage=200, ts=ts-1 WHERE phase='L2'"
            )
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            with self.assertRaisesRegex(ValueError, "upgrade band"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_finalized_episode_with_active_detector_blocks(self):
        """A finalized edit window cannot authorize an active detector."""
        status = self.settled_three_phase_fixture()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "UPDATE meta SET value=? WHERE key='voltage_detector_L3'",
                ('{"level":"normal","low":true}',),
            )
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            with self.assertRaisesRegex(ValueError, "phase detector"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_target_guard_refuses_downgrades(self):
        """Reject already installed, older or unpublished releases."""

        with patch.object(
            updater, "published_release", return_value=self.latest
        ):
            updater.guard_target(self.root, self.state, "0.5.13-hw")
            for target in ("0.5.12-hw", "0.5.11-hw"):
                with (
                    self.subTest(target=target),
                    self.assertRaises(ValueError),
                ):
                    updater.guard_target(self.root, self.state, target)

    def test_preflight_rejects_low_voltage_and_recovery(self):
        """Keep the updater idle during low voltage or recovery."""

        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE measurements SET voltage=117.5")
        status = {"ready": True, "connectivity": "online"}
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            with self.assertRaisesRegex(ValueError, "upgrade band"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("UPDATE measurements SET voltage=230")
            db.execute(
                "INSERT INTO meta VALUES('voltage_recovery_stable_since','1')"
            )
        with patch("urllib.request.urlopen") as request:
            request.return_value.__enter__.return_value = _JsonReply(status)
            with self.assertRaisesRegex(ValueError, "not yet stable"):
                updater.preflight(self.db, "http://127.0.0.1:8080/ready")

    def test_storage_preflight_accepts_sufficient_space(self):
        """Verify image and snapshot locations before pulling an image."""

        self.state.mkdir()
        docker_dir = self.base / "docker"
        docker_dir.mkdir()
        available = SimpleNamespace(free=2 * 1024**3)
        with patch.object(
            updater.shutil, "disk_usage", return_value=available
        ) as disk_usage:
            updater.storage_preflight(self.db, self.state, docker_dir)
        self.assertEqual(disk_usage.call_count, 3)

    def test_storage_preflight_refuses_low_space(self):
        """Insufficient free space must prevent any container updates."""

        self.state.mkdir()
        docker_dir = self.base / "docker"
        docker_dir.mkdir()
        available = SimpleNamespace(free=200 * 1024**2)
        with (
            patch.object(updater.shutil, "disk_usage", return_value=available),
            self.assertRaisesRegex(ValueError, "insufficient backup"),
        ):
            updater.storage_preflight(self.db, self.state, docker_dir)

    def test_storage_preflight_refuses_missing_docker_path(self):
        """An invalid Docker storage path is a hard failure."""

        self.state.mkdir()
        with self.assertRaisesRegex(ValueError, "Docker filesystem"):
            updater.storage_preflight(
                self.db, self.state, self.base / "missing-docker"
            )

    def test_atomic_status_output(self):
        """Status is valid JSON after atomic replacement."""

        file = self.state / "status.json"
        updater.atomic_json(file, {"installed": "0.5.12-hw"})
        updater.atomic_json(file, {"installed": "0.5.13-hw"})
        self.assertEqual(
            json.loads(file.read_text())["installed"], "0.5.13-hw"
        )


class _JsonReply:
    """Provide minimal local HTTP JSON in tests."""

    def __init__(self, content):
        """Encode the test response."""

        self.content = json.dumps(content).encode()

    def read(self, *_args):
        """Return the full response payload."""

        return self.content


if __name__ == "__main__":
    unittest.main()
