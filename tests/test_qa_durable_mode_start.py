"""Test immutable QA START claim and refusal before fence release."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "mode_start",
    Path(__file__).resolve().parents[1] / "scripts/qa-durable-mode-start.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class StartBindingTests(unittest.TestCase):
    """Use real durable records with synthetic inspected identities."""

    def setUp(self):
        """Prepare a pinned arm and mock the reinspection boundary."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.token = "123456abcdef"
        self.record = {
            "schema": 1,
            "journal_sha256": "a" * 64,
            "container_id": "b" * 64,
            "image_id": QA.IMAGE,
            "config_sha256": "c" * 64,
            "host_config_sha256": "d" * 64,
            "mounts_sha256": "e" * 64,
            "deployment_authorized": False,
            "startup_authorized": False,
        }
        self.addCleanup(patch.stopall)
        patch.object(QA, "read_bytes", side_effect=Path.read_bytes).start()
        self.binding = patch.object(
            QA, "binding", return_value=self.record
        ).start()
        self.release = patch.object(QA.OP, "release").start()
        self.anchor = QA.arm(self.root, self.token, "a" * 64)["arm_sha256"]

    def test_consumed_before_release(self):
        """Persist the claim before clearing the writer fence."""
        path = QA.path_for(self.root, self.token, "claim")

        def check(*_args, **kwargs):
            """Require the durable record during release."""
            self.assertEqual(
                json.loads(path.read_bytes())["container_id"], "b" * 64
            )
            self.assertEqual(kwargs["claim_sha"], QA.TX.sha(path.read_bytes()))

        self.release.side_effect = check
        self.assertEqual(
            QA.claim(self.root, self.token, self.anchor), "b" * 64
        )
        with self.assertRaisesRegex(ValueError, "already claimed"):
            QA.claim(self.root, self.token, self.anchor)

    def test_replaced_container(self):
        """Different inspected immutable identity cannot consume the arm."""
        self.binding.return_value = dict(self.record, container_id="f" * 64)
        with self.assertRaisesRegex(ValueError, "witness changed"):
            QA.claim(self.root, self.token, self.anchor)
        self.release.assert_not_called()

    def test_changed_host_config(self):
        """Configuration changes must invalidate the pinned arm."""
        self.binding.return_value = dict(
            self.record, host_config_sha256="f" * 64
        )
        with self.assertRaisesRegex(ValueError, "witness changed"):
            QA.claim(self.root, self.token, self.anchor)
        self.release.assert_not_called()

    def test_wrong_anchor(self):
        """Refuse an unreviewed arm fingerprint."""
        with self.assertRaisesRegex(ValueError, "anchor mismatch"):
            QA.claim(self.root, self.token, "0" * 64)
        self.release.assert_not_called()

    def test_release_interrupted(self):
        """A failed release cannot permit a repeated START claim."""
        self.release.side_effect = OSError("injected")
        with self.assertRaises(OSError):
            QA.claim(self.root, self.token, self.anchor)
        with self.assertRaisesRegex(ValueError, "already claimed"):
            QA.claim(self.root, self.token, self.anchor)

    def test_arm_replacement_refused(self):
        """Never overwrite original arm evidence."""
        with self.assertRaisesRegex(ValueError, "already exists"):
            QA.arm(self.root, self.token, "a" * 64)
