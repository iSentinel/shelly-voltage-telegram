"""Opt-in G admission before B/W writer lifetime locks."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "qa_writer_gate_G_module", ROOT / "writer_fence.py"
)
FENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FENCE
SPEC.loader.exec_module(FENCE)


def can_exclusive(path: Path) -> bool:
    """Attempt another lock description without retaining the result."""
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


class OptionalGAdmissionTests(unittest.TestCase):
    """Check only invented lock files, not Docker or production."""

    def setUp(self) -> None:
        """Create G/B/W as separate root-modeled private fixture inodes."""
        directory = tempfile.TemporaryDirectory(prefix="optin-g-unit-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.folder = self.root / "owner"
        self.folder.mkdir()
        os.chmod(self.folder, 488)
        for name in ("G", "B", "W"):
            path = self.folder / name
            path.touch(mode=416)
            os.chmod(path, 416)
        owner = patch.object(FENCE, "_TRUSTED_UID", os.geteuid())
        owner.start()
        self.addCleanup(owner.stop)

    def test_required_g_uses_G_before_B_and_W(self) -> None:
        """G SH is obtained before B SH and W EX."""
        source = (ROOT / "writer_fence.py").read_text()
        self.assertLess(
            source.index('_open_lock(folder, "G")'),
            source.index('_open_lock(folder, "B")'),
        )
        self.assertLess(
            source.index('_open_lock(folder, "B")'),
            source.index('_open_lock(folder, "W")'),
        )

    def test_G_exclusive_denies_writer_start(self) -> None:
        """Host G EX denies the writer before opening its B/W scope."""
        with (
            (self.folder / "G").open("rb") as maintenance,
            self.assertRaisesRegex(FENCE.WriterFenceError, "already_held"),
        ):
            fcntl.flock(maintenance, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with FENCE.writer_fence("required-g", self.folder):
                pass

    def test_G_releases_after_BW_admission(self) -> None:
        """Maintenance can take G EX while an incumbent still owns B/W."""
        with FENCE.writer_fence("required-g", self.folder) as lease:
            self.assertEqual(lease.mode, "required-g")
            self.assertIsNotNone(lease.g_fd)
            self.assertTrue(can_exclusive(self.folder / "G"))
            self.assertFalse(can_exclusive(self.folder / "B"))
            self.assertFalse(can_exclusive(self.folder / "W"))
        self.assertTrue(can_exclusive(self.folder / "B"))
        self.assertTrue(can_exclusive(self.folder / "W"))

    def test_missing_G_denied_only_in_new_mode(self) -> None:
        """Legacy required B/W must not suddenly require G."""
        (self.folder / "G").unlink()
        with FENCE.writer_fence("required", self.folder):
            pass
        with (
            self.assertRaises(OSError),
            FENCE.writer_fence("required-g", self.folder),
        ):
            pass

    def test_G_is_checked_before_each_sqlite_open(self) -> None:
        """A valid extended lease permits future database connections."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required-g"}),
            FENCE.writer_fence("required-g", self.folder),
        ):
            FENCE.assert_sqlite_writer_fenced()

    def test_G_is_required_before_sqlite_open(self) -> None:
        """The new mode cannot open SQLite without a real active owner."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required-g"}),
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "missing_at_database_open"
            ),
        ):
            FENCE.assert_sqlite_writer_fenced()

    def test_mode_cannot_downgrade_from_required_g(self) -> None:
        """Changing mode to legacy required invalidates the active lease."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required-g"}),
            FENCE.writer_fence("required-g", self.folder),
        ):
            os.environ["SHELLY_WRITER_FENCE"] = "required"
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "mode_changed_while_active"
            ):
                FENCE.assert_sqlite_writer_fenced()

    def test_G_inode_replacement_denied_before_sqlite(self) -> None:
        """A changed G pathname invalidates even the running B/W owner."""
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required-g"}),
            FENCE.writer_fence("required-g", self.folder),
        ):
            fresh = self.folder / "G.new"
            fresh.touch(mode=416)
            os.chmod(fresh, 416)
            os.replace(fresh, self.folder / "G")
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "inode_replaced"
            ):
                FENCE.assert_sqlite_writer_fenced()

    def test_G_group_writable_denied(self) -> None:
        """A group-writable admission inode is never trusted."""
        os.chmod(self.folder / "G", 432)
        with (
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "unsafe_permissions"
            ),
            FENCE.writer_fence("required-g", self.folder),
        ):
            pass

    def test_G_symlink_denied(self) -> None:
        """The writer refuses a symlink for the admission lock pathname."""
        (self.folder / "G").unlink()
        (self.folder / "G").symlink_to(self.folder / "B")
        with (
            self.assertRaises(OSError),
            FENCE.writer_fence("required-g", self.folder),
        ):
            pass

    def test_G_hardlink_denied(self) -> None:
        """Extra names for G must not bypass the root-managed inode policy."""
        os.link(self.folder / "G", self.folder / "G.extra")
        with (
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "link_count_not_one"
            ),
            FENCE.writer_fence("required-g", self.folder),
        ):
            pass

    def test_G_B_inode_alias_denied(self) -> None:
        """No two independent lock roles may refer to the same inode."""
        (self.folder / "G").unlink()
        os.link(self.folder / "B", self.folder / "G")
        with (
            self.assertRaises(FENCE.WriterFenceError),
            FENCE.writer_fence("required-g", self.folder),
        ):
            pass

    def test_G_SH_released_on_W_EX_conflict(self) -> None:
        """A rejected competing writer must not strand admission G SH."""
        with (self.folder / "W").open("rb") as incumbent:
            fcntl.flock(incumbent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                self.assertRaisesRegex(FENCE.WriterFenceError, "already_held"),
                FENCE.writer_fence("required-g", self.folder),
            ):
                pass
            self.assertTrue(can_exclusive(self.folder / "G"))
            self.assertTrue(can_exclusive(self.folder / "B"))

    def test_off_remains_inert(self) -> None:
        """No lock file is required without explicit fence activation."""
        with FENCE.writer_fence("off", self.folder / "missing") as lease:
            self.assertIsNone(lease)

    def test_G_mode_is_not_global_automatic_upgrade(self) -> None:
        """G admission must remain an explicit rollout flag."""
        self.assertIn("required", FENCE._VALID_MODES)
        self.assertIn("required-g", FENCE._VALID_MODES)
        self.assertEqual(FENCE._TRUSTED_UID, os.geteuid())
        self.assertEqual(stat.S_IMODE((self.folder / "G").stat().st_mode), 416)


if __name__ == "__main__":
    unittest.main()
