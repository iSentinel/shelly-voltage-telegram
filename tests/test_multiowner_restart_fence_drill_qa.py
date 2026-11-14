"""Verify restart-owner fencing only on private synthetic state fixtures."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/multiowner-restart-fence-drill-qa.py"
)
SPEC = importlib.util.spec_from_file_location("multiowner_fence_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class MultiOwnerFenceTests(unittest.TestCase):
    """Run the production-inert fixture workflow, never real Docker."""

    def setUp(self) -> None:
        """Create a private model with seven active restart owners."""
        temporary = tempfile.TemporaryDirectory(
            prefix="shelly-fence-multiowner-", dir="/tmp"
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / QA.MARKER).write_text(QA.MARKER_VALUE)
        lock = self.root / QA.LOCK
        lock.touch(mode=0o600)
        self.original = {
            "updater_enabled": True,
            "updater_active": False,
            "compose_enabled": True,
            "compose_active": True,
            "docker_running": True,
            "restart_policy": "unless-stopped",
            "boot_recovery_hook": True,
            "pending_update": False,
            "snapshot_a": False,
            "snapshot_b": False,
            "source_version": "0.5.21-hw",
            "image_version": "0.5.21-hw",
            "sqlite_digest": "a" * 64,
        }
        QA.write_private(self.root, QA.STATE, self.original)

    def run_cli(self, *flags: str) -> int:
        """Invoke only the private simulator in the current test process."""
        args = [str(SCRIPT), "--root", str(self.root), *flags]
        with (
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def read(self) -> dict:
        """Load the private fixture after one transaction."""
        return QA.read_private(self.root / QA.STATE)

    def assert_owners_restored(self) -> None:
        """Verify all five start owners returned to their original state."""
        state = self.read()
        for name in QA.OWNER_FIELDS:
            self.assertEqual(state[name], self.original[name])
        self.assertEqual(
            state["sqlite_digest"], self.original["sqlite_digest"]
        )

    def test_dry_run_is_no_write(self) -> None:
        """A dry run cannot create a transaction journal or snapshot."""
        self.assertEqual(self.run_cli("--dry-run"), 0)
        self.assertFalse((self.root / QA.JOURNAL).exists())
        self.assertEqual(self.read(), self.original)

    def test_normal_fence_and_restore(self) -> None:
        """Capture both model checkpoints and reenable owner states."""
        self.assertEqual(self.run_cli("--simulate-disposable"), 0)
        self.assert_owners_restored()
        self.assertTrue(self.read()["snapshot_a"])
        self.assertTrue(self.read()["snapshot_b"])
        self.assertFalse((self.root / QA.JOURNAL).exists())

    def test_second_simulation_denied(self) -> None:
        """An existing captured pair cannot be silently overwritten."""
        self.assertEqual(self.run_cli("--simulate-disposable"), 0)
        self.assertEqual(self.run_cli("--simulate-disposable"), 3)

    def test_process_failure_during_snapshot_a_recover(self) -> None:
        """Another command can recover synthetic owners after failure."""
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--simulate-disposable",
                "--fail-after",
                "capture_a",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
        )
        self.assertEqual(result.returncode, 3)
        self.assertTrue((self.root / QA.JOURNAL).exists())
        self.assertFalse(self.read()["docker_running"])
        self.assertEqual(self.run_cli("--recover-disposable"), 0)
        self.assert_owners_restored()
        self.assertFalse((self.root / QA.JOURNAL).exists())

    def test_fail_after_stop_recover(self) -> None:
        """A writer stopped before capture remains recoverable."""
        self.assertEqual(
            self.run_cli(
                "--simulate-disposable", "--fail-after", "stop_writer"
            ),
            3,
        )
        self.assertFalse(self.read()["docker_running"])
        self.assertEqual(self.run_cli("--recover-disposable"), 0)
        self.assert_owners_restored()

    def test_fail_after_backup_b_recover(self) -> None:
        """Do not use a completed pair to claim a completed maintenance."""
        self.assertEqual(
            self.run_cli("--simulate-disposable", "--fail-after", "capture_b"),
            3,
        )
        self.assertTrue(self.read()["snapshot_b"])
        self.assertEqual(self.run_cli("--recover-disposable"), 0)
        self.assert_owners_restored()

    def test_unexpected_restart_after_stop_fails(self) -> None:
        """Writer restart before first backup invalidates the transaction."""
        self.assertEqual(
            self.run_cli(
                "--simulate-disposable",
                "--inject-restart-after",
                "stop_writer",
            ),
            3,
        )
        self.assertFalse(self.read()["snapshot_a"])
        self.assertTrue((self.root / QA.JOURNAL).exists())

    def test_unexpected_restart_after_a_fails(self) -> None:
        """Writer restart between A and B is rejected before B."""
        self.assertEqual(
            self.run_cli(
                "--simulate-disposable", "--inject-restart-after", "capture_a"
            ),
            3,
        )
        self.assertTrue(self.read()["snapshot_a"])
        self.assertFalse(self.read()["snapshot_b"])

    def test_unexpected_restart_after_b_fails(self) -> None:
        """Before restoring restart policy, fence must still hold."""
        self.assertEqual(
            self.run_cli(
                "--simulate-disposable", "--inject-restart-after", "capture_b"
            ),
            3,
        )
        self.assertTrue((self.root / QA.JOURNAL).exists())

    def test_pending_update_is_blocked(self) -> None:
        """A pending boot recovery cannot be silently ignored."""
        state = self.original.copy()
        state["pending_update"] = True
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--simulate-disposable"), 3)
        self.assertFalse((self.root / QA.JOURNAL).exists())

    def test_updater_activity_is_blocked(self) -> None:
        """An updater already running prevents the simulated fence."""
        state = self.original.copy()
        state["updater_active"] = True
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--simulate-disposable"), 3)

    def test_mismatched_signed_source_is_blocked(self) -> None:
        """The live v0.5.15 versus image v0.5.21 is not ready."""
        state = self.original.copy()
        state["source_version"] = "0.5.15-hw"
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--simulate-disposable"), 3)

    def test_restart_policy_remains_no_at_writer_resume(self) -> None:
        """Prove controlled start precedes reenabling the restart policy."""
        seen: list[tuple[str, str]] = []
        original = QA.action

        def check_order(state: dict, step: str, saved: dict) -> None:
            if step == "start_writer":
                seen.append((step, state["restart_policy"]))
            original(state, step, saved)

        with patch.object(QA, "action", side_effect=check_order):
            self.assertEqual(self.run_cli("--simulate-disposable"), 0)
        self.assertEqual(seen, [("start_writer", "no")])
        self.assert_owners_restored()

    def test_failure_after_controlled_resume_can_recover(self) -> None:
        """A partially restored startup policy must retain its journal."""
        self.assertEqual(
            self.run_cli(
                "--simulate-disposable", "--fail-after", "start_writer"
            ),
            3,
        )
        self.assertEqual(self.read()["restart_policy"], "no")
        self.assertEqual(self.run_cli("--recover-disposable"), 0)
        self.assert_owners_restored()

    def test_changed_source_identity_blocks_recovery(self) -> None:
        """Do not conceal source version drift during interrupted upgrade."""
        self.run_cli("--simulate-disposable", "--fail-after", "capture_a")
        state = self.read()
        state["source_version"] = "0.5.15-hw"
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)
        self.assertEqual(self.read()["source_version"], "0.5.15-hw")

    def test_changed_image_identity_blocks_recovery(self) -> None:
        """A surprise signed-image change makes the journal insufficient."""
        self.run_cli("--simulate-disposable", "--fail-after", "capture_a")
        state = self.read()
        state["image_version"] = "0.5.22-hw"
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)

    def test_updater_started_during_failure_blocks_recovery(self) -> None:
        """A previously inactive updater must stay inactive in recovery."""
        self.run_cli("--simulate-disposable", "--fail-after", "capture_a")
        state = self.read()
        state["updater_active"] = True
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)

    def test_boot_recovery_hook_changed_blocks_recovery(self) -> None:
        """Unexpected boot-hook configuration needs independent review."""
        self.run_cli("--simulate-disposable", "--fail-after", "capture_a")
        state = self.read()
        state["boot_recovery_hook"] = False
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)

    def test_changed_sqlite_blocks_recovery(self) -> None:
        """Never restore old synthetic owner state over changed history."""
        self.run_cli("--simulate-disposable", "--fail-after", "capture_a")
        state = self.read()
        state["sqlite_digest"] = "b" * 64
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)
        self.assertEqual(self.read()["sqlite_digest"], "b" * 64)

    def test_changed_pending_update_blocks_recovery(self) -> None:
        """An unexpected boot-recovery request requires independent review."""
        self.run_cli("--simulate-disposable", "--fail-after", "capture_a")
        state = self.read()
        state["pending_update"] = True
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)

    def test_recover_without_journal_is_blocked(self) -> None:
        """Prevent stale or invented synthetic recovery operations."""
        self.assertEqual(self.run_cli("--recover-disposable"), 3)

    def test_corrupt_journal_is_blocked(self) -> None:
        """Reject untrusted recovery metadata without modifying the writer."""
        journal = self.root / QA.JOURNAL
        journal.write_text("{broken")
        journal.chmod(0o600)
        self.assertEqual(self.run_cli("--recover-disposable"), 3)
        self.assertEqual(self.read(), self.original)

    def test_linked_state_is_blocked(self) -> None:
        """Do not follow a symlink to potentially sensitive state."""
        state = self.root / QA.STATE
        state.unlink()
        state.symlink_to(self.root / QA.MARKER)
        self.assertEqual(self.run_cli("--dry-run"), 3)

    def test_linked_root_is_blocked(self) -> None:
        """Refuse a symlink in the scratch path namespace."""
        alias = self.root.parent / (self.root.name + "-link")
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        with self.assertRaises(QA.Blocked):
            QA.safe_root(alias)

    def test_production_path_is_blocked(self) -> None:
        """Explicitly forbid any real Shelly source/DB path."""
        with self.assertRaises(QA.Blocked):
            QA.safe_root(Path("/opt/shelly-voltage"))

    def test_missing_marker_is_blocked(self) -> None:
        """A valid directory is not implicitly an authorized QA target."""
        (self.root / QA.MARKER).unlink()
        self.assertEqual(self.run_cli("--simulate-disposable"), 3)

    def test_locked_peer_is_blocked(self) -> None:
        """A competing QA controller must not write a second journal."""
        descriptor = os.open(self.root / QA.LOCK, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_cli("--simulate-disposable"), 3)
        finally:
            os.close(descriptor)
        self.assertFalse((self.root / QA.JOURNAL).exists())

    def test_bad_restart_policy_is_blocked(self) -> None:
        """Unknown Docker policy cannot bypass fixture validation."""
        state = self.original.copy()
        state["restart_policy"] = "unknown"
        QA.write_private(self.root, QA.STATE, state)
        self.assertEqual(self.run_cli("--simulate-disposable"), 3)


if __name__ == "__main__":
    unittest.main()
