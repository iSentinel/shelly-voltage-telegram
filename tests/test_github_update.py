"""Tests for the read-only release checker and fail-closed updater gate."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
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
        self.latest = {
            "tag_name": "v0.5.13-hw",
            "draft": False,
            "prerelease": False,
            "html_url": (
                "https://github.com/iSentinel/shelly-voltage-telegram/"
                "releases/tag/v0.5.13-hw"
            ),
            "assets": [{"name": "shelly-voltage-telegram-0.5.13-hw.tar.gz"}],
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
