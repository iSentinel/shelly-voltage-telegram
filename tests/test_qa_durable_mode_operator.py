"""Test durable mode configuration changes and fail-closed recovery."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "durable_mode",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-durable-mode-operator.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class DurableModeTests(unittest.TestCase):
    """Exercise actual atomic files with modeled protected QA ownership."""

    def setUp(self):
        """Create an isolated fixture and immutable history snapshot."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "fence").mkdir()
        self.raw = b"LANGUAGE=uk\nSCHEDULED_REPORTS_MODE=enabled\n"
        self.env = self.root / ".env"
        self.env.write_bytes(self.raw)
        self.env.chmod(0o600)
        self.token = "123456abcdef"
        self.marker = QA.TX.marker_path(self.root)
        self.addCleanup(patch.stopall)
        patch.object(QA.TX, "protected").start()
        patch.object(
            QA.PLAN, "private_input", side_effect=lambda p, _: p.read_bytes()
        ).start()
        self.history = patch.object(
            QA.TX, "history_snapshot", return_value={"rows": 12, "inode": 4}
        ).start()

    def prepare(self):
        """Prepare a canonical enabled to paused transition."""
        return QA.prepare(self.root, self.token, "enabled", "paused")

    def test_bidirectional(self):
        """Preserve unrelated config bytes and release only explicitly."""
        self.prepare()
        self.assertEqual(self.env.read_bytes(), self.raw)
        QA.recover(self.root, self.token)
        self.assertTrue(self.marker.exists())
        QA.release(self.root, self.token)
        self.assertFalse(self.marker.exists())
        QA.release(self.root, self.token)
        next_token = "abcdef123456"
        QA.prepare(self.root, next_token, "paused", "enabled")
        QA.recover(self.root, next_token)
        QA.release(self.root, next_token)
        self.assertEqual(self.env.read_bytes(), self.raw)

    def test_crash_before_marker(self):
        """Recover intent only if the unchanged stopped baseline matches."""
        with (
            patch.object(QA.MARKER, "publish", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            self.prepare()
        self.assertFalse(self.marker.exists())
        QA.recover(self.root, self.token)
        self.assertTrue(self.marker.exists())

    def test_crash_after_config(self):
        """Resume an exact target configuration with the marker retained."""
        self.prepare()
        self.env.write_bytes(QA.PLAN.transition(self.raw, "enabled", "paused"))
        QA.recover(self.root, self.token)
        self.assertEqual(QA.read(self.root, self.token)["phase"], "configured")

    def test_crash_after_release_record(self):
        """Finish removing the marker only after the release checkpoint."""
        self.prepare()
        QA.recover(self.root, self.token)
        with (
            patch.object(QA.MARKER, "clear", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            QA.release(self.root, self.token)
        self.assertTrue(self.marker.exists())
        self.assertEqual(QA.read(self.root, self.token)["phase"], "released")
        QA.release(self.root, self.token)
        self.assertFalse(self.marker.exists())

    def test_foreign_marker(self):
        """Never overwrite an unrelated marker."""
        self.prepare()
        self.marker.write_text("foreign\n")
        with self.assertRaisesRegex(ValueError, "foreign"):
            QA.recover(self.root, self.token)
        self.assertEqual(self.marker.read_text(), "foreign\n")

    def test_history_drift(self):
        """Changes to a stopped baseline block recovery."""
        self.prepare()
        self.history.return_value = {"rows": 13, "inode": 4}
        with self.assertRaisesRegex(ValueError, "history changed"):
            QA.recover(self.root, self.token)

    def test_config_drift(self):
        """Do not adopt an unreviewed configuration."""
        self.prepare()
        self.env.write_bytes(self.raw + b"OTHER=yes\n")
        with self.assertRaisesRegex(ValueError, "config drift"):
            QA.recover(self.root, self.token)

    def test_missing_marker_after_config(self):
        """Do not recreate a lost fence after target publication."""
        self.prepare()
        QA.recover(self.root, self.token)
        self.marker.unlink()
        with self.assertRaisesRegex(ValueError, "missing mode marker"):
            QA.release(self.root, self.token)

    def test_unfinished_and_released(self):
        """No new intent before completion or reuse of a released token."""
        self.prepare()
        with self.assertRaises(ValueError):
            QA.prepare(self.root, "abcdef123456", "enabled", "paused")
        QA.recover(self.root, self.token)
        QA.release(self.root, self.token)
        with self.assertRaises(ValueError):
            QA.recover(self.root, self.token)
