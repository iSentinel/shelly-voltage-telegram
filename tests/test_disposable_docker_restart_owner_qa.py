"""Offline unit contracts for the disposable Docker restart owner bypass QA."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-docker-restart-owner-qa.py"
)
SPEC = importlib.util.spec_from_file_location("restart_owner_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def response(code: int = 0, output: str = "") -> SimpleNamespace:
    """Return a small synthetic subprocess result with no side effects."""
    return SimpleNamespace(returncode=code, stdout=output, stderr="")


class DockerRestartOwnerTests(unittest.TestCase):
    """Never start a real container from the unit suite."""

    def setUp(self) -> None:
        """Create only private invented paths."""
        folder = tempfile.TemporaryDirectory(prefix="restart-owner-unit-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def invoke(self, *args: str) -> tuple[int, str]:
        """Read command-line output without crossing the guest guard."""
        stream = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(stream),
        ):
            return QA.main(), stream.getvalue()

    def test_dry_run_is_inert(self) -> None:
        """No flags may never run Docker or check production."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            code, text = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("DRY_RUN", text)

    def test_generic_confirmation_rejected(self) -> None:
        """A generic yes must not enable offline container restart QA."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            code, _ = self.invoke("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)

    def test_wrong_guest_denied(self) -> None:
        """No root test can begin on a different hostname."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(QA.socket, "gethostname", return_value="production"),
            self.assertRaisesRegex(RuntimeError, "NOT_LXC114_QA"),
        ):
            QA.guest_guard()

    def test_nonroot_denied(self) -> None:
        """Unprivileged code cannot start the Docker bypass test."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(RuntimeError, "NOT_LXC114_QA"),
        ):
            QA.guest_guard()

    def test_nonloopback_denied(self) -> None:
        """External interfaces must stop before any Docker operation."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(RuntimeError, "QA_NETWORK_NOT_ISOLATED"),
        ):
            QA.guest_guard()

    def test_unsafe_original_writer_denied(self) -> None:
        """A running signed QA writer prevents any restart experiments."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(QA, "command", return_value=response(0, "true|no\n")),
            self.assertRaisesRegex(RuntimeError, "ORIGINAL_QA_WRITER_UNSAFE"),
        ):
            QA.guest_guard()

    def test_restart_policy_is_real(self) -> None:
        """Candidate arguments mirror independent Docker auto-restart."""
        values = QA.container_args(self.root, "svt-qa-owner-unit")
        self.assertEqual(values[0:2], ["docker", "run"])
        self.assertIn("unless-stopped", values)
        self.assertIn("--restart", values)
        self.assertIn("none", values)
        self.assertIn("--network", values)
        self.assertIn("--read-only", values)
        self.assertIn("no-new-privileges", values)
        self.assertIn(QA.IMAGE, values)

    def test_owner_label_and_private_mount(self) -> None:
        """Container is labelled for safe removal and mounts only scratch."""
        name = "svt-qa-owner-unit"
        values = QA.container_args(self.root, name)
        self.assertIn("org.svt.qa.owner=" + name, values)
        self.assertIn("type=bind,src=" + str(self.root) + ",dst=/work", values)
        self.assertFalse(any("secrets" in x for x in values))
        self.assertFalse(any("/var/lib/shelly-voltage" in x for x in values))

    def test_worker_is_valid_python(self) -> None:
        """Counter-only worker uses no network, SQLite or Telegram."""
        compile(QA.WORKER, "qa-worker", "exec")
        self.assertIn("restarts.log", QA.WORKER)
        self.assertNotIn("sqlite", QA.WORKER.lower())
        self.assertNotIn("telegram", QA.WORKER.lower())

    def test_observed_bypass_classification(self) -> None:
        """Synthetic completed counter and restart state are sufficient."""
        (self.root / "restarts.log").write_text("attempt\n" * 3)
        with (
            patch.object(QA, "command", return_value=response(0, "created")),
            patch.object(QA, "get_state", return_value=(2, "running")),
        ):
            report = QA.prove_bypass(self.root, "svt-qa-owner-unit")
        self.assertEqual(report["classification"], "BYPASS_CONFIRMED")
        self.assertEqual(report["attempts_under_B_EX"], 3)

    def test_cleanup_rejects_unknown_owner(self) -> None:
        """A foreign-labelled container must never be removed or altered."""
        calls = []

        def mocked(args: list[str], timeout: int = 12) -> SimpleNamespace:
            calls.append(args)
            return response(0, json.dumps({"org.svt.qa.owner": "other"}))

        with (
            patch.object(QA, "command", side_effect=mocked),
            self.assertRaisesRegex(RuntimeError, "UNEXPECTED_CONTAINER"),
        ):
            QA.cleanup_container("svt-qa-owner-unit")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "inspect")

    def test_cleanup_disables_restart_before_remove(self) -> None:
        """An owned sandbox must have restart disabled before removal."""
        calls = []
        name = "svt-qa-owner-unit"

        def mocked(args: list[str], timeout: int = 12) -> SimpleNamespace:
            calls.append(args)
            if args[1] == "inspect":
                return (
                    response(0, json.dumps({"org.svt.qa.owner": name}))
                    if len(calls) == 1
                    else response(1)
                )
            return response()

        with patch.object(QA, "command", side_effect=mocked):
            QA.cleanup_container(name)
        self.assertEqual(
            [c[1] for c in calls], ["inspect", "update", "rm", "inspect"]
        )
        self.assertIn("--restart=no", calls[1])

    def test_cleanup_missing_sandbox_noop(self) -> None:
        """A failed earlier start must never remove an unrelated container."""
        with patch.object(QA, "command", return_value=response(1)) as mocked:
            QA.cleanup_container("svt-qa-owner-missing")
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
