"""Review durable synthetic incident holds without releasing the fence."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "incident_hold",
    Path(__file__).resolve().parents[1] / "scripts/qa-incident-hold.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class IncidentHoldTests(unittest.TestCase):
    """Exercise actual marker and receipt files with modeled history."""

    def setUp(self):
        """Create protected fixture-shaped paths and fixed review evidence."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "fence").mkdir()
        self.marker = QA.TX.marker_path(self.root)
        self.token = "123456abcdef"
        self.anchor = "a" * 64
        self.evidence = {
            "schema": 1,
            "token": QA.token_value(self.root, self.token),
            "fault": "general-runtime",
            "scope_review_sha256": self.anchor,
            "fault_operator_supplied": True,
            "configuration_sha256": "b" * 64,
            "history": {"rows": 4, "pending": 1, "logical_sha256": "c" * 64},
            "release_authorized": False,
            "startup_authorized": False,
            "deployment_authorized": False,
        }
        self.addCleanup(patch.stopall)
        patch.object(QA.TX, "protected").start()
        patch.object(
            QA.OP.PLAN,
            "private_input",
            side_effect=lambda p, _: p.read_bytes(),
        ).start()
        self.snapshot = patch.object(
            QA, "snapshot", return_value=self.evidence
        ).start()

    def prepare(self):
        """Publish an incident hold with its immutable initial evidence."""
        return QA.prepare(
            self.root, self.token, "general-runtime", self.anchor
        )

    def inspect(self, anchor=None):
        """Review the protected receipt using its pinned hash."""
        raw = QA.hold_path(self.root).read_bytes()
        return QA.inspect(
            self.root,
            self.token,
            "general-runtime",
            self.anchor,
            QA.TX.sha(raw) if anchor is None else anchor,
        )

    def test_exact_replay(self):
        """Repeated inspection does not clear the marker or change bytes."""
        self.assertEqual(self.prepare(), self.evidence)
        raw = QA.hold_path(self.root).read_bytes()
        self.assertEqual(self.inspect(), self.evidence)
        self.assertEqual(QA.hold_path(self.root).read_bytes(), raw)
        self.assertTrue(self.marker.exists())

    def test_foreign_marker(self):
        """An existing foreign fence is not adopted."""
        self.marker.write_text("foreign\n")
        with self.assertRaisesRegex(ValueError, "foreign marker"):
            self.prepare()
        self.assertEqual(self.marker.read_text(), "foreign\n")

    def test_orphan_after_publication(self):
        """A failed journal write leaves the existing fence in place."""
        with (
            patch.object(QA.TX, "atomic_write", side_effect=OSError),
            self.assertRaises(OSError),
        ):
            self.prepare()
        self.assertTrue(self.marker.exists())
        self.assertFalse(QA.hold_path(self.root).exists())
        with self.assertRaisesRegex(ValueError, "existing incident"):
            self.prepare()

    def test_missing_marker(self):
        """Inspection never recreates a missing marker."""
        self.prepare()
        self.marker.unlink()
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.inspect()
        self.assertFalse(self.marker.exists())

    def test_history_drift(self):
        """Changed logical history cannot be silently accepted."""
        self.prepare()
        self.snapshot.return_value = dict(self.evidence, history={"rows": 5})
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.inspect()

    def test_wrong_receipt_anchor(self):
        """Refuse an unreviewed receipt identity."""
        self.prepare()
        with self.assertRaisesRegex(ValueError, "anchor mismatch"):
            self.inspect("0" * 64)

    def test_cannot_prepare_again(self):
        """The initial incident record cannot be replaced."""
        self.prepare()
        with self.assertRaisesRegex(ValueError, "existing incident"):
            self.prepare()
