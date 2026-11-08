"""Read-only EM-50 release preflight, including fail-closed manual gates."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from test_yasno_handoff import fixture

from yasno_handoff import handoff

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_readiness", ROOT / "scripts/em50-release-preflight.py"
)
assert SPEC and SPEC.loader
GATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GATE)


def sqlite_fixture(path: Path) -> None:
    """Create a small private stand-in with status metadata."""

    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE measurements (voltage REAL)")
        db.execute("INSERT INTO measurements VALUES (220.0)")
        db.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        db.execute(
            "INSERT INTO meta VALUES (?,?)",
            ("telegram_status_message_id", "SENSITIVE_FAKE_ID"),
        )
        db.commit()


def backup_fixture(directory: Path) -> Path:
    """Write one synthetic backup and its matching SHA-256 manifest."""

    path = directory / "voltage-20261108-000000.sqlite3.gz"
    path.write_bytes(b"NON_PRODUCTION_BACKUP_FIXTURE")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(
        f"{digest}  {path}\n", encoding="utf-8"
    )
    return path


class Em50ReleasePreflightTests(unittest.TestCase):
    """Ensure automated PASS never fabricates release authorization."""

    def test_health_url_restricts_external_hosts(self):
        """Never follow arbitrary hosts or transport protocols."""

        with patch.object(GATE.urllib.request, "build_opener") as opener:
            for url in (
                "https://example.com/health",
                "http://127.0.0.1.evil.example/health",
                "http://127.0.0.1/admin",
                "http://user:secret@127.0.0.1/health",
            ):
                with (
                    self.subTest(url=url),
                    self.assertRaisesRegex(ValueError, "local HTTP"),
                ):
                    GATE.local_json(url)
            opener.assert_not_called()

    def test_health_redirects_are_rejected(self):
        """A localhost gateway cannot redirect the check elsewhere."""

        request = urllib.request.Request("http://127.0.0.1:8080/health")
        with self.assertRaisesRegex(ValueError, "redirects"):
            GATE.DenyRedirect().redirect_request(
                request,
                None,
                302,
                "Moved",
                {},
                "https://example.com/health",
            )

    def test_health_needs_fresh_sample_and_ready_true(self):
        """HTTP 200 alone cannot declare a healthy monitoring service."""

        now = time.time()
        fresh = {
            "connectivity": "online",
            "version": "0.5.13-hw",
            "last_success": now - 5,
        }
        with patch.object(
            GATE,
            "local_json",
            side_effect=[fresh, {"ready": True}],
        ):
            self.assertIn(
                "runtime=0.5.13-hw",
                GATE.health_check("local-health", "local-ready", now),
            )
        with (
            patch.object(
                GATE,
                "local_json",
                side_effect=[fresh, {"ready": False}],
            ),
            self.assertRaisesRegex(ValueError, "ready"),
        ):
            GATE.health_check("local-health", "local-ready", now)
        with (
            patch.object(
                GATE,
                "local_json",
                side_effect=[
                    {**fresh, "last_success": now - 121},
                    {"ready": True},
                ],
            ),
            self.assertRaisesRegex(ValueError, "stale"),
        ):
            GATE.health_check("local-health", "local-ready", now)

    def test_sqlite_check_does_not_mutate_or_leak_ids(self):
        """Confirm metadata existence but do not display real identifiers."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.sqlite3"
            sqlite_fixture(path)
            old = path.read_bytes()
            message = GATE.sqlite_check(path)
            self.assertEqual(path.read_bytes(), old)
            self.assertIn("integrity=ok", message)
            self.assertIn("status_id_saved=true", message)
            self.assertNotIn("SENSITIVE_FAKE_ID", message)

    def test_latest_backup_sha256_and_age(self):
        """Detect a modified backup without restoring anything."""

        with tempfile.TemporaryDirectory() as tmp:
            path = backup_fixture(Path(tmp))
            result = GATE.backup_check(Path(tmp), time.time(), 26)
            self.assertIn("sha256=ok", result)
            path.write_bytes(b"CORRUPTED")
            with self.assertRaisesRegex(ValueError, "checksum"):
                GATE.backup_check(Path(tmp), time.time(), 26)

    def test_recipient_cache_enforces_read_only_roles(self):
        """The preflight checks protected handoff ownership and freshness."""

        with tempfile.TemporaryDirectory() as tmp:
            source, target, gid = fixture(tmp)
            handoff(str(source), str(target), "10.1", gid, "install")
            self.assertIn(
                "source=YASNO",
                GATE.reader_cache_check(target, "10.1", gid),
            )
            target.chmod(0o666)
            with self.assertRaisesRegex(ValueError, "mode"):
                GATE.reader_cache_check(target, "10.1", gid)

    def test_candidate_must_remain_unpublished(self):
        """Do not confuse stable releases and unapproved source candidates."""

        self.assertIn(
            "stable_marker_unchanged=true",
            GATE.version_check(ROOT),
        )

    def test_manual_release_gates_never_auto_pass(self):
        """No test run can certify unobserved real Telegram visibility."""

        args = argparse.Namespace(
            root=str(ROOT),
            health_url="http://127.0.0.1:8080/health",
            ready_url="http://127.0.0.1:8080/ready",
            db="fake",
            backup_dir="fake",
            cache="fake",
            group="10.1",
            recipient_gid=10001,
            max_age_hours=26,
        )
        with (
            patch.object(GATE, "health_check", return_value="PASS"),
            patch.object(GATE, "sqlite_check", return_value="PASS"),
            patch.object(GATE, "backup_check", return_value="PASS"),
            patch.object(GATE, "reader_cache_check", return_value="PASS"),
        ):
            report = GATE.inspect(args)
        self.assertTrue(report["automated_pass"])
        self.assertFalse(report["release_authorized"])
        self.assertEqual(len(report["manual_pending"]), 3)

    def test_cli_exit_codes_distinguish_failure_from_manual_block(self):
        """Return 1 on automated failures and 2 for pending human gates."""

        for result, expected in ((True, 2), (False, 1)):
            report = {
                "automated_pass": result,
                "release_authorized": False,
                "checks": {},
                "manual_pending": list(GATE.PENDING),
            }
            with (
                self.subTest(automated=result),
                patch.object(sys, "argv", ["preflight.py", "--json"]),
                patch.object(GATE, "inspect", return_value=report),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(GATE.main(), expected)
                parsed = json.loads(output.getvalue())
                self.assertFalse(parsed["release_authorized"])


if __name__ == "__main__":
    unittest.main()
