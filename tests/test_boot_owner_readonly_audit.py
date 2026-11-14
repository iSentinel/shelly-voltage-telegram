"""Read-only boot owner observation may never authorize a writer start."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/boot-owner-readonly-audit.py"
)
SPEC = importlib.util.spec_from_file_location(
    "svt_boot_owner_observer", SCRIPT
)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)

CONTAINER = "svt-qa-signed-writer-114"
UNIT = "svt-qa-compose.service"
GOOD_DOCKER = {"restart_policy": "no", "running": False}
GOOD_COMPOSE = {
    "restart_policy": "no",
    "network_mode": "none",
    "ports_present": False,
}
GOOD_UNIT = {
    "active": "failed",
    "enabled": True,
    "prestart_present": True,
    "start_present": True,
}


class BootOwnerReadonlyObserverTests(unittest.TestCase):
    """Pure synthetic observations and mocked command boundaries."""

    def setUp(self) -> None:
        """Give the observer only a private dummy file and path."""
        temp = tempfile.TemporaryDirectory(
            prefix="svt-owner-unit-", dir="/tmp"
        )
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.compose = self.root / "compose.yaml"
        self.compose.write_text("name: qa\n")
        self.pending = self.root / "pending-update"

    def cli(self, *args: str) -> tuple[int, str]:
        """Capture CLI without calling any live service."""
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
        ):
            return QA.main(), output.getvalue()

    def test_inert_default_never_inspects_host(self) -> None:
        """Empty invocation is always a no-op."""
        with patch.object(QA, "observe", side_effect=AssertionError):
            code, text = self.cli()
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", text)

    def test_explicit_dry_run_never_inspects_host(self) -> None:
        """Dry-run must dominate any --observe flag."""
        with patch.object(QA, "observe", side_effect=AssertionError):
            code, _ = self.cli("--observe", "--dry-run")
        self.assertEqual(code, 0)

    def test_all_targets_required(self) -> None:
        """No silent production path defaults allowed."""
        self.assertEqual(self.cli("--observe")[0], 2)

    def test_fixed_target_allowlist(self) -> None:
        """Arbitrary Docker targets must never be inspected."""
        self.assertTrue(QA.CONTAINER.fullmatch(CONTAINER))
        self.assertTrue(QA.CONTAINER.fullmatch("shelly-voltage-bot"))
        self.assertFalse(QA.CONTAINER.fullmatch("random-docker-name"))
        self.assertFalse(QA.SERVICE.fullmatch("ssh.service"))

    def test_reject_relative_compose(self) -> None:
        """Only explicit absolute regular Compose file is allowed."""
        with self.assertRaises(QA.Blocked):
            QA.ensure_file(Path("relative.yaml"))

    def test_reject_missing_compose(self) -> None:
        """Do not implicitly create or search Compose files."""
        with self.assertRaises(QA.Blocked):
            QA.ensure_file(self.root / "nonexistent.yaml")

    def test_reject_linked_compose(self) -> None:
        """Do not follow a symlink to another source."""
        alias = self.root / "alias.yaml"
        alias.symlink_to(self.compose)
        with self.assertRaises(QA.Blocked):
            QA.ensure_file(alias)

    def test_pending_marker_detected_without_read(self) -> None:
        """Marker presence alone is enough to require safe boot."""
        self.assertFalse(QA.pending_state(self.pending))
        self.pending.write_text("synthetic\n")
        self.assertTrue(QA.pending_state(self.pending))

    def test_symlinked_pending_marker_rejected(self) -> None:
        """Do not trust a redirected pending marker."""
        alias = self.root / "alias-pending"
        self.pending.write_text("synthetic\n")
        alias.symlink_to(self.pending)
        with self.assertRaises(QA.Blocked):
            QA.pending_state(alias)

    def test_single_owner_pending_stopped_is_observation_only(self) -> None:
        """No boot authority follows even when writer stays stopped."""
        result = QA.classify(GOOD_DOCKER, GOOD_COMPOSE, GOOD_UNIT, True)
        self.assertEqual(result["status"], "PENDING_WRITER_FENCED_OBSERVED")
        self.assertFalse(result["boot_start_authorized"])
        self.assertFalse(result["all_other_restart_owners_fenced"])

    def test_running_writer_while_pending_detects_bypass(self) -> None:
        """The real LXC114 bypass must be explicit in QA status."""
        docker = dict(GOOD_DOCKER, running=True)
        result = QA.classify(docker, GOOD_COMPOSE, GOOD_UNIT, True)
        self.assertEqual(result["status"], "BOOT_OWNER_UNFENCED")
        self.assertIn("PENDING_BUT_DOCKER_WRITER_RUNNING", result["reasons"])

    def test_docker_unless_stopped_detects_independent_owner(self) -> None:
        """Original QA restart policy is not single-writer fenced."""
        docker = dict(GOOD_DOCKER, restart_policy="unless-stopped")
        result = QA.classify(docker, GOOD_COMPOSE, GOOD_UNIT, True)
        self.assertIn("DOCKER_INDEPENDENT_RESTART_OWNER", result["reasons"])

    def test_compose_policy_can_reenable_docker_restart(self) -> None:
        """Docker update alone is insufficient if Compose undoes it."""
        compose = dict(GOOD_COMPOSE, restart_policy="unless-stopped")
        result = QA.classify(GOOD_DOCKER, compose, GOOD_UNIT, True)
        self.assertIn("COMPOSE_REENABLES_DOCKER_RESTART", result["reasons"])

    def test_compose_missing_prestart_fails(self) -> None:
        """Single-owner Compose must guard its start path."""
        unit = dict(GOOD_UNIT, prestart_present=False)
        result = QA.classify(GOOD_DOCKER, GOOD_COMPOSE, unit, True)
        self.assertIn("SYSTEMD_PREFLIGHT_OR_START_MISSING", result["reasons"])

    def test_systemd_disabled_requires_review(self) -> None:
        """Observed restart=no does not imply any managed boot start."""
        unit = dict(GOOD_UNIT, enabled=False)
        result = QA.classify(GOOD_DOCKER, GOOD_COMPOSE, unit, False)
        self.assertIn("SYSTEMD_NOT_ENABLED", result["reasons"])

    def test_systemd_active_with_pending_is_inconsistent(self) -> None:
        """Active Compose and pending marker need independent review."""
        unit = dict(GOOD_UNIT, active="active")
        result = QA.classify(GOOD_DOCKER, GOOD_COMPOSE, unit, True)
        self.assertIn("PENDING_BUT_COMPOSE_SERVICE_ACTIVE", result["reasons"])

    def test_clean_single_owner_remains_non_authorizing(self) -> None:
        """No pending marker means observation, not release approval."""
        result = QA.classify(GOOD_DOCKER, GOOD_COMPOSE, GOOD_UNIT, False)
        self.assertEqual(result["status"], "SINGLE_OWNER_POLICY_OBSERVED")
        for key in (
            "boot_start_authorized",
            "source_install_authorized",
            "telegram_photo_authorized",
            "production_modified",
        ):
            self.assertFalse(result[key])

    def test_invalid_docker_inspect_is_blocked(self) -> None:
        """Never guess the writer status on incomplete information."""
        with (
            patch.object(QA, "command", return_value="no|unknown"),
            self.assertRaises(QA.Blocked),
        ):
            QA.docker_state(CONTAINER)

    def test_compose_many_services_refused(self) -> None:
        """Observing many services does not prove one writer."""
        plan = {"services": {"a": {}, "b": {}}}
        with (
            patch.object(QA, "command", return_value=json.dumps(plan)),
            self.assertRaises(QA.Blocked),
        ):
            QA.compose_state(self.compose, CONTAINER)

    def test_compose_identity_mismatch_refused(self) -> None:
        """No accidental other-service inspection."""
        plan = {"services": {"a": {"container_name": "different"}}}
        with (
            patch.object(QA, "command", return_value=json.dumps(plan)),
            self.assertRaises(QA.Blocked),
        ):
            QA.compose_state(self.compose, CONTAINER)

    def test_compose_does_not_return_environment_values(self) -> None:
        """Only restart and isolation fields may leave Compose parsing."""
        plan = {
            "services": {
                "a": {
                    "container_name": CONTAINER,
                    "restart": "no",
                    "network_mode": "none",
                    "environment": {"TOKEN": "secret"},
                }
            }
        }
        with patch.object(QA, "command", return_value=json.dumps(plan)):
            result = QA.compose_state(self.compose, CONTAINER)
        self.assertEqual(result, GOOD_COMPOSE)
        self.assertNotIn("secret", str(result))

    def test_systemd_prestart_flag_is_observed(self) -> None:
        """Read-only systemctl metadata, never invoke a unit."""
        data = (
            "ActiveState=failed\nUnitFileState=enabled\n"
            "ExecStartPre={ path=/usr/bin/python3 ; argv[]=/qa/prestart.py }\n"
            "ExecStart={ path=/usr/bin/docker ; argv[]=compose up }\n"
        )
        with patch.object(QA, "command", return_value=data):
            self.assertEqual(QA.systemd_state(UNIT), GOOD_UNIT)

    def test_unrecognized_prestart_cannot_pass(self) -> None:
        """A dummy ExecStartPre is not a recovery owner guard."""
        data = (
            "ActiveState=failed\nUnitFileState=enabled\n"
            "ExecStartPre={ path=/bin/true ; argv[]=/bin/true }\n"
            "ExecStart={ path=/usr/bin/docker ; argv[]=compose up }\n"
        )
        with patch.object(QA, "command", return_value=data):
            result = QA.systemd_state(UNIT)
        self.assertFalse(result["prestart_present"])
        self.assertIn(
            "SYSTEMD_PREFLIGHT_OR_START_MISSING",
            QA.classify(GOOD_DOCKER, GOOD_COMPOSE, result, True)["reasons"],
        )

    def test_unrecognized_start_cannot_pass(self) -> None:
        """A marker-only ExecStart does not own the Compose writer."""
        data = (
            "ActiveState=failed\nUnitFileState=enabled\n"
            "ExecStartPre={ path=/usr/bin/python3 ; argv[]=/qa/prestart.py }\n"
            "ExecStart={ path=/usr/bin/touch ; argv[]=/qa/marker }\n"
        )
        with patch.object(QA, "command", return_value=data):
            result = QA.systemd_state(UNIT)
        self.assertFalse(result["start_present"])

    def test_subprocess_failure_blocks(self) -> None:
        """Do not assume safety if the Docker socket is unavailable."""
        proc = subprocess.CompletedProcess(
            args=["docker"], returncode=1, stdout="", stderr="error"
        )
        with (
            patch.object(subprocess, "run", return_value=proc),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["docker", "inspect"])

    def test_subprocess_timeout_blocks(self) -> None:
        """Never wait indefinitely for a damaged Docker daemon."""
        with (
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("docker", 8),
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["docker", "inspect"])

    def test_cli_reports_review_without_changing_any_service(self) -> None:
        """Mock explicit live inputs and verify fail-closed JSON result."""
        danger = QA.classify(
            dict(GOOD_DOCKER, restart_policy="unless-stopped"),
            GOOD_COMPOSE,
            GOOD_UNIT,
            True,
        )
        with patch.object(QA, "observe", return_value=danger):
            code, output = self.cli(
                "--observe",
                "--container",
                CONTAINER,
                "--unit",
                UNIT,
                "--compose",
                str(self.compose),
                "--pending",
                str(self.pending),
                "--jsonl",
            )
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output)["status"], "BOOT_OWNER_UNFENCED")

    def test_help_available(self) -> None:
        """-h and --help work without Docker or systemd."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--pending", result.stdout)


if __name__ == "__main__":
    unittest.main()
