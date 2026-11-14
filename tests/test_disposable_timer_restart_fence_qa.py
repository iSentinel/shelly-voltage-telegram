"""Verify timer-fence QA uses only generated, isolated systemd objects."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-timer-restart-fence-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_timer_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
NAME = "svt-qa-timer-abcdef123456"
IMAGE = "sha256:" + "a" * 64
PRODUCTION = ("b" * 64, "0", "true", IMAGE)


class DisposableTimerFenceTests(unittest.TestCase):
    """All unit tests operate on mock commands, never live systemd."""

    def invoke(self, *args: str) -> int:
        """Invoke a CLI mode with output suppressed."""
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_default_is_nonmutating_dry_run(self) -> None:
        """Normal invocation may not touch the Docker daemon."""
        with patch.object(QA, "run", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke("--dry-run"), 0)

    def test_missing_confirmation_blocks_execution(self) -> None:
        """The test requires an exact explicit disposable-only phrase."""
        with patch.object(QA, "execute", side_effect=AssertionError):
            self.assertEqual(self.invoke("--run-disposable"), 2)
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_exact_confirmation_allows_qa_only(self) -> None:
        """The confirmed request executes the isolated test runner."""
        with patch.object(QA, "execute") as execute:
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", QA.CONFIRM),
                0,
            )
            execute.assert_called_once_with()

    def test_untrusted_qa_names_are_refused(self) -> None:
        """Reject actual production and arbitrary caller input."""
        for value in (
            "shelly-voltage-bot",
            "svt-qa-timer-abcdef123456;stop",
            "svt-qa-timer-123",
            "other-container",
        ):
            with (
                self.subTest(value=value),
                self.assertRaises(QA.Blocked),
            ):
                QA.qa_name(value)
        QA.qa_name(NAME)

    def test_only_two_generated_timer_phases(self) -> None:
        """Prevent arbitrary systemd service targets."""
        with self.assertRaises(QA.Blocked):
            QA.timer_unit(NAME, "production")
        self.assertEqual(QA.timer_unit(NAME, "fenced"), NAME + "-fenced")

    def test_mutable_image_reference_is_refused(self) -> None:
        """Only the already-local image ID may be reused."""
        with self.assertRaises(QA.Blocked):
            QA.creation_args(NAME, "image:latest")

    def test_no_network_mounts_credentials_or_privileged_mode(self) -> None:
        """The QA container is a restricted, unconnected sleeper."""
        args = QA.creation_args(NAME, IMAGE)
        self.assertEqual(args[:2], ["docker", "create"])
        self.assertEqual(args[args.index("--network") + 1], "none")
        for forbidden in (
            "--volume",
            "--mount",
            "--publish",
            "-p",
            "--env",
            "--privileged",
            "shelly-voltage-bot",
        ):
            self.assertNotIn(forbidden, args)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop", args)
        self.assertIn("no-new-privileges", args)
        self.assertEqual(args[args.index("--entrypoint") + 1], "/bin/sh")

    def test_docker_wrapper_rejects_unapproved_actions(self) -> None:
        """Neither production stop nor a test image update is allowed."""
        with self.assertRaises(QA.Blocked):
            QA.docker(NAME, "update")
        with self.assertRaises(QA.Blocked):
            QA.docker("shelly-voltage-bot", "stop")

    def test_docker_state_refuses_untrusted_output(self) -> None:
        """Do not infer a stopped writer from malformed metadata."""
        with (
            patch.object(QA, "docker", return_value="unknown"),
            self.assertRaises(QA.Blocked),
        ):
            QA.running(NAME)

    def test_actual_timer_arguments_include_precision(self) -> None:
        """AccuracySec must be short enough for deterministic tests."""
        with patch.object(QA, "run", return_value="") as execute:
            base = QA.start_timer(NAME, "unfenced")
        args = execute.call_args.args[0]
        self.assertEqual(base, NAME + "-unfenced")
        self.assertIn("--on-active=3s", args)
        self.assertIn("--timer-property=AccuracySec=1ms", args)
        self.assertIn("--timer-property=RandomizedDelaySec=0", args)
        self.assertEqual(args[-3:], ["/usr/bin/docker", "start", NAME])

    def test_fenced_timer_uses_only_generated_name(self) -> None:
        """The second QA timer uses a distinct one-shot unit."""
        with patch.object(QA, "run", return_value="") as execute:
            QA.start_timer(NAME, "fenced")
        args = execute.call_args.args[0]
        self.assertIn("--on-active=4s", args)
        self.assertIn("--unit", args)
        self.assertIn(NAME + "-fenced", args)

    def test_cleanup_only_stops_generated_qa_objects(self) -> None:
        """Cleanup cannot disable the real updater or Compose units."""
        with patch.object(QA, "run", return_value="") as execute:
            QA.cleanup(NAME)
        calls = [entry.args[0] for entry in execute.call_args_list]
        self.assertEqual(calls[-1], ["docker", "rm", "-f", NAME])
        self.assertNotIn("shelly-voltage-compose.service", str(calls))
        self.assertNotIn("shelly-voltage-update-check.timer", str(calls))

    def test_wait_running_observes_restart(self) -> None:
        """An unfenced timer running the QA writer is reported."""
        with (
            patch.object(QA, "running", side_effect=[False, True]),
            patch.object(QA.time, "sleep"),
        ):
            QA.await_running(NAME)

    def test_fenced_timer_stays_stopped(self) -> None:
        """The timer fence must keep the QA writer stopped."""
        with (
            patch.object(QA, "start_timer"),
            patch.object(QA, "stop_timer"),
            patch.object(QA, "running", return_value=False),
            patch.object(QA.time, "sleep"),
        ):
            QA.verify_fenced_timer(NAME)

    def test_fenced_timer_restart_blocks(self) -> None:
        """A resumed QA writer means the timer-fence acceptance failed."""
        with (
            patch.object(QA, "start_timer"),
            patch.object(QA, "stop_timer"),
            patch.object(QA, "running", return_value=True),
            self.assertRaises(QA.Blocked),
        ):
            QA.verify_fenced_timer(NAME)

    def test_subprocess_failure_is_fail_closed(self) -> None:
        """Systemd errors are never interpreted as a timer PASS."""
        result = subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr=""
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.Blocked),
        ):
            QA.run(["systemctl", "show"])

    def test_failed_creation_always_cleans(self) -> None:
        """A mid-experiment exception still removes generated QA objects."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(
                QA, "create_sleeper", side_effect=QA.Blocked("INJECTED")
            ),
            patch.object(QA, "cleanup") as cleanup,
            patch.object(QA, "verify_no_orphans"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_changed_production_identity_blocks(self) -> None:
        """Concurrent live restart cannot silently produce a QA PASS."""
        changed = ("c" * 64, "1", "true", IMAGE)
        with (
            patch.object(QA, "prerequisites"),
            patch.object(
                QA, "production_identity", side_effect=[PRODUCTION, changed]
            ),
            patch.object(QA, "create_sleeper"),
            patch.object(QA, "start_timer"),
            patch.object(QA, "await_running"),
            patch.object(QA, "stop_timer"),
            patch.object(QA, "docker"),
            patch.object(QA, "running", return_value=False),
            patch.object(QA, "verify_fenced_timer"),
            patch.object(QA, "cleanup"),
            patch.object(QA, "verify_no_orphans"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()

    def test_detect_orphaned_container(self) -> None:
        """A leftover QA Docker object must be reported as unsafe."""
        with (
            patch.object(QA, "run", return_value=NAME),
            self.assertRaises(QA.Blocked),
        ):
            QA.verify_no_orphans(NAME)


if __name__ == "__main__":
    unittest.main()
