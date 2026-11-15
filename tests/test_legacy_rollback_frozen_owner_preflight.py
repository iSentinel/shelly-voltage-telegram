"""Fail-closed tests for inherited-G legacy rollback admission."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/legacy-rollback-frozen-owner-preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "qa_legacy_rollback_freeze", SCRIPT
)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class LegacyRollbackFreezeTests(unittest.TestCase):
    """Only synthetic files and mocked Docker/systemd are accessible."""

    def setUp(self) -> None:
        """Create a private local fake G inode with stable identity."""
        tmp = tempfile.TemporaryDirectory(prefix="qa-legacy-freeze-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.gate = self.root / "gate"
        self.gate.mkdir(mode=0o700)
        self.g = self.gate / "G"
        self.g.touch()
        self.g.chmod(0o640)

    def cli(self, *args: str) -> int:
        """No-argument mode never probes production owner state."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(
                QA, "inspect_frozen_owner", side_effect=AssertionError
            ),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """No live Docker, systemd or file inspection without --check."""
        self.assertEqual(self.cli(), 0)

    def test_missing_g_fd_fails_before_inspection(self) -> None:
        """A caller may not infer or invent the maintenance lock FD."""
        self.assertEqual(self.cli("--check"), 2)

    def test_untrusted_symlinked_g_refused(self) -> None:
        """Do not follow a named lock symlink to another inode."""
        link = self.gate / "other"
        link.symlink_to(self.g)
        self.g.unlink()
        self.g.symlink_to(link)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.trusted_gate(self.gate)

    def test_writable_g_inode_refused(self) -> None:
        """The maintenance gate cannot be group-writable."""
        self.g.chmod(0o666)
        with self.assertRaisesRegex(
            QA.FrozenOwnerRequired,
            "G_LOCK_INODE_UNSAFE|GATE_DIRECTORY_UNTRUSTED",
        ):
            QA.trusted_gate(self.gate)

    def test_wrong_g_directory_permissions_refused(self) -> None:
        """A world-writable parent is not trusted admission state."""
        self.gate.chmod(0o777)
        with self.assertRaisesRegex(
            QA.FrozenOwnerRequired,
            "GATE_DIRECTORY_UNSAFE_MODE|GATE_DIRECTORY_UNTRUSTED",
        ):
            QA.trusted_gate(self.gate)

    def test_no_lock_on_matching_fd_refused(self) -> None:
        """Same inode is insufficient without an inherited exclusive lock."""
        with (
            self.g.open("rb") as original,
            self.assertRaisesRegex(
                QA.FrozenOwnerRequired, "INHERITED_G_EX_NOT_HELD"
            ),
        ):
            QA.require_inherited_exclusive(self.g, original.fileno())

    def test_real_g_ex_on_matching_fd_passes(self) -> None:
        """Actual Linux fdinfo reports WRITE flock on held descriptor."""
        with self.g.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            QA.require_inherited_exclusive(self.g, held.fileno())

    def test_separate_fd_does_not_inherit_g_ex(self) -> None:
        """An unrelated opener cannot impersonate the maintenance owner."""
        with self.g.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.g.open("rb") as other,
                self.assertRaisesRegex(
                    QA.FrozenOwnerRequired, "INHERITED_G_EX_NOT_HELD"
                ),
            ):
                QA.require_inherited_exclusive(self.g, other.fileno())

    def test_different_g_inode_refused(self) -> None:
        """After inode substitution an old lock is not valid admission."""
        with self.g.open("rb") as original:
            fcntl.flock(original, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.g.unlink()
            self.g.touch()
            self.g.chmod(0o640)
            with self.assertRaisesRegex(
                QA.FrozenOwnerRequired, "G_FD_DIFFERENT_INODE"
            ):
                QA.require_inherited_exclusive(self.g, original.fileno())

    def test_invalid_fd_number_refused(self) -> None:
        """Never open caller-controlled or special fd0/1/2 as G."""
        with self.assertRaisesRegex(
            QA.FrozenOwnerRequired, "G_FD_OUT_OF_RANGE"
        ):
            QA.require_inherited_exclusive(self.g, 1)

    def test_docker_no_restart_and_stopped_ok(self) -> None:
        """A stopped container must also have restart policy=no."""
        with patch.object(QA, "readonly_command", return_value="false|no|2"):
            self.assertEqual(QA.docker_owner_state(), (False, "no", 2))

    def test_docker_running_refused(self) -> None:
        """Never restore SQLite while a legacy container may write."""
        with (
            patch.object(
                QA, "docker_owner_state", return_value=(True, "no", 0)
            ),
            self.assertRaisesRegex(QA.FrozenOwnerRequired, "STILL_RUNNING"),
        ):
            QA.require_docker_frozen()

    def test_docker_autorestart_refused(self) -> None:
        """Stopping Docker without changing policy is not a freeze."""
        with (
            patch.object(
                QA,
                "docker_owner_state",
                return_value=(False, "unless-stopped", 0),
            ),
            self.assertRaisesRegex(
                QA.FrozenOwnerRequired, "AUTORESTART_NOT_FROZEN"
            ),
        ):
            QA.require_docker_frozen()

    def test_docker_state_change_refused(self) -> None:
        """A restart-count race between two reads must fail closed."""
        with (
            patch.object(
                QA,
                "docker_owner_state",
                side_effect=[(False, "no", 0), (False, "no", 1)],
            ),
            patch.object(QA.time, "sleep"),
            self.assertRaisesRegex(QA.FrozenOwnerRequired, "STATE_CHANGED"),
        ):
            QA.require_docker_frozen()

    def test_docker_two_stable_observations_required(self) -> None:
        """A known stopped owner is observed twice before acceptance."""
        with (
            patch.object(
                QA, "docker_owner_state", return_value=(False, "no", 7)
            ) as probe,
            patch.object(QA.time, "sleep"),
        ):
            self.assertEqual(QA.require_docker_frozen(), 7)
        self.assertEqual(probe.call_count, 2)

    def test_unparseable_docker_state_refused(self) -> None:
        """Missing or unexpected Docker fields are never accepted."""
        with (
            patch.object(QA, "readonly_command", return_value="unknown"),
            self.assertRaisesRegex(QA.FrozenOwnerRequired, "UNVERIFIABLE"),
        ):
            QA.docker_owner_state()

    def test_systemd_active_service_refused(self) -> None:
        """Enabled or masked is insufficient when service is active."""
        with (
            patch.object(
                QA,
                "readonly_command",
                return_value="ActiveState=active\nUnitFileState=masked",
            ),
            self.assertRaisesRegex(QA.FrozenOwnerRequired, "STILL_ACTIVE"),
        ):
            QA.require_systemd_frozen(QA.SERVICES[0])

    def test_systemd_inactive_but_enabled_refused(self) -> None:
        """A disabled but unmasked service remains manually startable."""
        with (
            patch.object(
                QA,
                "readonly_command",
                return_value="ActiveState=inactive\nUnitFileState=disabled",
            ),
            self.assertRaisesRegex(QA.FrozenOwnerRequired, "NOT_MASKED"),
        ):
            QA.require_systemd_frozen(QA.SERVICES[0])

    def test_systemd_inactive_runtime_mask_accepted(self) -> None:
        """Only masked inactive known units pass the narrow prerequisite."""
        with patch.object(
            QA,
            "readonly_command",
            return_value="ActiveState=inactive\nUnitFileState=masked-runtime",
        ):
            QA.require_systemd_frozen(QA.SERVICES[0])

    def test_full_fake_frozen_result_still_no_go(self) -> None:
        """Known owner tests alone cannot authorize rollback/release."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(QA, "trusted_gate", return_value=self.g),
            patch.object(QA, "require_inherited_exclusive"),
            patch.object(QA, "require_systemd_frozen"),
            patch.object(QA, "require_docker_frozen", return_value=0),
        ):
            result = QA.inspect_frozen_owner(self.gate, 7)
        self.assertEqual(result["release_decision"], "NO_GO")
        self.assertFalse(result["final_cutover_authorized"])
        self.assertFalse(
            result["all_manual_install_update_rollback_owners_proven"]
        )

    def test_fail_closed_cli_exit_three(self) -> None:
        """A bad inherited lock must never return a positive status."""
        with (
            patch.object(
                sys,
                "argv",
                [
                    "qa",
                    "--check",
                    "--gate-dir",
                    str(self.gate),
                    "--inherited-g-fd",
                    "8",
                ],
            ),
            patch.object(
                QA,
                "inspect_frozen_owner",
                side_effect=QA.FrozenOwnerRequired("OLD_WRITER"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as result,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("OWNER_FREEZE_REFUSED", result.getvalue())


if __name__ == "__main__":
    unittest.main()
