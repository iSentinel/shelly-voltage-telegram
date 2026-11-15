"""Default-inert unit contracts for stable B/W during release and rollback."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "disposable-owner-release-rollback-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_release_owner_inode", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class StableOwnerInodeQATests(unittest.TestCase):
    """Keep every filesystem experiment under an invented temporary root."""

    def setUp(self) -> None:
        """Patch only the test fixture to use the current unprivileged UID."""
        tmp = tempfile.TemporaryDirectory(prefix="owner-replay-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (
            ("ROOT_OWNER_UID", os.geteuid()),
            ("WRITER_GROUP_GID", os.getegid()),
        ):
            holder = patch.object(QA, name, value)
            holder.start()
            self.addCleanup(holder.stop)
        no_chown = patch.object(QA.os, "chown", return_value=None)
        no_chown.start()
        self.addCleanup(no_chown.stop)
        QA.prepare(self.root)
        self.baseline = QA.lock_identity(self.root)

    def invoke(self, *args: str) -> int:
        """Execute only the default-inert front end without any host QA."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "qa_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_cli_is_inert(self) -> None:
        """The tool must not contact Docker or create a release by default."""
        self.assertEqual(self.invoke(), 0)

    def test_generic_consent_is_denied(self) -> None:
        """A generic yes is never enough to start offline fault replay."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_fixed_qa_guard_is_adjacent(self) -> None:
        """The script uses only the exact sibling LXC114 safety module."""
        self.assertEqual(QA.GUARD.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD.is_file())

    def test_production_defaults_stay_root_and_group_10001(self) -> None:
        """Test UID patches must not change released source constants."""
        source = SCRIPT.read_text()
        self.assertIn("ROOT_OWNER_UID = 0", source)
        self.assertIn("WRITER_GROUP_GID = 10001", source)

    def test_lock_modes_and_distinct_inodes(self) -> None:
        """B and W must be separate regular mode0640 inodes."""
        for name in ("B", "W"):
            info = (self.root / "owner" / name).lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o640)
        self.assertNotEqual(self.baseline["B"][:2], self.baseline["W"][:2])

    def test_initial_source_is_v1(self) -> None:
        """The synthetic active release starts on v1 only."""
        self.assertEqual(QA.current(self.root), "v1")
        self.assertTrue((self.root / "current").is_symlink())

    def test_writer_B_SH_blocks_maintenance_EX(self) -> None:
        """The real kernel disallows maintenance EX while writer holds SH."""
        QA.verify_writer_blocks_maintenance(self.root)
        self.assertEqual(QA.lock_identity(self.root), self.baseline)

    def test_maintenance_excludes_writer(self) -> None:
        """Maintenance B EX blocks a new B SH admission."""
        with (self.root / "owner/B").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_lock(self.root / "owner/B", False))

    def test_source_switch_does_not_change_B_W_inode(self) -> None:
        """v1->v2 atomic activation preserves independently owned locks."""
        QA.change_under_maintenance(self.root, "v2", self.baseline)
        self.assertEqual(QA.current(self.root), "v2")
        self.assertEqual(QA.lock_identity(self.root), self.baseline)

    def test_rollback_retains_both_lock_inodes(self) -> None:
        """v1->v2->v1 rollback must never delete or replace B/W."""
        QA.change_under_maintenance(self.root, "v2", self.baseline)
        QA.change_under_maintenance(self.root, "v1", self.baseline)
        self.assertEqual(QA.current(self.root), "v1")
        self.assertEqual(QA.lock_identity(self.root), self.baseline)

    def test_update_during_running_writer_denied(self) -> None:
        """Do not change current source while the writer owns B SH."""
        with (self.root / "owner/B").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                QA.change_under_maintenance(self.root, "v2", self.baseline)
        self.assertEqual(QA.current(self.root), "v1")

    def test_unknown_release_denied(self) -> None:
        """Version targets are fixed rather than user-supplied paths."""
        with self.assertRaisesRegex(RuntimeError, "UNKNOWN_SYNTHETIC"):
            QA.swap(self.root, "../../etc")
        self.assertEqual(QA.current(self.root), "v1")

    def test_locked_inode_replacement_detected(self) -> None:
        """Swapping B pathname inode must invalidate the original baseline."""
        current = self.root / "owner/B"
        new = self.root / "owner/B.new"
        new.touch(mode=0o640)
        os.chmod(new, 0o640)
        with current.open("rb"):
            os.replace(new, current)
            self.assertNotEqual(QA.lock_identity(self.root), self.baseline)
            with self.assertRaisesRegex(RuntimeError, "IDENTITY_REPLACED"):
                QA.change_under_maintenance(self.root, "v2", self.baseline)

    def test_symlinked_owner_lock_denied(self) -> None:
        """A symlink must not be accepted as a B lock inode."""
        lock = self.root / "owner/B"
        lock.unlink()
        lock.symlink_to(self.root / "owner/W")
        with self.assertRaisesRegex(RuntimeError, "OWNER_OR_TYPE"):
            QA.lock_identity(self.root)

    def test_extra_hardlink_denied(self) -> None:
        """Additional inode names invalidate the stable lock contract."""
        os.link(self.root / "owner/W", self.root / "owner/W.hard")
        with self.assertRaisesRegex(RuntimeError, "HARDLINK_DETECTED"):
            QA.lock_identity(self.root)

    def test_wrong_permissions_denied(self) -> None:
        """Accidental group write on B cannot be accepted."""
        os.chmod(self.root / "owner/B", 0o660)
        with self.assertRaisesRegex(RuntimeError, "PERMISSION_CHANGED"):
            QA.lock_identity(self.root)

    def test_rollback_result_explicitly_excludes_production(self) -> None:
        """The positive proof may only mention synthetic source versions."""
        proof = QA.run_suite()
        self.assertEqual(proof["synthetic_releases"], ["v1", "v2", "v1"])
        self.assertTrue(proof["B_W_owner_inodes_stable"])
        self.assertFalse(proof["docker_used"])
        self.assertFalse(proof["production_changed"])


if __name__ == "__main__":
    unittest.main()
