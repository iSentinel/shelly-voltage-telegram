"""Old updater and rollback must fail before acting under sole ownership."""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLY = ROOT / "scripts/apply-update.sh"
ROLLBACK = ROOT / "scripts/rollback.sh"


class LegacySupervisorGuardTests(unittest.TestCase):
    """Refuse old database restoration when protected supervisor is active."""

    def run_guard(
        self, script: Path, args: list[str], env_name: str
    ) -> subprocess.CompletedProcess[str]:
        """Invoke guarded shell entrypoint without Docker or real database."""
        environment = dict(os.environ)
        environment.pop("SVT_SUPERVISOR_OVERLAY", None)
        environment.pop("SVT_WRITER_FENCE_DIR", None)
        environment[env_name] = "/var/lib/svt-qa-only-candidate"
        return subprocess.run(
            ["bash", str(script), *args],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=6,
        )

    def test_recovery_blocked_by_supervisor_overlay(self) -> None:
        """Automatic legacy --recover cannot delete newest WAL history."""
        result = self.run_guard(APPLY, ["--recover"], "SVT_SUPERVISOR_OVERLAY")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("legacy updater blocked", result.stderr)

    def test_legacy_update_recovery_blocked_by_writer_fence(self) -> None:
        """The protected SQLite lock rejects old recovery at entry."""
        result = self.run_guard(APPLY, ["--recover"], "SVT_WRITER_FENCE_DIR")
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_legacy_update_dry_run_blocked_under_supervisor(self) -> None:
        """Even old update staging cannot bypass a future sole owner."""
        result = self.run_guard(
            APPLY,
            ["--version", "0.5.26-hw", "--dry-run"],
            "SVT_SUPERVISOR_OVERLAY",
        )
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_legacy_rollback_blocked_by_supervisor_overlay(self) -> None:
        """Old rollback cannot replace SQLite after supervised activation."""
        result = self.run_guard(
            ROLLBACK, ["--dry-run"], "SVT_SUPERVISOR_OVERLAY"
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("legacy rollback blocked", result.stderr)

    def test_legacy_rollback_blocked_by_writer_fence(self) -> None:
        """The old --confirm-db-replacement path is not an escape."""
        result = self.run_guard(
            ROLLBACK,
            ["--confirm-db-replacement"],
            "SVT_WRITER_FENCE_DIR",
        )
        self.assertEqual(result.returncode, 3, result.stderr)

    def test_sourced_legacy_functions_refuse_supervised_mode(self) -> None:
        """Other scripts cannot call old restore functions around main."""
        for action in (
            "recover_pending",
            "restore_snapshot_database",
            "activate sha256:fixture",
            "start_stack",
        ):
            with self.subTest(action=action):
                environment = dict(os.environ)
                environment["SVT_WRITER_FENCE_DIR"] = (
                    "/var/lib/svt-qa-only-candidate"
                )
                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f"source {APPLY}; {action}",
                    ],
                    env=environment,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=6,
                )
                self.assertEqual(result.returncode, 3)
                self.assertIn("legacy updater blocked", result.stderr)

    def test_help_still_available_while_supervised(self) -> None:
        """Help must not require touching Docker or the SQLite database."""
        for script in (APPLY, ROLLBACK):
            with self.subTest(script=script.name):
                result = self.run_guard(
                    script, ["--help"], "SVT_SUPERVISOR_OVERLAY"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
