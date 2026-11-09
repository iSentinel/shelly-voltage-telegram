"""Verify privacy auditing has no raw health leaks or external network."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

SRC = (
    Path(__file__).resolve().parents[1]
    / "scripts/shelly-error-privacy-audit.py"
)
SPEC = importlib.util.spec_from_file_location("shelly_privacy_audit", SRC)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def snapshot(reason: object = "connection_error") -> dict:
    """Make a local health fixture for three phases."""

    return {
        "connectivity": "offline",
        "last_error": reason,
        "phases": {
            "L1": {"connectivity": "offline", "last_error": reason},
            "L2": {"connectivity": "not_configured"},
            "L3": {"connectivity": "not_configured"},
        },
    }


class ShellyPrivacyAuditTests(unittest.TestCase):
    """No production service or bot API is contacted by these fixtures."""

    def test_sanitized_runtime_is_pass(self):
        """Only fixed categories are acceptable in health snapshots."""

        report = AUDIT.audit_snapshot(snapshot())
        self.assertEqual(report["status"], "pass")
        self.assertFalse(report["raw_error_exported"])

    def test_realistic_unpatched_runtime_is_fail_and_redacted(self):
        """An IP and password must never appear in failed audit output."""

        private = "http://u:secret@192.0.2.10/rpc"
        report = AUDIT.audit_snapshot(snapshot(private))
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "untrusted_error_detail")
        self.assertNotIn(private, json.dumps(report))
        self.assertNotIn("192.0.2.10", json.dumps(report))

    def test_secondary_phase_untrusted_error_is_fail(self):
        """The L2 channel must obey exactly the same privacy policy."""

        payload = snapshot()
        payload["phases"]["L2"] = {
            "connectivity": "offline",
            "last_error": "private-url",
        }
        self.assertEqual(AUDIT.audit_snapshot(payload)["status"], "fail")

    def test_online_without_error_is_not_false_pass(self):
        """Without a sampled error, compatibility cannot be proven."""

        record = snapshot(None)
        self.assertEqual(
            AUDIT.audit_snapshot(record)["status"], "inconclusive"
        )

    def test_invalid_health_schema_fails_closed(self):
        """Malformed or missing phase objects never silently pass."""

        for payload in (None, [], {}, {"phases": []}, {"phases": {}}):
            with self.subTest(payload=payload):
                self.assertEqual(
                    AUDIT.audit_snapshot(payload)["status"], "fail"
                )

    def test_loopback_only_endpoint_validation(self):
        """Reject remote IPs, host credentials and arbitrary URL paths."""

        for address in (
            "https://127.0.0.1:8080/health",
            "http://192.0.2.10:80/health",
            "http://user:password@127.0.0.1:8080/health",
            "http://localhost:8080/admin",
            "http://localhost:8080/health?token=secret",
            "http://localhost:99999/health",
        ):
            with self.subTest(url=address), self.assertRaises(ValueError):
                AUDIT.validate_url(address)

    def test_local_response_is_read_once_and_sanitized(self):
        """HTTP GET does not cause an API write or expose a secret."""

        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(snapshot()).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(AUDIT, "build_opener", return_value=opener) as build:
            result = AUDIT.audit_live("http://127.0.0.1:8080/health", 1)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(build.call_count, 1)
        self.assertEqual(len(build.call_args.args), 2)
        self.assertIsInstance(build.call_args.args[0], AUDIT.ProxyHandler)
        self.assertIsInstance(build.call_args.args[1], AUDIT.RejectRedirects)
        opener.open.assert_called_once_with(
            "http://127.0.0.1:8080/health", timeout=1
        )
        response.read.assert_called_once_with(65537)

    def test_redirect_to_remote_destination_is_rejected(self):
        """A local HTTP redirect cannot authorize any remote fetch."""

        handler = AUDIT.RejectRedirects()
        follow = handler.redirect_request(
            Mock(),
            None,
            302,
            "redirect",
            {},
            "https://private-device.invalid/health",
        )
        self.assertIsNone(follow)

    def test_untrusted_json_type_is_fail_not_exception(self):
        """Dicts or lists in error fields fail without leaking contents."""

        for raw in ({"private": "secret"}, ["192.0.2.10"], True):
            with self.subTest(type=type(raw).__name__):
                report = AUDIT.audit_snapshot(snapshot(raw))
                self.assertEqual(report["status"], "fail")
                self.assertNotIn("192.0.2.10", json.dumps(report))

    def test_oversized_health_document_fails_closed(self):
        """A local server cannot force unbounded JSON buffering."""

        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.return_value = b"x" * 65537
        opener = Mock()
        opener.open.return_value = stream
        with patch.object(AUDIT, "build_opener", return_value=opener):
            report = AUDIT.audit_live("http://127.0.0.1:8080/health", 1)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["reason"], "oversized_health")

    def test_dry_run_does_not_fetch_health(self):
        """Dry run is safe even if the monitor is powered off."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["privacy", "--dry-run", "--json"]),
            patch.object(
                AUDIT,
                "build_opener",
                side_effect=AssertionError("network unexpected"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(AUDIT.main(), 0)
        self.assertEqual(
            json.loads(output.getvalue())["reason"], "no_network_calls"
        )

    def test_live_unpatched_health_exit_status_is_two(self):
        """A rejected health document returns stable failure code 2."""

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["privacy", "--json"]),
            patch.object(
                AUDIT,
                "audit_live",
                return_value=AUDIT.verdict("fail", "untrusted_error_detail"),
            ),
            redirect_stdout(out),
        ):
            self.assertEqual(AUDIT.main(), 2)
        self.assertNotIn("192.0.2.10", out.getvalue())

    def test_help_and_inconclusive_exit(self):
        """Help and inconclusive statuses are deterministic for scripts."""

        out = io.StringIO()
        with (
            patch.object(sys, "argv", ["privacy", "--help"]),
            redirect_stdout(out),
            self.assertRaises(SystemExit) as finish,
        ):
            AUDIT.parser().parse_args()
        self.assertEqual(finish.exception.code, 0)
        with (
            patch.object(sys, "argv", ["privacy"]),
            patch.object(
                AUDIT,
                "audit_live",
                return_value=AUDIT.verdict(
                    "inconclusive", "no_error_observed"
                ),
            ),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(AUDIT.main(), 3)


if __name__ == "__main__":
    unittest.main()
