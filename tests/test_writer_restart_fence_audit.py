"""The restart-fence audit may inspect production but never change it."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/writer-restart-fence-audit.py"
)
SPEC = importlib.util.spec_from_file_location("writer_fence_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class WriterRestartAuditTests(unittest.TestCase):
    """Cover all independent restart owners with no side effects."""

    def setUp(self) -> None:
        """Set synthetic running-service properties."""
        self.compose = {
            "LoadState": "loaded",
            "ActiveState": "active",
            "UnitFileState": "enabled",
            "Type": "oneshot",
        }
        self.timer = {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "UnitFileState": "enabled",
        }
        self.docker = {
            "running": "true",
            "restart": "unless-stopped",
            "image": (
                "ghcr.io/isentinel/shelly-voltage-telegram@sha256:" + "a" * 64
            ),
        }

    def decision(self, ready: int = 503) -> dict:
        """Compute a decision from locally supplied data only."""
        return AUDIT.decide(self.compose, self.timer, self.docker, ready)

    def test_realistic_offline_state_is_blocked(self) -> None:
        """Healthy running container is not a stopped writer."""
        report = self.decision()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(len(report["reasons"]), 6)
        self.assertIn("DOCKER_WRITER_NOT_STOPPED", report["reasons"])
        self.assertIn("UPDATER_TIMER_ENABLED", report["reasons"])
        self.assertFalse(report["writer_fenced"])

    def test_stopped_service_alone_is_not_enough(self) -> None:
        """A systemd stop cannot certify a running Docker writer."""
        self.compose["ActiveState"] = "inactive"
        report = self.decision(ready=200)
        self.assertIn("DOCKER_WRITER_NOT_STOPPED", report["reasons"])

    def test_disabled_timer_does_not_mask_docker_restart(self) -> None:
        """Disabling one timer cannot prove the container is quiesced."""
        self.timer["UnitFileState"] = "disabled"
        self.timer["ActiveState"] = "inactive"
        self.assertIn(
            "DOCKER_RESTART_POLICY_REQUIRES_REVIEW",
            self.decision(ready=200)["reasons"],
        )

    def test_inactive_writer_does_not_claim_authorization(self) -> None:
        """Even clean snapshots never authorize an actual maintenance."""
        self.compose.update(ActiveState="inactive", UnitFileState="disabled")
        self.timer.update(ActiveState="inactive", UnitFileState="disabled")
        self.docker.update(running="false", restart="no")
        report = self.decision(ready=200)
        self.assertEqual(report["status"], "READ_ONLY_PREFLIGHT_PASS")
        self.assertFalse(report["writer_fenced"])
        self.assertFalse(report["owner_maintenance_authorized"])
        self.assertFalse(report["public_photo_authorized"])

    def test_unpinned_image_is_blocked(self) -> None:
        """A local or mutable image tag never passes an identity check."""
        self.docker["image"] = "shelly-voltage:latest"
        self.assertIn(
            "SIGNED_IMAGE_IDENTITY_UNVERIFIED",
            self.decision(ready=200)["reasons"],
        )

    def test_unavailable_unit_is_blocked(self) -> None:
        """A missing service must never be interpreted as quiescence."""
        self.compose["LoadState"] = "not-found"
        self.assertIn(
            "COMPOSE_SERVICE_UNVERIFIED",
            self.decision(ready=200)["reasons"],
        )

    def test_unknown_ready_refuses_release(self) -> None:
        """Readiness can never be inferred from container health alone."""
        self.assertIn("SHELLY_NOT_READY", self.decision(ready=503)["reasons"])

    def test_systemd_timer_does_not_require_type(self) -> None:
        """A timer has no service Type property; avoid false errors."""
        reply = (
            "LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n"
        )
        with patch.object(AUDIT, "command_output", return_value=reply) as call:
            values = AUDIT.inspect_unit(AUDIT.UPDATE_TIMER)
        self.assertEqual(values, self.timer)
        self.assertNotIn("--property=Type", call.call_args.args[0])

    def test_systemd_service_includes_type(self) -> None:
        """Require service Type without applying it to .timer units."""
        reply = (
            "LoadState=loaded\nActiveState=active\n"
            "UnitFileState=enabled\nType=oneshot\n"
        )
        with patch.object(AUDIT, "command_output", return_value=reply) as call:
            values = AUDIT.inspect_unit(AUDIT.COMPOSE_UNIT)
        self.assertEqual(values, self.compose)
        self.assertIn("--property=Type", call.call_args.args[0])

    def test_docker_parses_only_safe_fields(self) -> None:
        """Query only safe named container metadata, not credentials."""
        response = "true|unless-stopped|" + self.docker["image"]
        with patch.object(AUDIT, "command_output", return_value=response):
            parsed = AUDIT.inspect_container()
        self.assertEqual(parsed, self.docker)

    def test_command_failure_is_fail_closed(self) -> None:
        """Docker inspect errors cannot produce a safety certificate."""
        failure = subprocess.CompletedProcess(
            args=["docker"], returncode=1, stdout="", stderr=""
        )
        with (
            patch.object(subprocess, "run", return_value=failure),
            self.assertRaises(AUDIT.Blocked),
        ):
            AUDIT.command_output(["docker", "inspect"])

    def test_audit_failure_never_authorizes(self) -> None:
        """Do not allow a failed systemd read to imply maintenance safety."""
        with patch.object(
            AUDIT, "inspect_unit", side_effect=AUDIT.Blocked("UNAVAILABLE")
        ):
            report = AUDIT.audit()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["owner_maintenance_authorized"])

    def test_jsonl_cli_has_no_side_effects(self) -> None:
        """Expose only non-secret status, without host write actions."""
        report = self.decision()
        with (
            patch.object(AUDIT, "audit", return_value=report),
            patch.object(sys, "argv", [str(SCRIPT), "--jsonl"]),
            redirect_stdout(io.StringIO()) as output,
        ):
            exit_code = AUDIT.main()
        self.assertEqual(exit_code, 2)
        self.assertFalse(json.loads(output.getvalue())["production_modified"])


if __name__ == "__main__":
    unittest.main()
