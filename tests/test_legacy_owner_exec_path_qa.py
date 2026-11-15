"""Reject untrusted Docker/systemd executables in rollback preflight."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/legacy-rollback-frozen-owner-preflight.py"
SPEC = importlib.util.spec_from_file_location("qa_frozen_exec_path", SOURCE)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class FrozenOwnerExecPathTests(unittest.TestCase):
    """No real Docker/systemd command is executed by these tests."""

    def setUp(self) -> None:
        """Create a disposable fake binary beneath an owned private path."""
        temp = tempfile.TemporaryDirectory(prefix="qa-owner-exec-path-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.docker = self.bin / "docker"
        self.docker.write_text("#!/bin/sh\nprintf 'synthetic-stopped\n'\n")
        self.docker.chmod(0o755)

    def test_unlisted_command_rejected(self) -> None:
        """The preflight permits only Docker and systemctl executables."""
        with self.assertRaisesRegex(
            QA.FrozenOwnerRequired, "OWNER_COMMAND_NOT_ALLOWED"
        ):
            QA.trusted_command("sh")

    def test_missing_docker_rejected(self) -> None:
        """No hidden PATH fallback can authorize unknown owner state."""
        with (
            patch.dict(os.environ, {"PATH": str(self.root / "missing")}),
            self.assertRaisesRegex(
                QA.FrozenOwnerRequired, "OWNER_COMMAND_MISSING"
            ),
        ):
            QA.trusted_command("docker")

    @unittest.skipUnless(os.geteuid() == 0, "needs root-owned QA fixture")
    def test_private_root_owned_executable_allowed(self) -> None:
        """A trusted test-owned binary is safe to resolve absolutely."""
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertEqual(QA.trusted_command("docker"), self.docker)

    @unittest.skipUnless(os.geteuid() == 0, "needs root-owned QA fixture")
    def test_checked_executable_used_as_absolute_path(self) -> None:
        """Command execution must use the validated absolute filename."""
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertEqual(
                QA.readonly_command(["docker", "inspect"]), "synthetic-stopped"
            )

    def test_writable_parent_rejected(self) -> None:
        """Reject fake Docker installed in group/world-writable parents."""
        self.bin.chmod(0o777)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.require_private_executable_path(self.docker)

    def test_writable_executable_rejected(self) -> None:
        """A writable owner-command inode is not acceptable evidence."""
        self.docker.chmod(0o777)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.require_private_executable_path(self.docker)

    def test_non_executable_inode_rejected(self) -> None:
        """A plain data file is not a trusted Docker binary."""
        self.docker.chmod(0o644)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.require_private_executable_path(self.docker)

    def test_symlink_executable_rejected(self) -> None:
        """No executable may be redirected through an untrusted alias."""
        alias = self.bin / "systemctl"
        alias.symlink_to(self.docker)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.require_private_executable_path(alias)

    def test_linked_parent_rejected(self) -> None:
        """A renamed directory symlink cannot substitute a privileged CLI."""
        alias = self.root / "linked-bin"
        alias.symlink_to(self.bin)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.require_private_executable_path(alias / "docker")

    def test_bad_path_under_fake_path_rejected(self) -> None:
        """An untrusted PATH search result cannot be executed."""
        self.bin.chmod(0o777)
        with (
            patch.dict(os.environ, {"PATH": str(self.bin)}),
            self.assertRaises(QA.FrozenOwnerRequired),
        ):
            QA.trusted_command("docker")

    def test_extra_hardlink_refused(self) -> None:
        """Multiple aliases for an owner command invalidate inode trust."""
        alias = self.bin / "second-docker"
        os.link(self.docker, alias)
        with self.assertRaises(QA.FrozenOwnerRequired):
            QA.require_private_executable_path(self.docker)

    def test_nonroot_owned_fake_is_refused(self) -> None:
        """A user-owned binary must never impersonate root-owned Docker."""
        if os.geteuid() == 0:
            self.skipTest("root-owned fixture by design")
        with (
            patch.dict(os.environ, {"PATH": str(self.bin)}),
            self.assertRaises(QA.FrozenOwnerRequired),
        ):
            QA.trusted_command("docker")


if __name__ == "__main__":
    unittest.main()
