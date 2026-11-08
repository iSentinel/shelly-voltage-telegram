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
from datetime import datetime, timedelta, timezone
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

    with contextlib.closing(sqlite3.connect(path)) as db:
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


def acceptance_arguments(path: str) -> argparse.Namespace:
    """Reuse synthetic release targets for private evidence-only QA."""

    return argparse.Namespace(
        root=str(ROOT),
        health_url="http://127.0.0.1:8080/health",
        ready_url="http://127.0.0.1:8080/ready",
        db="fake",
        backup_dir="fake",
        cache="fake",
        group="10.1",
        recipient_gid=10001,
        max_age_hours=26,
        acceptance_evidence=path,
    )


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

    def test_integrated_guard_reports_stale_backup_without_secrets(self):
        """A valid nightly backup must not pass as a current rollback point."""

        with patch.object(
            GATE.importlib.util, "spec_from_file_location"
        ) as spec_for:
            spec_for.return_value = None
            result = GATE.predeploy_backup_status(
                "/nonexistent/live.sqlite3",
                "/nonexistent/backups",
                time.time(),
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["detail"], "backup_guard_unavailable")

    def test_release_preflight_includes_backup_parity_as_automatic_gate(self):
        """Predeploy parity failure must override five other green checks."""

        parameters = acceptance_arguments("")
        with (
            patch.object(GATE, "health_check", return_value="PASS"),
            patch.object(GATE, "sqlite_check", return_value="PASS"),
            patch.object(GATE, "backup_check", return_value="PASS"),
            patch.object(GATE, "reader_cache_check", return_value="PASS"),
            patch.object(
                GATE,
                "predeploy_backup_status",
                return_value={"status": "FAIL", "detail": "backup_stale"},
            ),
        ):
            report = GATE.inspect(parameters)
        self.assertFalse(report["automated_pass"])
        self.assertEqual(
            report["checks"]["predeploy_backup_parity"]["detail"],
            "backup_stale",
        )
        self.assertFalse(report["release_authorized"])

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
            patch.object(
                GATE,
                "predeploy_backup_status",
                return_value={"status": "PASS", "detail": "parity"},
            ),
            patch.object(GATE, "reader_cache_check", return_value="PASS"),
        ):
            report = GATE.inspect(args)
        self.assertTrue(report["automated_pass"])
        self.assertFalse(report["release_authorized"])
        self.assertEqual(len(report["manual_pending"]), 3)

    def test_protected_realistic_receipt_completes_only_private_gate(self):
        """Human evidence changes manual status, never release permission."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "accepted.json"
            receipt = {
                "bot_api_send": "PASS",
                "bot_api_edit": "PASS",
                "user_visible_edited_message": "PASS",
                "delivery_type": "silent private administrator chat",
                "public_channel_modified": False,
                "pinned_message_modified": False,
                "released": False,
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }
            path.write_text(json.dumps(receipt), encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(
                GATE.acceptance_status(str(path), time.time()), "PASS"
            )
            arguments = acceptance_arguments(str(path))
            with (
                patch.object(GATE, "health_check", return_value="PASS"),
                patch.object(GATE, "sqlite_check", return_value="PASS"),
                patch.object(GATE, "backup_check", return_value="PASS"),
                patch.object(
                    GATE,
                    "predeploy_backup_status",
                    return_value={"status": "PASS", "detail": "parity"},
                ),
                patch.object(GATE, "reader_cache_check", return_value="PASS"),
            ):
                report = GATE.inspect(arguments)
            self.assertTrue(report["automated_pass"])
            self.assertEqual(report["manual_evidence_status"], "PASS")
            self.assertEqual(report["manual_completed"], [GATE.PENDING[0]])
            self.assertEqual(len(report["manual_pending"]), 2)
            self.assertFalse(report["release_authorized"])

    def test_missing_evidence_is_not_implicitly_approved(self):
        """Do not infer a human acceptance from test or GitHub success."""

        self.assertEqual(
            GATE.acceptance_status(None, time.time()), "NOT_PROVIDED"
        )
        self.assertEqual(
            GATE.acceptance_status("/nonexistent/evidence", time.time()),
            "INVALID",
        )

    def test_world_readable_and_symlink_receipt_is_rejected(self):
        """Acceptance evidence must be a protected, direct regular file."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof.json"
            path.write_text("{}", encoding="utf-8")
            path.chmod(0o644)
            self.assertEqual(
                GATE.acceptance_status(str(path), time.time()), "INVALID"
            )
            alias = Path(tmp) / "linked.json"
            alias.symlink_to(path)
            path.chmod(0o600)
            self.assertEqual(
                GATE.acceptance_status(str(alias), time.time()), "INVALID"
            )

    def test_invalid_report_fields_are_not_human_evidence(self):
        """A forged or incomplete JSON cannot close private acceptance."""

        current = datetime.now(timezone.utc).isoformat()
        base = {
            "bot_api_send": "PASS",
            "bot_api_edit": "PASS",
            "user_visible_edited_message": "PASS",
            "delivery_type": "silent private administrator chat",
            "public_channel_modified": False,
            "pinned_message_modified": False,
            "released": False,
            "confirmed_at": current,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof.json"
            for change in (
                {"bot_api_edit": "FAIL"},
                {"user_visible_edited_message": "PENDING"},
                {"public_channel_modified": True},
                {"pinned_message_modified": True},
                {"released": True},
                {"public_channel_modified": 0},
                {"delivery_type": "channel"},
            ):
                payload = {**base, **change}
                path.write_text(json.dumps(payload), encoding="utf-8")
                path.chmod(0o600)
                with self.subTest(change=change):
                    self.assertEqual(
                        GATE.acceptance_status(str(path), time.time()),
                        "INVALID",
                    )

    def test_evidence_rejects_future_expired_and_naive_times(self):
        """Human approval receipts need bounded, timezone-aware dates."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof.json"
            for timestamp in (
                (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
                (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                datetime.now().isoformat(),
            ):
                receipt = {
                    "bot_api_send": "PASS",
                    "bot_api_edit": "PASS",
                    "user_visible_edited_message": "PASS",
                    "delivery_type": "silent private administrator chat",
                    "public_channel_modified": False,
                    "pinned_message_modified": False,
                    "released": False,
                    "confirmed_at": timestamp,
                }
                path.write_text(json.dumps(receipt), encoding="utf-8")
                path.chmod(0o600)
                with self.subTest(timestamp=timestamp):
                    self.assertEqual(
                        GATE.acceptance_status(str(path), time.time()),
                        "INVALID",
                    )

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
