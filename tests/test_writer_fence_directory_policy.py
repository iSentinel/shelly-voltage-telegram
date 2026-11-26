"""Validate root-protected production fence selection and explicit setup."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from writer_fence import WriterFenceError, fence_directory


class WriterFenceDirectoryTests(unittest.TestCase):
    """A pending marker must never live in a writable monitor directory."""

    def setUp(self) -> None:
        """Create a private non-production directory."""
        tmp = tempfile.TemporaryDirectory(prefix="svt-fence-dir-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "history.sqlite3"

    def test_fixture_default_remains_isolated(self) -> None:
        """Legacy unit fixtures use their own private test directory."""
        with patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": ""}):
            self.assertEqual(fence_directory(self.path), self.path.parent)

    def test_reject_relative_shared_directory(self) -> None:
        """Production root fence mount must be an absolute path."""
        with (
            patch.dict(os.environ, {"SVT_WRITER_FENCE_DIR": "./run"}),
            self.assertRaisesRegex(WriterFenceError, "DIR_INVALID"),
        ):
            fence_directory(self.path)

    def test_reject_own_database_directory(self) -> None:
        """The app-writable database directory cannot own pending updates."""
        with (
            patch.dict(
                os.environ,
                {"SVT_WRITER_FENCE_DIR": str(self.path.parent)},
            ),
            self.assertRaisesRegex(WriterFenceError, "DIR_INVALID"),
        ):
            fence_directory(self.path)

    def test_reject_writable_parent(self) -> None:
        """Even a root-owned child beneath /tmp is untrusted."""
        with (
            patch.dict(
                os.environ,
                {"SVT_WRITER_FENCE_DIR": "/tmp/svt-root-fence-qa"},
            ),
            self.assertRaisesRegex(WriterFenceError, "DIR_UNSAFE"),
        ):
            fence_directory(self.path)


if __name__ == "__main__":
    unittest.main()
