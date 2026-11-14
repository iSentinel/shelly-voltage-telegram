"""Test strict isolation of Docker/systemd lifecycle QA operations."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-docker-systemd-lifecycle-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_lifecycle_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
NAME = "svt-qa-lifecycle-abcdef123456"
UNIT = NAME + ".service"
IMAGE = "sha256:" + "a" * 64
PRODUCTION_ID = ("1" * 64, "0", "true", IMAGE)


class DisposableLifecycleTests(unittest.TestCase):
    """Verify that no code path can target production objects."""

    def invoke(self, *options: str) -> int:
        """Call the CLI and retain the expected exit status."""
        with (
            patch.object(sys, "argv", [str(SCRIPT), *options]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_dry_run_never_invokes_commands(self) -> None:
        """Default and explicit dry runs are non-mutating."""
        with patch.object(QA, "run", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke("--dry-run"), 0)

    def test_missing_confirmation_blocks_execution(self) -> None:
        """Explicit --run-disposable alone cannot start a container."""
        with patch.object(QA, "execute", side_effect=AssertionError):
            self.assertEqual(self.invoke("--run-disposable"), 2)
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "no"), 2
            )

    def test_exact_confirmation_is_required(self) -> None:
        """Only the owner-only QA confirmation unlocks the runner."""
        with patch.object(QA, "execute") as execute:
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", QA.CONFIRM),
                0,
            )
            execute.assert_called_once_with()

    def test_production_container_name_refused(self) -> None:
        """Production and arbitrary Docker targets never pass validation."""
        for value in (
            "shelly-voltage-bot",
            "any-container",
            "svt-qa-lifecycle-123",
            "svt-qa-lifecycle-abcdef123456;rm",
        ):
            with self.subTest(value=value), self.assertRaises(QA.QAError):
                QA.require_name(value)

    def test_generated_name_allowed(self) -> None:
        """Only exact generated disposable identifiers are accepted."""
        QA.require_name(NAME)

    def test_invalid_image_reference_fails_closed(self) -> None:
        """Mutable image tags cannot be used for this QA harness."""
        with self.assertRaises(QA.QAError):
            QA.container_creation_args(NAME, "shelly-voltage:latest")

    def test_container_has_no_network_ports_or_mounts(self) -> None:
        """Ensure no app entrypoint or host data can enter the sandbox."""
        args = QA.container_creation_args(NAME, IMAGE)
        self.assertEqual(args[:2], ["docker", "create"])
        self.assertIn("--network", args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        for unsafe in ("-v", "--volume", "-p", "--publish", "--env"):
            self.assertNotIn(unsafe, args)
        self.assertIn("--read-only", args)
        self.assertIn("--no-healthcheck", args)
        self.assertIn("65534:65534", args)
        self.assertEqual(args[args.index("--entrypoint") + 1], "/bin/sh")
        self.assertEqual(args[-1], "while :; do sleep 1; done")

    def test_docker_inspect_rejects_invalid_result(self) -> None:
        """Untrusted metadata cannot cause a false stopped-writer PASS."""
        with (
            patch.object(QA, "docker", return_value="invalid"),
            self.assertRaises(QA.QAError),
        ):
            QA.docker_state(NAME)

    def test_require_stopped_detects_surprise_start(self) -> None:
        """Restart during the A/B interval must be visible."""
        with (
            patch.object(QA, "docker_state", return_value=("true", "no")),
            self.assertRaises(QA.QAError),
        ):
            QA.require_stopped(NAME)

    def test_safe_docker_lifecycle_command_order(self) -> None:
        """QA writer must restart while restart policy remains no."""
        results = [
            ("true", "unless-stopped"),
            ("false", "no"),
            ("true", "no"),
            ("false", "no"),
            ("false", "no"),
            ("true", "no"),
            ("true", "unless-stopped"),
        ]
        with (
            tempfile.TemporaryDirectory(dir="/tmp") as directory,
            patch.object(QA, "create_container") as create,
            patch.object(QA, "docker") as docker,
            patch.object(QA, "docker_state", side_effect=results),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            QA.run_docker_lifecycle(NAME, Path(directory), IMAGE)
            self.assertTrue((Path(directory) / "qa-checkpoint.txt").exists())
        create.assert_called_once_with(NAME, IMAGE)
        expected = [
            call(NAME, "start"),
            call(NAME, "update", "--restart=no"),
            call(NAME, "stop", "-t", "3"),
            call(NAME, "start"),
            call(NAME, "stop", "-t", "3"),
            call(NAME, "start"),
            call(NAME, "update", "--restart=unless-stopped"),
        ]
        self.assertEqual(docker.call_args_list, expected)

    def test_run_refuses_failed_subprocess(self) -> None:
        """Command failure aborts the QA sequence."""
        result = subprocess.CompletedProcess(
            args=["docker"], returncode=1, stdout="", stderr=""
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.QAError),
        ):
            QA.run(["docker", "inspect"])

    def test_systemd_unit_rejects_production(self) -> None:
        """The systemd reader cannot inspect a production unit."""
        with self.assertRaises(QA.QAError):
            QA.systemd_state("shelly-voltage-compose.service")

    def test_cleanup_uses_only_generated_qa_names(self) -> None:
        """Ensure cleanup cannot stop production or remove live containers."""
        with patch.object(QA, "run", return_value="") as run:
            QA.cleanup(NAME, UNIT)
        commands = [part.args[0] for part in run.call_args_list]
        self.assertEqual(
            commands,
            [
                ["systemctl", "stop", UNIT],
                ["systemctl", "reset-failed", UNIT],
                ["docker", "rm", "-f", NAME],
            ],
        )

    def test_changed_production_identity_is_blocked(self) -> None:
        """A concurrent production restart invalidates the QA acceptance."""
        changed = ("2" * 64, "1", "true", IMAGE)
        with (
            patch.object(QA, "prerequisites"),
            patch.object(
                QA, "production_identity", side_effect=[PRODUCTION_ID, changed]
            ),
            patch.object(QA, "run_docker_lifecycle"),
            patch.object(QA, "run_systemd_lifecycle"),
            patch.object(QA, "cleanup") as cleanup,
            self.assertRaises(QA.QAError),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_failed_qa_run_still_cleans_objects(self) -> None:
        """A test failure must still remove its own disposable resources."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(
                QA, "production_identity", return_value=PRODUCTION_ID
            ),
            patch.object(
                QA,
                "run_docker_lifecycle",
                side_effect=QA.QAError("INJECTED_QA_FAILURE"),
            ),
            patch.object(QA, "cleanup") as cleanup,
            self.assertRaises(QA.QAError),
        ):
            QA.execute()
        cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
