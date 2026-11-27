"""Verify scheduled transport with real loopback HTTP and no Telegram."""

from __future__ import annotations

import json
import threading
import time
import unittest
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from test_app import APP


class ReportHTTPHandler(BaseHTTPRequestHandler):
    """Serve controlled, non-sensitive synthetic API replies."""

    def log_message(self, *_args):
        """Keep test HTTP logs quiet."""

    def do_POST(self):
        """Accept a request and return the configured fake response."""
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.accepted.set()
        body = json.dumps(self.server.body).encode()
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        with suppress(OSError):
            if self.server.slow:
                for byte in body:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    if self.server.finished.wait(0.2):
                        return
            else:
                self.wfile.write(body)


class ReportHTTPDeadlineTests(unittest.TestCase):
    """Use real subprocess transport and a loopback-only fake API."""

    def setUp(self):
        """Start a disposable HTTP server and replace the global stop flag."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReportHTTPHandler)
        self.server = server
        server.accepted = threading.Event()
        server.finished = threading.Event()
        server.status = 200
        server.body = {"ok": True, "result": {"message_id": 42}}
        server.slow = False
        self.stop = threading.Event()
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.finished.set)
        stop_patch = patch.object(APP, "STOP", self.stop)
        stop_patch.start()
        self.addCleanup(stop_patch.stop)
        self.url = f"http://127.0.0.1:{server.server_port}/sendMessage"

    def send(self):
        """Run the production subprocess transport against fake localhost."""
        return APP.bounded_report_post(self.url, {"text": "synthetic"})

    def test_acknowledged_delivery(self):
        """Return only the validated acknowledgement."""
        self.assertEqual(self.send(), self.server.body)

    def test_explicit_429(self):
        """Accept a coherent rate-limit rejection."""
        self.server.status = 429
        self.server.body = {
            "ok": False,
            "error_code": 429,
            "parameters": {"retry_after": 30},
        }
        self.assertEqual(self.send(), self.server.body)

    def test_inconsistent_error_is_ambiguous(self):
        """A JSON error mismatching HTTP status cannot permit a resend."""
        self.server.status = 500
        self.server.body = {"ok": False, "error_code": 429}
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            self.send()

    def test_malformed_ack_is_ambiguous(self):
        """A boolean is not a valid message identifier."""
        self.server.body["result"]["message_id"] = True
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            self.send()

    def test_oversized_response_is_ambiguous(self):
        """Bound the body without logging or trusting arbitrary content."""
        self.server.body["padding"] = "a" * 70000
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            self.send()

    def test_slow_body_hits_total_deadline(self):
        """A trickle of data cannot extend the wall-clock budget."""
        self.server.slow = True
        # Leave time for the transport child to import on a cold runner.
        # The response takes much longer than the complete deadline.
        self.server.body["padding"] = "a" * 128
        started = time.monotonic()
        with (
            patch.object(APP, "REPORT_HTTP_DEADLINE", 5.0),
            self.assertRaisesRegex(RuntimeError, "outcome unknown"),
        ):
            self.send()
        self.assertTrue(self.server.accepted.is_set())
        self.assertLess(time.monotonic() - started, 7.0)

    def test_stop_cancels_active_http(self):
        """Cancel an already accepted request without a second send."""
        self.server.slow = True

        def stop_when_accepted():
            """Wait for the remote side to receive the synthetic request."""
            if self.server.accepted.wait(5):
                self.stop.set()

        thread = threading.Thread(target=stop_when_accepted)
        thread.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
                self.send()
        finally:
            self.stop.set()
            thread.join(timeout=6)
        self.assertTrue(self.server.accepted.is_set())
        self.assertLess(time.monotonic() - started, 7.0)
