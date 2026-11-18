"""Fail-closed source-only tests for dual systemd/Docker start owners."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/boot-ownership-audit.py"
)
SPEC = importlib.util.spec_from_file_location("boot_ownership_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)

UNIT = """[Unit]
Description=Test-only Shelly unit
[Service]
Type=oneshot
ExecStartPre=/bin/bash /opt/shelly-voltage/scripts/apply-update.sh --recover
ExecStart=/bin/bash /opt/shelly-voltage/scripts/start-compose.sh
ExecStop=/usr/bin/docker compose stop
[Install]
WantedBy=multi-user.target
"""


class BootOwnerAuditTests(unittest.TestCase):
    """Model only unit and restart metadata; never touch Docker or bot."""

    def assess(
        self,
        installed=UNIT,
        source=UNIT,
        pending=False,
        policy="no",
        running=True,
    ):
        """Exercise a pure policy decision with explicit synthetic evidence."""

        return AUDIT.assess(installed, source, pending, policy, running)

    def assert_blocked(self, reason, **kwargs):
        """Require a specific reason without granting release authority."""

        result = self.assess(**kwargs)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(reason, result["reasons"])
        self.assertFalse(result["boot_continuity_verified"])
        self.assertFalse(result["source_install_authorized"])
        self.assertFalse(result["rollback_authorized"])
        self.assertFalse(result["production_modified"])

    def test_matching_single_restart_owner_is_static_only(self):
        """Static parity never counts as full boot/restart acceptance."""

        result = self.assess()
        self.assertEqual(result["status"], "PASS_STATIC_EVIDENCE_ONLY")
        self.assertFalse(result["boot_continuity_verified"])
        self.assertFalse(result["single_writer_fenced_verified"])
        self.assertFalse(result["rollback_authorized"])

    def test_installed_unit_differs_from_repository(self):
        """A missing repository prestart hook must be reported."""

        source = UNIT.replace(
            "ExecStartPre=" + AUDIT.EXPECTED_RECOVER + "\n", ""
        )
        self.assert_blocked("BOOT_INSTALLED_SOURCE_DRIFT", source=source)

    def test_missing_live_recovery_prestart_denied(self):
        """Do not infer boot safety from a plain Compose ExecStart."""

        installed = UNIT.replace(
            "ExecStartPre=" + AUDIT.EXPECTED_RECOVER + "\n", ""
        )
        self.assert_blocked(
            "BOOT_RECOVERY_GATE_MISSING_OR_CHANGED", installed=installed
        )

    def test_pending_archive_requires_review(self):
        """An unchecked pending update must not be treated as boot-ready."""

        self.assert_blocked(
            "BOOT_PENDING_RECOVERY_REQUIRES_REVIEW", pending=True
        )

    def test_unless_stopped_detected_as_competing_owner(self):
        """Docker restart policy may compete with systemd ExecStartPre."""

        self.assert_blocked(
            "BOOT_DOCKER_RESTART_OWNER_UNFENCED", policy="unless-stopped"
        )

    def test_always_and_on_failure_restart_are_not_proven(self):
        """Any independent automatic Docker restart is still an owner."""

        for policy in ("always", "on-failure"):
            with self.subTest(policy=policy):
                self.assert_blocked(
                    "BOOT_DOCKER_RESTART_OWNER_UNFENCED", policy=policy
                )

    def test_unknown_policy_is_not_assumed_safe(self):
        """Do not assign restart ownership to an unknown Docker policy."""

        self.assert_blocked("BOOT_RESTART_POLICY_UNKNOWN", policy="custom")

    def test_stopped_container_is_not_marked_running(self):
        """State comparison never launches the bot to validate health."""

        self.assert_blocked("BOOT_CONTAINER_NOT_RUNNING", running=False)

    def test_changed_service_execstart_denied(self):
        """Unexpected start commands cannot pass source comparison."""

        installed = UNIT.replace(
            AUDIT.EXPECTED_START, "/bin/bash /tmp/start.sh"
        )
        self.assert_blocked("BOOT_START_PATH_UNEXPECTED", installed=installed)

    def test_changed_service_execstop_denied(self):
        """Do not permit an unrecognized Docker stop path."""

        installed = UNIT.replace(AUDIT.EXPECTED_STOP, "/bin/true")
        self.assert_blocked("BOOT_STOP_PATH_UNEXPECTED", installed=installed)

    def test_changed_service_type_denied(self):
        """The deployed service must match the observed oneshot type."""

        installed = UNIT.replace("Type=oneshot", "Type=simple")
        self.assert_blocked(
            "BOOT_SERVICE_TYPE_UNEXPECTED", installed=installed
        )

    def test_duplicate_recovery_hook_denied(self):
        """Ambiguous systemd directives must never be silently accepted."""

        installed = UNIT.replace(
            "ExecStart=",
            "ExecStartPre=/bin/true\nExecStart=",
        )
        with self.assertRaisesRegex(
            AUDIT.Blocked, "SERVICE_UNIT_DUPLICATED_DIRECTIVE"
        ):
            self.assess(installed=installed)

    def test_symlinked_unit_denied(self):
        """Never follow symbolic links for input unit provenance."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "real.service"
            original.write_text(UNIT)
            alias = root / "alias.service"
            alias.symlink_to(original)
            with self.assertRaisesRegex(
                AUDIT.Blocked, "SERVICE_UNIT_PATH_UNSAFE"
            ):
                AUDIT.unit_text(alias)

    def test_pending_symlink_denied(self):
        """Do not follow an attacker-selected pending file."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "pending"
            marker.symlink_to(root / "other")
            with self.assertRaisesRegex(
                AUDIT.Blocked, "PENDING_MARKER_PATH_UNSAFE"
            ):
                AUDIT.pending_status(marker)

    def test_plain_pending_file_is_reported_without_reading(self):
        """Pending marker contents should not be leaked in audit output."""

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pending-update"
            marker.write_text("secret synthetic token")
            self.assertTrue(AUDIT.pending_status(marker))

    def test_docker_inspect_reads_only_policy_and_running(self):
        """Inspect command must omit environment and secret fields."""

        result = subprocess.CompletedProcess(
            [], 0, stdout="unless-stopped|true\n"
        )
        with patch.object(
            AUDIT.subprocess, "run", return_value=result
        ) as call:
            policy, running = AUDIT.inspect_restart_policy("test-bot")
        self.assertEqual((policy, running), ("unless-stopped", True))
        args = call.call_args.args[0]
        self.assertEqual(args[:3], ["docker", "inspect", "test-bot"])
        self.assertNotIn("Env", " ".join(args))
        self.assertNotIn("exec", args)

    def test_docker_inspect_failure_denied(self):
        """Unavailable Docker metadata must not be guessed."""

        result = subprocess.CompletedProcess([], 1, stderr="failed")
        with (
            patch.object(AUDIT.subprocess, "run", return_value=result),
            self.assertRaisesRegex(AUDIT.Blocked, "CONTAINER_INSPECT_FAILED"),
        ):
            AUDIT.inspect_restart_policy("test-bot")


if __name__ == "__main__":
    unittest.main()
