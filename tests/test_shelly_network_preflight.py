"""No-network regression tests for privacy-aware Shelly reachability."""

from __future__ import annotations

import errno
import importlib.util
import io
import json
import socket
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

SOURCE = (
    Path(__file__).resolve().parents[1] / "scripts/shelly-network-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("shelly_net_preflight", SOURCE)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def records():
    """Return one fake private endpoint for socket-only testing."""

    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", 80))]


class ShellyNetworkPreflightTests(unittest.TestCase):
    """Never contact a real meter, Telegram API, or production storage."""

    def run_network(self, connect_error=None):
        """Mock DNS, socket creation and a single TCP connect."""

        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        if connect_error is not None:
            client.connect.side_effect = connect_error
        with (
            patch.object(PROBE.socket, "getaddrinfo", return_value=records()),
            patch.object(PROBE.socket, "socket", return_value=client),
        ):
            response = PROBE.run_probe("http://user:secret@192.0.2.10", 0.2)
        return response, client

    def test_success_is_only_tcp_not_rpc_or_physical_power(self):
        """A reachable port is not proof of correct voltage or HTTP RPC."""

        result, client = self.run_network()
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["reason"], "tcp_connected")
        self.assertFalse(result["http_rpc_tested"])
        self.assertFalse(result["device_power_state_verified"])
        client.connect.assert_called_once()

    def test_unreachable_is_safe_and_sanitized(self):
        """An errno 113 failure is reported without address disclosure."""

        result, _ = self.run_network(OSError(errno.EHOSTUNREACH, "private"))
        self.assertEqual(result["reason"], "tcp_unreachable")
        self.assertEqual(result["status"], "fail")
        self.assertNotIn("192.", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_connection_refused_is_different_from_unreachable(self):
        """A reachable host with closed port is not a DNS failure."""

        result, _ = self.run_network(OSError(errno.ECONNREFUSED, "refused"))
        self.assertEqual(result["reason"], "tcp_refused")

    def test_socket_timeout_is_different_from_unreachable(self):
        """A TCP timeout gets a stable failure reason."""

        result, _ = self.run_network(TimeoutError("timed out"))
        self.assertEqual(result["reason"], "tcp_timeout")

    def test_dns_error_never_echoes_host(self):
        """DNS errors omit configured host and authentication details."""

        with patch.object(
            PROBE.socket, "getaddrinfo", side_effect=socket.gaierror(-2)
        ):
            result = PROBE.run_probe(
                "private:password@unresolved.invalid", 0.2
            )
        self.assertEqual(result["reason"], "dns_failed")
        self.assertNotIn("unresolved", json.dumps(result))

    def test_missing_dns_results_does_not_pass(self):
        """An empty resolver response is not a reachable endpoint."""

        with patch.object(PROBE.socket, "getaddrinfo", return_value=[]):
            result = PROBE.run_probe("192.0.2.10", 0.2)
        self.assertEqual(result["reason"], "dns_empty")

    def test_bad_urls_and_timeouts_fail(self):
        """Reject malformed configuration before opening network sockets."""

        for host, timeout in (
            ("", 0.2),
            ("ftp://example.invalid", 0.2),
            ("http://host:65536", 0.2),
            ("host", 0.0),
            ("host", 9.0),
            ("host", float("nan")),
        ):
            with (
                self.subTest(host=host, timeout=timeout),
                self.assertRaises(ValueError),
            ):
                PROBE.run_probe(host, timeout)

    def test_dry_run_avoids_config_and_network(self):
        """Dry run works safely with no environment or credentials."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["preflight", "--dry-run", "--json"]),
            patch.object(
                PROBE.app,
                "load_config",
                side_effect=AssertionError("config was unexpectedly read"),
            ),
            patch.object(
                PROBE.socket,
                "getaddrinfo",
                side_effect=AssertionError("network was unexpectedly used"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(PROBE.main(), 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["status"], "dry_run")
        self.assertEqual(report["telegram_writes"], 0)

    def test_live_failures_return_exit_code_two(self):
        """An unreachable endpoint fails without uncaught exceptions."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["preflight", "--json"]),
            patch.object(
                PROBE.app,
                "load_config",
                return_value=Mock(shelly_host="192.0.2.10"),
            ),
            patch.object(
                PROBE,
                "tcp_probe",
                return_value=PROBE.result(
                    "fail",
                    "tcp_unreachable",
                ),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(PROBE.main(), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "fail")

    def test_help_is_available(self):
        """The preflight has a standard non-interactive help option."""

        output = io.StringIO()
        with (
            patch.object(sys, "argv", ["preflight", "--help"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as finish,
        ):
            PROBE.build_parser().parse_args()
        self.assertEqual(finish.exception.code, 0)
        self.assertIn("--dry-run", output.getvalue())


if __name__ == "__main__":
    unittest.main()
