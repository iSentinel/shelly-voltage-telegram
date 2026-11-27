"""Exercise read-only installed supervisor review and refusal paths."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import github_update

SPEC = importlib.util.spec_from_file_location(
    "installed_review",
    Path(__file__).resolve().parents[1]
    / "scripts/review-installed-supervisor.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"github_update": github_update}):
    SPEC.loader.exec_module(QA)


class InstalledReviewTests(unittest.TestCase):
    """Model the production observation without mutating live services."""

    def setUp(self):
        """Provide coherent snapshots and independent review functions."""
        self.paths = QA.CUT.Layout()
        self.pair = {"version": "0.5.27-hw"}
        self.identity = ["a", "b", "c"]
        self.addCleanup(patch.stopall)
        self.runtime = patch.object(
            QA.CUT,
            "installed_runtime",
            return_value=(self.pair, "no", True),
        ).start()
        self.ids = patch.object(
            QA, "runtime_identity", return_value=self.identity
        ).start()
        patch.object(QA.CUT, "state_of", return_value="inactive").start()
        patch.object(
            QA.CUT, "installed_transaction", return_value=Path("/qa")
        ).start()
        patch.object(QA, "source_inputs", return_value=34).start()
        patch.object(QA, "check_runtime").start()
        self.real_owner = QA.owner_held
        self.owner = patch.object(QA, "owner_held", return_value=True).start()
        self.run = patch.object(QA.CUT, "run", return_value="no").start()
        self.public = patch.object(QA.CUT, "public_receipt_preflight").start()

    def test_success_does_not_authorize(self):
        """An observation is never approval or signature reverification."""
        result = QA.review(self.paths)
        self.assertEqual(result["signed_inputs_match"], 34)
        for key in (
            "deployment_authorized",
            "startup_authorized",
            "signatures_reverified",
        ):
            self.assertIs(result[key], False)

    def test_public_receipt_blocker(self):
        """A separate public receipt blocker remains visible."""
        self.public.side_effect = ValueError("legacy alias")
        self.assertIs(QA.review(self.paths)["public_receipt_preflight"], False)

    def test_owner_not_held(self):
        """Do not certify a missing live owner."""
        self.owner.return_value = False
        with self.assertRaisesRegex(ValueError, "lease unconfirmed"):
            QA.review(self.paths)

    def test_runtime_changed(self):
        """A restart during the review invalidates the snapshot."""
        self.ids.side_effect = [self.identity, ["a", "b", "other"]]
        with self.assertRaisesRegex(ValueError, "changed during review"):
            QA.review(self.paths)

    def test_pending_daemon_reload(self):
        """On-disk unit bytes are insufficient with a pending reload."""
        self.run.return_value = "yes"
        with self.assertRaisesRegex(ValueError, "reload pending"):
            QA.review(self.paths)

    def test_missing_lock_does_not_create_file(self):
        """The audit cannot manufacture an owner inode."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner"
            with self.assertRaises(FileNotFoundError):
                self.real_owner(path)
            self.assertFalse(os.path.lexists(path))
