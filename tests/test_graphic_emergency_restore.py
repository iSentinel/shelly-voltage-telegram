"""Isolated tests for independent systemd graphic-cutover restoration."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/graphic-emergency-restore.py"
SPEC = importlib.util.spec_from_file_location("graphic_restore", SCRIPT)
assert SPEC and SPEC.loader
WATCHDOG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCHDOG)


class GraphicEmergencyRestoreTests(unittest.TestCase):
    """No test may mutate production systemd, Docker or Telegram state."""

    def setUp(self):
        """Use an existing-looking private owner file without reading it."""

        self.owner = Path("/synthetic/owner-approval.json")

    def test_production_requires_healthy_container(self):
        """An unhealthy writer is never safe to disarm or skip restarting."""

        healthy = ["active", "enabled", "true", "healthy", "unless-stopped"]
        for status in ("unhealthy", "starting", "<no value>", ""):
            with (
                self.subTest(health=status),
                mock.patch.object(
                    WATCHDOG,
                    "state",
                    side_effect=healthy[:3] + [status],
                ),
            ):
                self.assertFalse(WATCHDOG.production_running())
        with mock.patch.object(WATCHDOG, "state", side_effect=healthy):
            self.assertTrue(WATCHDOG.production_running())

    def test_health_probe_failure_does_not_claim_restoration(self):
        """Docker inspection errors must keep the timer available."""

        for status in (
            "SERVICE_COMMAND_UNAVAILABLE",
            "SERVICE_COMMAND_FAILED",
        ):
            with (
                self.subTest(reason=status),
                mock.patch.object(
                    WATCHDOG,
                    "state",
                    side_effect=[
                        "active",
                        "enabled",
                        "true",
                        WATCHDOG.RestoreBlocked(status),
                    ],
                ),
            ):
                self.assertFalse(WATCHDOG.production_running())

    def test_disarm_rejects_unhealthy_container(self):
        """A running but unhealthy container must not disable the timer."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(
                WATCHDOG,
                "state",
                side_effect=["active", "enabled", "true", "unhealthy"],
            ),
            mock.patch.object(WATCHDOG, "command") as command,
            self.assertRaisesRegex(WATCHDOG.RestoreBlocked, "NOT_RESTORED"),
        ):
            WATCHDOG.disarm()
        command.assert_not_called()

    def test_arm_requires_root_and_bounded_deadline(self):
        """Unprivileged or too-long maintenance cannot arm a timer."""

        for uid, minutes in ((1000, 15), (0, 0), (0, 31)):
            with (
                self.subTest(uid=uid, minutes=minutes),
                mock.patch.object(WATCHDOG.os, "geteuid", return_value=uid),
                mock.patch.object(WATCHDOG, "command") as executed,
                self.assertRaisesRegex(WATCHDOG.RestoreBlocked, "ARM_UNSAFE"),
            ):
                WATCHDOG.arm(minutes, self.owner)
            executed.assert_not_called()

    def test_arm_requires_owner_approval_before_touching_host(self):
        """Missing approval cannot install a stable copy or arm a unit."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(WATCHDOG, "approval", return_value=False),
            mock.patch.object(WATCHDOG, "install_stable_copy") as copy,
            self.assertRaisesRegex(WATCHDOG.RestoreBlocked, "OWNER_APPROVAL"),
        ):
            WATCHDOG.arm(15, self.owner)
        copy.assert_not_called()

    def test_arm_refuses_unhealthy_baseline(self):
        """Offline physical meter is not equivalent to a broken Docker host."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(WATCHDOG, "approval", return_value=True),
            mock.patch.object(
                WATCHDOG, "production_running", return_value=False
            ),
            mock.patch.object(WATCHDOG, "command") as command,
            self.assertRaisesRegex(WATCHDOG.RestoreBlocked, "BASELINE"),
        ):
            WATCHDOG.arm(15, self.owner)
        command.assert_not_called()

    def test_arm_schedules_restoration_with_explicit_execute(self):
        """A timer must invoke a real bounded --restore --execute action."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(WATCHDOG, "approval", return_value=True),
            mock.patch.object(
                WATCHDOG, "production_running", return_value=True
            ),
            mock.patch.object(
                WATCHDOG, "timer_armed", side_effect=[False, True]
            ),
            mock.patch.object(WATCHDOG, "install_stable_copy") as copy,
            mock.patch.object(WATCHDOG, "command") as command,
        ):
            result = WATCHDOG.arm(15, self.owner)
        call = command.call_args.args[0]
        self.assertEqual(result["status"], "ARMED")
        self.assertIn("--on-active=15min", call)
        self.assertIn("--on-unit-active=1min", call)
        self.assertEqual(call[-2:], ["--restore", "--execute"])
        self.assertFalse(result["writer_stopped"])
        copy.assert_called_once()

    def test_already_running_restoration_is_safe_noop(self):
        """A late timer may not restart a healthy restored service."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(
                WATCHDOG, "production_running", return_value=True
            ),
            mock.patch.object(WATCHDOG, "command") as command,
        ):
            self.assertEqual(WATCHDOG.restore()["status"], "ALREADY_RESTORED")
        command.assert_not_called()

    def test_restore_reenables_service_and_docker_restart(self):
        """Lost SSH sessions cannot strand a stopped monitor."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(
                WATCHDOG, "production_running", side_effect=[False, True]
            ),
            mock.patch.object(WATCHDOG, "command") as command,
        ):
            self.assertEqual(WATCHDOG.restore()["status"], "RESTORED")
        calls = [item.args[0] for item in command.call_args_list]
        self.assertIn(
            ["systemctl", "unmask", "--runtime", WATCHDOG.UNIT], calls
        )
        self.assertIn(
            [
                "docker",
                "update",
                "--restart=unless-stopped",
                WATCHDOG.CONTAINER,
            ],
            calls,
        )
        self.assertIn(["systemctl", "enable", WATCHDOG.UNIT], calls)
        self.assertIn(["systemctl", "restart", WATCHDOG.UNIT], calls)

    def test_restore_still_starts_service_if_docker_was_recreated(self):
        """A missing container cannot prevent systemd from recovering."""

        def execute(args, timeout=30):
            if args[:2] == ["docker", "update"]:
                raise WATCHDOG.RestoreBlocked("SERVICE_COMMAND_FAILED")

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(
                WATCHDOG, "production_running", side_effect=[False, True]
            ),
            mock.patch.object(WATCHDOG, "command", side_effect=execute) as run,
        ):
            self.assertEqual(WATCHDOG.restore()["status"], "RESTORED")
        self.assertTrue(
            any("restart" in c.args[0] for c in run.call_args_list)
        )

    def test_disarm_requires_restored_monitoring(self):
        """Never cancel the independent safeguard while writer is down."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(
                WATCHDOG, "production_running", return_value=False
            ),
            mock.patch.object(WATCHDOG, "command") as command,
            self.assertRaisesRegex(WATCHDOG.RestoreBlocked, "NOT_RESTORED"),
        ):
            WATCHDOG.disarm()
        command.assert_not_called()

    def test_disarm_stops_only_watchdog_timer(self):
        """Once restored, cancelling must not stop the production service."""

        with (
            mock.patch.object(WATCHDOG.os, "geteuid", return_value=0),
            mock.patch.object(
                WATCHDOG, "production_running", return_value=True
            ),
            mock.patch.object(WATCHDOG, "timer_armed", return_value=True),
            mock.patch.object(WATCHDOG, "command") as command,
        ):
            self.assertEqual(WATCHDOG.disarm()["status"], "DISARMED")
        command.assert_called_once_with(["systemctl", "stop", WATCHDOG.TIMER])

    def test_mutation_requires_explicit_execution(self):
        """A missing --execute switch must never schedule restoration."""

        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["watchdog", "--arm"]),
            mock.patch.object(WATCHDOG, "arm") as arm,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(WATCHDOG.main(), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "BLOCKED")
        arm.assert_not_called()

    def test_default_audit_is_read_only(self):
        """Read-only inspection must not alter any installed unit."""

        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["watchdog"]),
            mock.patch.object(WATCHDOG, "timer_armed", return_value=False),
            mock.patch.object(
                WATCHDOG, "production_running", return_value=True
            ),
            mock.patch.object(WATCHDOG, "command") as command,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(WATCHDOG.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "READ_ONLY")
        command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
