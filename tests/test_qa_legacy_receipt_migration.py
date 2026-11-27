"""Verify isolated migration of a synthetic legacy systemd receipt alias."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "legacy_receipt",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-legacy-receipt-migration.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class MigrationTests(unittest.TestCase):
    """Use real disposable directory operations and modeled ownership."""

    def setUp(self):
        """Model fixture ownership on root and nonroot CI alike."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.target = self.root / QA.TARGET
        self.target.parent.mkdir(mode=0o700)
        self.target.mkdir(mode=0o750)
        self.receipt = self.target / "deployed.json"
        self.receipt.write_bytes(b'{"synthetic":true}\n')
        self.receipt.chmod(0o600)
        self.alias = self.root / QA.NAME
        self.alias.symlink_to(QA.TARGET)
        original = Path.lstat

        def modeled(path):
            """Preserve actual type/inode while modeling the two owners."""
            info = original(path)
            if path.is_relative_to(self.root):
                values = list(info)
                values[4] = 10001 if path.is_relative_to(self.target) else 0
                return os.stat_result(values)
            return info

        guard = patch.object(Path, "lstat", modeled)
        guard.start()
        self.addCleanup(guard.stop)

    def test_normal_and_repeated_recovery(self):
        """No legacy receipt is copied to the new public directory."""
        record = QA.prepare(self.root)
        self.assertTrue(self.alias.is_symlink())
        for _ in range(2):
            result = QA.recover(self.root)
            self.assertTrue(result["private_preserved"])
            self.assertFalse(result["deployment_authorized"])
            self.assertEqual(QA.identity(self.alias.lstat()), record["staged"])
            self.assertEqual(list(self.alias.iterdir()), [])

    def test_interrupted_unlink(self):
        """Recover only the staged inode after an interrupted unlink."""
        record = QA.prepare(self.root)
        self.alias.unlink()
        QA.recover(self.root)
        self.assertEqual(QA.identity(self.alias.lstat()), record["staged"])

    def test_unexpected_alias(self):
        """A foreign link cannot be removed."""
        self.alias.unlink()
        self.alias.symlink_to("elsewhere")
        with self.assertRaisesRegex(ValueError, "unexpected legacy alias"):
            QA.prepare(self.root)
        self.assertEqual(os.readlink(self.alias), "elsewhere")

    def test_private_drift(self):
        """The original private receipt must remain byte-identical."""
        QA.prepare(self.root)
        self.receipt.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "private receipt changed"):
            QA.recover(self.root)
        self.assertTrue(self.alias.is_symlink())

    def test_foreign_public_directory(self):
        """An unexpected public directory is not an owned replacement."""
        QA.prepare(self.root)
        self.alias.unlink()
        self.alias.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            QA.recover(self.root)
        self.assertTrue((self.root / QA.STAGED).is_dir())

    def test_orphan_stage(self):
        """An interrupted preparation needs explicit review, not adoption."""
        (self.root / QA.STAGED).mkdir()
        with self.assertRaisesRegex(ValueError, "existing migration state"):
            QA.prepare(self.root)
        self.assertTrue(self.alias.is_symlink())

    def test_published_receipt_not_overwritten(self):
        """After publication the rehearsal will not modify public state."""
        QA.prepare(self.root)
        QA.recover(self.root)
        public = self.alias / "deployed.json"
        public.write_bytes(b"new")
        with self.assertRaisesRegex(ValueError, "unexpected replacement"):
            QA.recover(self.root)
        self.assertEqual(public.read_bytes(), b"new")
