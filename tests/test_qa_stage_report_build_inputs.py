"""Test fail-closed staging of reviewed build input bytes."""

import hashlib
import runpy
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = runpy.run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "scripts/qa-stage-report-build-inputs.py"
    )
)
CHECK = SCRIPT["checked_bytes"]
WRITE = SCRIPT["write_archive"]
SAFE = SCRIPT["safe_name"]


class StageInputsTests(unittest.TestCase):
    """Exercise input integrity and deterministic archive publication."""

    def setUp(self):
        """Create private test paths."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "input.deb"
        self.source.write_bytes(b"verified")
        self.digest = hashlib.sha256(b"verified").hexdigest()

    def test_exact_bytes(self):
        """Return only content matching size and hash."""
        self.assertEqual(CHECK(self.source, self.digest, 8), b"verified")

    def test_changed_content(self):
        """Refuse a same-length changed artifact."""
        self.source.write_bytes(b"corrupt!")
        with self.assertRaisesRegex(ValueError, "SHA256"):
            CHECK(self.source, self.digest, 8)

    def test_wrong_size(self):
        """Refuse a wrong declared size."""
        with self.assertRaisesRegex(ValueError, "size"):
            CHECK(self.source, self.digest, 9)

    def test_missing_or_symlink(self):
        """Missing files and symbolic links cannot be staged."""
        with self.assertRaises(OSError):
            CHECK(self.root / "missing.deb", self.digest)
        alias = self.root / "alias.deb"
        alias.symlink_to(self.source)
        with self.assertRaises(OSError):
            CHECK(alias, self.digest)

    def test_unsafe_name(self):
        """Reject traversal and non-basename inputs."""
        for name in ("../x", "/x", ".", "..", "a/b", "a b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                SAFE(name)

    def test_reproducible_archive(self):
        """Archive order and metadata are independent of mapping order."""
        a, b = self.root / "a.tar", self.root / "b.tar"
        WRITE({"wheels/b": b"B", "debs/a": b"A"}, a)
        WRITE({"debs/a": b"A", "wheels/b": b"B"}, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        with tarfile.open(a) as tar:
            self.assertEqual(tar.getnames(), ["debs/a", "wheels/b"])
            for item in tar:
                self.assertTrue(item.isfile())
                self.assertEqual((item.uid, item.gid, item.mtime), (0, 0, 0))

    def test_long_wheel_name(self):
        """Long platform tags survive deterministic PAX encoding."""
        name = "wheels/" + "a" * 120 + ".whl"
        a, b = self.root / "long-a.tar", self.root / "long-b.tar"
        WRITE({name: b"wheel"}, a)
        WRITE({name: b"wheel"}, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        with tarfile.open(a) as tar:
            self.assertEqual(tar.getnames(), [name])
            self.assertEqual(tar.extractfile(name).read(), b"wheel")

    def test_existing_output_unchanged(self):
        """Never overwrite a previously published archive."""
        output = self.root / "existing.tar"
        output.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            WRITE({"x": b"new"}, output)
        self.assertEqual(output.read_bytes(), b"original")
