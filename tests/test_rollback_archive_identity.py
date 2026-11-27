"""Require unambiguous protected paths in rollback source archives."""

import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_signed_forward_cutover import CUTOVER


class RollbackArchiveTests(unittest.TestCase):
    """Exercise actual tar bytes, including ambiguous protected members."""

    def setUp(self):
        """Build only local synthetic source archives."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "source.tar.gz"
        self.version = "0.5.99-hw"
        self.prefix = f"shelly-voltage-telegram-{self.version}/"

    def archive(self, extra=(), missing=None):
        """Create exact fixture inputs and optional unusual entries."""
        names = CUTOVER.MONITOR_FILES | CUTOVER.ROLLBACK_SUPERVISOR_FILES
        with tarfile.open(self.path, "w:gz") as tar:
            for name in sorted(names - {missing}):
                self.member(tar, name, tarfile.REGTYPE)
            for name, kind in extra:
                self.member(tar, name, kind)
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def member(self, tar, name, kind):
        """Add a file or a deliberately nonregular member."""
        info = tarfile.TarInfo(self.prefix + name)
        info.type = kind
        if kind == tarfile.REGTYPE:
            data = name.encode()
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        else:
            info.linkname = self.prefix + "app.py"
            tar.addfile(info)

    def read(self, digest):
        """Invoke the unchanged exact rollback input boundary."""
        return CUTOVER.archived_monitor_inputs(self.path, self.version, digest)

    def test_expected_inputs(self):
        """Supervisor paths are required but runtime comparison is exact."""
        self.assertEqual(set(self.read(self.archive())), CUTOVER.MONITOR_FILES)

    def test_duplicate_monitor(self):
        """Repeated regular source members cannot be selected by order."""
        digest = self.archive([("app.py", tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, "ambiguous archived"):
            self.read(digest)

    def test_duplicate_supervisor(self):
        """Repeated supervisor members are also ambiguous."""
        name = "scripts/supervised-container.py"
        digest = self.archive([(name, tarfile.REGTYPE)])
        with self.assertRaisesRegex(ValueError, "ambiguous archived"):
            self.read(digest)

    def test_nonregular_protected_paths(self):
        """Reject protected links or directories even beside valid files."""
        for name in ("app.py", "scripts/supervised-container.py", "new.py"):
            for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE):
                with self.subTest(name=name, kind=kind):
                    digest = self.archive([(name, kind)])
                    with self.assertRaisesRegex(
                        ValueError, "ambiguous archived"
                    ):
                        self.read(digest)

    def test_missing_supervisor(self):
        """Required startup source cannot be omitted."""
        digest = self.archive(missing="scripts/supervised-container.py")
        with self.assertRaisesRegex(ValueError, "lacks supervisor"):
            self.read(digest)

    def test_additional_runtime_input(self):
        """A new top-level Python module changes the parity set."""
        before = self.read(self.archive())
        after = self.read(self.archive([("new.py", tarfile.REGTYPE)]))
        self.assertNotEqual(before, after)
        self.assertIn("new.py", after)
