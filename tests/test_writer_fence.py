"""Default-inert tests for optional in-container B SH / W EX fence."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "writer_fence.py"
SPEC = importlib.util.spec_from_file_location("candidate_writer_fence", SCRIPT)
FENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FENCE
SPEC.loader.exec_module(FENCE)


class WriterFenceCandidateTests(unittest.TestCase):
    """Exercise only private temporary lock files on isolated LXC114."""

    def setUp(self) -> None:
        """Create root-owned private lock files with explicit stable modes."""
        temp = tempfile.TemporaryDirectory(
            prefix="svt-fence-unit-", dir="/var/tmp"
        )
        self.addCleanup(temp.cleanup)
        owner_patch = patch.object(FENCE, "_TRUSTED_UID", os.geteuid())
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        self.root = Path(temp.name)
        self.folder = self.root / "owner"
        self.folder.mkdir(mode=488)
        os.chmod(self.folder, 488)
        for name in ("B", "W"):
            lock = self.folder / name
            lock.touch(mode=416)
            os.chmod(lock, 416)
        self.assertEqual(FENCE._ACTIVE, None)

    def test_empty_mode_is_inert(self) -> None:
        """Legacy default must not require or create any B/W files."""
        with FENCE.writer_fence("", self.root / "missing") as lease:
            self.assertIsNone(lease)

    def test_explicit_off_mode_is_inert(self) -> None:
        """Opt-out is a no-op only while the rollout flag is not required."""
        with FENCE.writer_fence("off", self.root / "missing") as lease:
            self.assertIsNone(lease)

    def test_unknown_mode_fails_closed(self) -> None:
        """Typos must never silently bypass a requested fence."""
        with (
            self.assertRaisesRegex(FENCE.WriterFenceError, "invalid_mode"),
            FENCE.writer_fence("yes", self.folder),
        ):
            pass

    def test_missing_lock_directory_denied(self) -> None:
        """Required mode never creates any lock files or paths."""
        with (
            self.assertRaises(OSError),
            FENCE.writer_fence("required", self.root / "absent"),
        ):
            pass
        self.assertFalse((self.root / "absent").exists())

    def test_missing_W_file_denied(self) -> None:
        """An incomplete B/W mount fails before app startup."""
        (self.folder / "W").unlink()
        with (
            self.assertRaises(OSError),
            FENCE.writer_fence("required", self.folder),
        ):
            pass
        self.assertFalse((self.folder / "W").exists())

    def test_required_lease_is_active(self) -> None:
        """The active lease covers both B SH and W EX until scope exit."""
        with FENCE.writer_fence("required", self.folder) as lease:
            self.assertIsNotNone(lease)
            self.assertIs(FENCE._ACTIVE, lease)
            lease.validate()
        self.assertIsNone(FENCE._ACTIVE)

    def test_sqlite_gate_requires_active_scope(self) -> None:
        """Required mode without startup lease blocks SQLite immediately."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "missing_at_database_open"
            ),
        ):
            FENCE.assert_sqlite_writer_fenced()

    def test_sqlite_gate_validates_active_scope(self) -> None:
        """A valid lease authorizes SQLite connection before each open."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            FENCE.assert_sqlite_writer_fenced()

    def test_sqlite_gate_default_legacy_inert(self) -> None:
        """Existing diagnostics are unchanged if no feature is enabled."""
        with patch.dict(os.environ, {"SHELLY_WRITER_FENCE": ""}):
            FENCE.assert_sqlite_writer_fenced()

    def test_sqlite_gate_bad_mode_denied(self) -> None:
        """Runtime mode tampering cannot bypass the required state."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "maybe"}),
            self.assertRaisesRegex(FENCE.WriterFenceError, "invalid_mode"),
        ):
            FENCE.assert_sqlite_writer_fenced()

    def test_B_exclusive_conflict_denied(self) -> None:
        """Maintenance EX lock prevents the writer's shared B lock."""
        with (self.folder / "B").open("rb") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.assertRaisesRegex(FENCE.WriterFenceError, "already_held"),
                FENCE.writer_fence("required", self.folder),
            ):
                pass

    def test_W_exclusive_conflict_denied(self) -> None:
        """A competing writer blocks the second W EX before SQLite."""
        with (self.folder / "W").open("rb") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.assertRaisesRegex(FENCE.WriterFenceError, "already_held"),
                FENCE.writer_fence("required", self.folder),
            ):
                pass

    def test_B_released_after_failed_W(self) -> None:
        """Failed W acquisition must not leave a stranded B reader."""
        with (self.folder / "W").open("rb") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.assertRaises(FENCE.WriterFenceError),
                FENCE.writer_fence("required", self.folder),
            ):
                pass
            result = subprocess.run(
                [
                    "/usr/bin/flock",
                    "-n",
                    "-x",
                    str(self.folder / "B"),
                    "/usr/bin/true",
                ],
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0)

    def test_second_independent_W_owner_denied(self) -> None:
        """The in-process lease excludes a separate process W EX."""
        with FENCE.writer_fence("required", self.folder):
            child = subprocess.run(
                [
                    "/usr/bin/flock",
                    "-n",
                    "-x",
                    str(self.folder / "W"),
                    "/usr/bin/true",
                ],
                check=False,
                timeout=5,
            )
            self.assertNotEqual(child.returncode, 0)

    def test_another_writer_scope_denied(self) -> None:
        """Two nested writer entrypoints cannot share one active state."""
        with (
            FENCE.writer_fence("required", self.folder),
            self.assertRaisesRegex(FENCE.WriterFenceError, "duplicate_scope"),
            FENCE.writer_fence("required", self.folder),
        ):
            pass

    def test_lock_fds_close_on_scope_exit(self) -> None:
        """No in-process writer lock survives normal application exit."""
        with FENCE.writer_fence("required", self.folder) as lease:
            bfd, wfd = (lease.b_fd, lease.w_fd)
        for fd in (bfd, wfd):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_reacquire_after_normal_exit(self) -> None:
        """A new owner may acquire B/W after prior writer exits."""
        with FENCE.writer_fence("required", self.folder):
            pass
        with FENCE.writer_fence("required", self.folder) as lease:
            lease.validate()

    def test_symlinked_lock_denied(self) -> None:
        """No-follow must reject a replacement lock pathname symlink."""
        path = self.folder / "B"
        path.unlink()
        path.symlink_to(self.root / "fake")
        with (
            self.assertRaises(OSError),
            FENCE.writer_fence("required", self.folder),
        ):
            pass

    def test_symlinked_directory_denied(self) -> None:
        """Never follow a symlink for the mount directory itself."""
        link = self.root / "linked-owner"
        link.symlink_to(self.folder, target_is_directory=True)
        with self.assertRaises(OSError), FENCE.writer_fence("required", link):
            pass

    def test_group_writable_lock_denied(self) -> None:
        """Any group write permission on a lock inode fails closed."""
        os.chmod(self.folder / "B", 432)
        with (
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "unsafe_permissions"
            ),
            FENCE.writer_fence("required", self.folder),
        ):
            pass

    def test_group_writable_directory_denied(self) -> None:
        """Any group-write permission on the lock directory is unsafe."""
        os.chmod(self.folder, 504)
        with (
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "unsafe_permissions"
            ),
            FENCE.writer_fence("required", self.folder),
        ):
            pass

    def test_foreign_owner_denied(self) -> None:
        """Require the configured trusted UID on all lock inodes."""
        with (
            patch.object(FENCE, "_TRUSTED_UID", os.geteuid() + 1),
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "wrong_owner_or_group"
            ),
            FENCE.writer_fence("required", self.folder),
        ):
            pass

    def test_hardlinked_lock_denied(self) -> None:
        """A lock inode with extra hardlinks is never trusted."""
        (self.folder / "W").unlink()
        os.link(self.folder / "B", self.folder / "W")
        with (
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "link_count_not_one"
            ),
            FENCE.writer_fence("required", self.folder),
        ):
            pass

    def test_directory_inode_replacement_detected(self) -> None:
        """Rename-and-recreate of mount path invalidates active lease."""
        with FENCE.writer_fence("required", self.folder) as lease:
            saved = self.root / "owner-old"
            self.folder.rename(saved)
            self.folder.mkdir(mode=488)
            os.chmod(self.folder, 488)
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "inode_replaced"
            ):
                lease.validate()

    def test_lock_inode_replacement_detected(self) -> None:
        """A new B inode invalidates SQLite gate despite old held fd."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            next_lock = self.folder / "B.new"
            next_lock.touch(mode=416)
            os.chmod(next_lock, 416)
            os.replace(next_lock, self.folder / "B")
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "inode_replaced"
            ):
                FENCE.assert_sqlite_writer_fenced()

    def test_mode_downgrade_during_active_lease_denied(self) -> None:
        """Changing required to off cannot bypass an already active lease."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            os.environ["SHELLY_WRITER_FENCE"] = "off"
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "mode_changed_while_active"
            ):
                FENCE.assert_sqlite_writer_fenced()

    def test_held_lock_chmod_must_fail_before_sqlite(self) -> None:
        """Writable B permissions are rechecked on every SQLite open."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            os.chmod(self.folder / "B", 432)
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "unsafe_permissions"
            ):
                FENCE.assert_sqlite_writer_fenced()

    def test_held_lock_owner_change_blocks_sqlite(self) -> None:
        """A trusted owner mismatch must fail at the next SQLite gate."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
            patch.object(FENCE, "_TRUSTED_UID", os.geteuid() + 1),
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "wrong_owner_or_group"
            ),
        ):
            FENCE.assert_sqlite_writer_fenced()

    def test_held_directory_permission_change_denied(self) -> None:
        """Parent mode changes are revalidated before writer opens SQLite."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            os.chmod(self.folder, 504)
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "unsafe_permissions"
            ):
                FENCE.assert_sqlite_writer_fenced()

    def test_held_lock_hardlink_change_denied(self) -> None:
        """Adding an alias to a live B inode triggers validation failure."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            os.link(self.folder / "B", self.folder / "B.link")
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "link_count_not_one"
            ):
                FENCE.assert_sqlite_writer_fenced()


if __name__ == "__main__":
    unittest.main()
