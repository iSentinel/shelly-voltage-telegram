"""Protect isolated systemd pre-start hook QA from production mutations."""

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
    / "scripts/disposable-boot-prestart-hook-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_prestart_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
NAME = "svt-qa-boot-abcdef123456"
IMAGE = "sha256:" + "a" * 64
PRODUCTION = ("b" * 64, "0", "true", IMAGE)
STOPPED = ("false", "no", "none", "0")
RUNNING = ("true", "no", "none", "0")


class BootPrestartHookTests(unittest.TestCase):
    """Use only mocked external commands and generated QA identities."""

    def invoke(self, *args: str) -> int:
        """Run the CLI safely without output or host changes."""
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_default_mode_is_inert(self) -> None:
        """Normal invocation may not call any host executable."""
        with patch.object(QA, "run", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke("--dry-run"), 0)

    def test_confirmation_required_exactly(self) -> None:
        """Partial or absent consent cannot start a QA service."""
        with patch.object(QA, "execute", side_effect=AssertionError):
            self.assertEqual(self.invoke("--run-disposable"), 2)
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_exact_confirmation_calls_qa_runner(self) -> None:
        """Only the exact QA-only phrase activates the runner."""
        with patch.object(QA, "execute") as runner:
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", QA.CONFIRM),
                0,
            )
            runner.assert_called_once_with()

    def test_name_rejection_includes_real_compose(self) -> None:
        """No actual production or arbitrary service can be targeted."""
        for value in (
            "shelly-voltage-bot",
            "shelly-voltage-compose",
            "svt-qa-boot-123",
            "svt-qa-boot-abcdef123456;rm",
        ):
            with (
                self.subTest(value=value),
                self.assertRaises(QA.Blocked),
            ):
                QA.safe_name(value)
        QA.safe_name(NAME)

    def test_only_two_transient_service_names(self) -> None:
        """Never create an updater or actual systemd boot unit."""
        with self.assertRaises(QA.Blocked):
            QA.unit(NAME, "production")
        self.assertEqual(QA.unit(NAME, "hook"), NAME + "-hook.service")

    def test_mutable_image_tag_rejected(self) -> None:
        """Use only the locally inspected immutable image ID."""
        with self.assertRaises(QA.Blocked):
            QA.create_container(NAME, "image:latest")

    def test_docker_wrapper_rejects_real_target(self) -> None:
        """Neither a real stop nor a QA image update can be called."""
        with self.assertRaises(QA.Blocked):
            QA.docker("shelly-voltage-bot", "stop")
        with self.assertRaises(QA.Blocked):
            QA.docker(NAME, "update", "--restart=no")

    def test_container_create_never_has_network_or_data_mount(self) -> None:
        """QA sleeper contains no production file paths, ports or tokens."""
        with (
            patch.object(QA, "run", return_value="") as runner,
            patch.object(QA, "state", return_value=STOPPED),
        ):
            QA.create_container(NAME, IMAGE)
        args = runner.call_args.args[0]
        self.assertEqual(args[:2], ["docker", "create"])
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop", args)
        self.assertIn("--restart=no", args)
        for forbidden in ("--mount", "--volume", "--env", "--publish"):
            self.assertNotIn(forbidden, args)

    def test_hook_command_exactly_targets_only_qa_name(self) -> None:
        """Transient ExecStartPre may only start generated QA Docker."""
        with patch.object(QA, "run", return_value="") as runner:
            QA.start_prestart_hook(NAME)
        args = runner.call_args.args[0]
        self.assertIn(
            f"--property=ExecStartPre=/usr/bin/docker start {NAME}", args
        )
        self.assertIn("--collect", args)
        self.assertNotIn("shelly-voltage-compose.service", str(args))

    def test_control_has_no_prestart_hook(self) -> None:
        """Negative control cannot call Docker start from systemd."""
        with patch.object(QA, "run", return_value="") as runner:
            QA.start_control_service(NAME)
        args = runner.call_args.args[0]
        self.assertFalse(any("ExecStartPre" in item for item in args))
        self.assertEqual(args[-1], "/usr/bin/true")

    def test_running_state_must_be_trusted(self) -> None:
        """Malformed Docker state can never count as startup evidence."""
        with (
            patch.object(QA, "docker", return_value="unknown|no|none|0"),
            self.assertRaises(QA.Blocked),
        ):
            QA.state(NAME)

    def test_hook_wait_observes_test_start(self) -> None:
        """Running QA process is evidence of hook activation."""
        with (
            patch.object(QA, "state", side_effect=[STOPPED, RUNNING]),
            patch.object(QA.time, "sleep"),
        ):
            QA.await_running(NAME)

    def test_hook_does_not_start_fails_closed(self) -> None:
        """If QA remains stopped, acceptance must fail."""
        with (
            patch.object(QA, "state", return_value=STOPPED),
            patch.object(QA.time, "monotonic", side_effect=[0, 1, 10]),
            patch.object(QA.time, "sleep"),
            self.assertRaises(QA.Blocked),
        ):
            QA.await_running(NAME)

    def test_no_hook_control_stays_stopped(self) -> None:
        """Control without the hook must not restart the QA writer."""
        with (
            patch.object(QA, "state", return_value=STOPPED),
            patch.object(QA.time, "sleep"),
        ):
            QA.verify_stays_stopped(NAME)

    def test_control_writer_started_is_blocked(self) -> None:
        """A surprise start in the negative control means test failure."""
        with (
            patch.object(QA, "state", return_value=RUNNING),
            self.assertRaises(QA.Blocked),
        ):
            QA.verify_stays_stopped(NAME)

    def test_cleanup_only_generated_services(self) -> None:
        """No real production timer or Compose service may be stopped."""
        with patch.object(QA, "run", return_value="") as runner:
            QA.cleanup(NAME)
        calls = [entry.args[0] for entry in runner.call_args_list]
        self.assertEqual(calls[-1], ["docker", "rm", "-f", NAME])
        self.assertEqual(len(calls), 5)
        self.assertNotIn("shelly-voltage", str(calls))

    def test_production_units_are_only_inspected(self) -> None:
        """Read-only owner baseline cannot change enabled services."""
        with patch.object(QA, "run", return_value="state") as runner:
            self.assertEqual(QA.production_owner_states(), ("state", "state"))
        calls = [entry.args[0] for entry in runner.call_args_list]
        self.assertTrue(all(cmd[:2] == ["systemctl", "show"] for cmd in calls))
        self.assertIn("shelly-voltage-compose.service", calls[0])
        self.assertIn("shelly-voltage-update-check.timer", calls[1])

    def test_qa_failure_attempts_cleanup(self) -> None:
        """Interrupted QA service setup must run cleanup and checks."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(
                QA, "production_owner_states", return_value=("a", "b")
            ),
            patch.object(
                QA, "create_container", side_effect=QA.Blocked("CRASH")
            ),
            patch.object(QA, "cleanup") as cleanup,
            patch.object(QA, "verify_cleanup"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_changed_real_container_blocks_qa_acceptance(self) -> None:
        """A concurrent production restart invalidates an isolated test."""
        changed = ("c" * 64, "1", "true", IMAGE)
        with (
            patch.object(QA, "prerequisites"),
            patch.object(
                QA,
                "production_identity",
                side_effect=[PRODUCTION, changed],
            ),
            patch.object(
                QA, "production_owner_states", return_value=("a", "b")
            ),
            patch.object(QA, "create_container"),
            patch.object(QA, "exercise"),
            patch.object(QA, "cleanup"),
            patch.object(QA, "verify_cleanup"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()

    def test_changed_prod_systemd_owner_blocks(self) -> None:
        """An unexpected Compose/timer state change invalidates QA."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(
                QA,
                "production_owner_states",
                side_effect=[("active", "enabled"), ("inactive", "enabled")],
            ),
            patch.object(QA, "create_container"),
            patch.object(QA, "exercise"),
            patch.object(QA, "cleanup"),
            patch.object(QA, "verify_cleanup"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()

    def test_failed_external_command_blocks(self) -> None:
        """Do not interpret a failed systemd action as a successful hook."""
        proc = subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr=""
        )
        with (
            patch.object(subprocess, "run", return_value=proc),
            self.assertRaises(QA.Blocked),
        ):
            QA.run(["systemctl", "show"])


if __name__ == "__main__":
    unittest.main()
