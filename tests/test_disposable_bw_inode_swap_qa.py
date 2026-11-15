"""Default-inert unit checks for private B/W lock inode replacement QA."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-bw-inode-swap-qa.py"
)
SPEC = importlib.util.spec_from_file_location("qa_inode_swap", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class InodeSwapQATests(unittest.TestCase):
    """Unit suite does not touch Docker, production or real locks."""

    def setUp(self) -> None:
        """Create only an isolated scratch directory."""
        directory = tempfile.TemporaryDirectory(prefix="inode-qa-unit-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def invoke(self, *args: str) -> int:
        """Run the tool without displaying its CLI output."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """No flags must never even check the remote guest."""
        with patch.object(QA, "load_guard", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_generic_confirmation_denied(self) -> None:
        """Only exact offline consent can start fault testing."""
        with patch.object(QA, "load_guard", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_guard_sibling_location(self) -> None:
        """The script can only import a sibling offline guard."""
        self.assertEqual(QA.GUARD_PATH.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD_PATH.is_file())

    def test_probe_is_independent_flock_process(self) -> None:
        """A separate real subprocess owns the EX lock probe."""
        target = self.root / "B"
        with patch.object(QA.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=0)
            self.assertTrue(QA.probe_exclusive(target))
        args = run.call_args.args[0]
        self.assertEqual(args[0], "/usr/bin/flock")
        self.assertIn("--exclusive", args)
        self.assertIn("--nonblock", args)
        self.assertIn(str(target), args)

    def test_exclusive_probe_false_on_denial(self) -> None:
        """An active reader cannot be silently classified as free."""
        with patch.object(QA.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=1)
            self.assertFalse(QA.probe_exclusive(self.root / "B"))

    def test_replacement_changes_inode_only_in_scratch(self) -> None:
        """Only our local temporary path can be replaced."""
        target = self.root / "B"
        target.touch(mode=0o600)
        old, new = QA.replace_inode(target)
        self.assertNotEqual(old, new)
        self.assertEqual(target.stat().st_ino, new)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertFalse(target.with_suffix(".replacement").exists())

    def test_old_held_shared_lock_denies_probe(self) -> None:
        """A stable inode behaves correctly while B SH is held."""
        target = self.root / "maintenance.lock"
        target.touch(mode=0o600)
        with target.open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_exclusive(target))

    def test_new_inode_bypasses_old_shared_lock(self) -> None:
        """Independent lock probe succeeds only after inode replacement."""
        target = self.root / "maintenance.lock"
        target.touch(mode=0o600)
        with target.open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_exclusive(target))
            QA.replace_inode(target)
            self.assertTrue(QA.probe_exclusive(target))

    def test_B_and_W_split_brain_case(self) -> None:
        """Real B/W old FDs remain locked after paths point to new inodes."""
        report = QA.check_case(self.root)
        self.assertEqual(
            report["classification"], "LOCK_INODE_REPLACEMENT_SPLIT_BRAIN"
        )
        self.assertEqual(report["new_B_EX_during_old_B_SH"], "ALLOWED_GAP")
        self.assertEqual(report["new_W_EX_during_old_W_EX"], "ALLOWED_GAP")

    def test_suite_has_no_docker_or_production_access(self) -> None:
        """Only ephemeral private directories appear in result."""
        report = QA.run_suite()
        self.assertFalse(report["docker_used"])
        self.assertFalse(report["production_modified"])

    def test_replacement_requires_existing_QA_inode(self) -> None:
        """Missing original lock cannot be accepted."""
        with self.assertRaises(FileNotFoundError):
            QA.replace_inode(self.root / "missing.lock")


if __name__ == "__main__":
    unittest.main()
