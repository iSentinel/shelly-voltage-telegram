"""Exercise durable QA launch and acceptance evidence without Docker."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "report_start",
    Path(__file__).resolve().parents[1] / "scripts/qa-durable-report-start.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class LaunchReceiptTests(unittest.TestCase):
    """Exercise actual receipt files with synthetic launch observations."""

    def setUp(self):
        """Prepare one deterministic simulated launch and history."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.token = "123456abcdef"
        self.anchor = "a" * 64
        self.identity = "b" * 64
        self.info = {
            "Id": self.identity,
            "RestartCount": 0,
            "State": {"Running": True, "StartedAt": "2026-11-27T12:00:00Z"},
        }
        self.history = {
            "device": 1,
            "inode": 2,
            "rows": 12,
            "last_id": 12,
            "prefix_sha256": "c" * 64,
            "metadata_sha256": "d" * 64,
        }
        self.result = {
            "container_id": self.identity,
            "started_at": self.info["State"]["StartedAt"],
            "phases": {
                p: {"samples": 3, "latest_age": 0.5}
                for p in ("L1", "L2", "L3")
            },
        }
        self.addCleanup(patch.stopall)
        patch.object(
            QA.START, "read_bytes", side_effect=Path.read_bytes
        ).start()
        patch.object(
            QA.TX, "inspect_qa_container", return_value=self.info
        ).start()
        self.snapshot = patch.object(
            QA.TX, "history_snapshot", return_value=self.history
        ).start()

    def launch(self):
        """Write one immutable launch receipt."""
        return QA.record_launch(
            self.root, self.token, self.anchor, self.identity
        )

    def accept(self, history=None):
        """Write or review the first runtime acceptance."""
        return QA.RECEIPT.accept(
            QA.START,
            self.root,
            self.token,
            self.anchor,
            self.result,
            self.history if history is None else history,
        )

    def check(self):
        """Revalidate the observed launch identity."""
        return QA.RECEIPT.check_launch(
            QA.START, self.root, self.token, self.anchor, self.info
        )

    def test_launch_once(self):
        """The first StartedAt record cannot be overwritten."""
        self.assertEqual(self.launch(), self.check())
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.launch()

    def test_missing_launch_refused(self):
        """A claim alone is not evidence that Docker started."""
        with self.assertRaises(FileNotFoundError):
            self.check()

    def test_restart_refused(self):
        """A restart after the recorded START cannot be accepted."""
        self.launch()
        self.info["State"]["StartedAt"] = "2026-11-27T12:10:00Z"
        with self.assertRaisesRegex(ValueError, "launch receipt mismatch"):
            self.check()

    def test_restart_counter_refused(self):
        """Unexpected automatic restart is refused."""
        self.launch()
        self.info["RestartCount"] = 1
        with self.assertRaisesRegex(ValueError, "launch receipt mismatch"):
            self.check()

    def test_acceptance_replay_preserves_bytes(self):
        """Growth does not replace the first accepted receipt."""
        self.launch()
        first = self.accept()
        path = QA.START.path_for(self.root, self.token, "acceptance")
        before = path.read_bytes()
        current = dict(self.history, rows=20, last_id=20)
        self.snapshot.return_value = current
        self.assertEqual(self.accept(current), first)
        self.assertEqual(path.read_bytes(), before)
        self.snapshot.assert_called_with(self.root, 12)
        self.assertFalse(first["deployment_authorized"])

    def test_accepted_prefix_drift(self):
        """Changed already accepted rows block a later observation."""
        self.launch()
        self.accept()
        self.snapshot.return_value = dict(self.history, prefix_sha256="f" * 64)
        with self.assertRaisesRegex(ValueError, "accepted history changed"):
            self.accept()

    def test_acceptance_tamper(self):
        """Changed launch binding cannot be silently adopted."""
        self.launch()
        self.accept()
        path = QA.START.path_for(self.root, self.token, "acceptance")
        prior = json.loads(path.read_bytes())
        prior["container_id"] = "f" * 64
        path.write_text(json.dumps(prior))
        with self.assertRaisesRegex(
            ValueError, "acceptance identity mismatch"
        ):
            self.accept()
