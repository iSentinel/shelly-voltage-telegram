"""Exercise protected synthetic delivery evidence without external sends."""

import importlib.util
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
D = QA.DELIVERY


class DeliveryTests(unittest.TestCase):
    """Check exact synthetic markers, sequence and immutable receipts."""

    def setUp(self):
        """Use a disposable receipt directory and modeled history."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.token = "123456abcdef"
        self.meta = {D.DAILY: "2026-11-27", D.PENDING: "2026-W48:synthetic"}
        self.history = {
            "device": 1,
            "inode": 2,
            "last_id": 12,
            "rows": 12,
            "prefix_sha256": "a" * 64,
            "metadata_sha256": "b" * 64,
        }
        self.addCleanup(patch.stopall)
        patch.object(
            QA.START, "read_bytes", side_effect=Path.read_bytes
        ).start()

    def test_paused(self):
        """Ordinary alert only; pending marker remains."""
        D.verify_events("paused", b"ordinary-paused\n", self.meta)

    def test_resumed(self):
        """Exactly one new report and ordinary resumed notification."""
        meta = dict(self.meta, **{D.WEEKLY: "2026-W48"})
        D.verify_events(
            "enabled",
            b"ordinary-paused\nresumed-report\nordinary-resumed\n",
            meta,
        )

    def test_duplicate_refused(self):
        """Any additional synthetic event invalidates delivery evidence."""
        with self.assertRaisesRegex(ValueError, "sequence mismatch"):
            D.verify_events(
                "paused", b"ordinary-paused\nordinary-paused\n", self.meta
            )

    def test_missing_pending_refused(self):
        """An unresolved reservation must survive the transition."""
        del self.meta[D.PENDING]
        with self.assertRaisesRegex(ValueError, "marker mismatch"):
            D.verify_events("paused", b"ordinary-paused\n", self.meta)

    def test_immutable_receipt(self):
        """Replay exact bytes; refuse changed evidence."""
        record = {"mode": "paused", "deployment_authorized": False}
        first = D.persist(QA.START, self.root, self.token, record)
        self.assertEqual(
            D.persist(QA.START, self.root, self.token, record), first
        )
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            D.persist(
                QA.START, self.root, self.token, dict(record, mode="enabled")
            )

    def test_predecessor_binding(self):
        """Pin the earlier stopped history and exact pending token."""
        record = {
            "mode": "paused",
            "history": self.history,
            "pending_sha256": QA.TX.sha(self.meta[D.PENDING].encode()),
            "event_sha256": QA.TX.sha(b"ordinary-paused\n"),
            "deployment_authorized": False,
            "synthetic_api_only": True,
            "telegram_delivery_verified": False,
        }
        anchor = D.persist(QA.START, self.root, self.token, record)
        journal = {"baseline": self.history}
        self.assertEqual(
            D.verify_predecessor(
                QA.START, self.root, self.token, anchor, self.meta, journal
            ),
            record,
        )
        self.meta[D.PENDING] = "2026-W48:changed"
        with self.assertRaisesRegex(ValueError, "predecessor mismatch"):
            D.verify_predecessor(
                QA.START, self.root, self.token, anchor, self.meta, journal
            )

    def test_history_drift(self):
        """Stopped evidence must retain the accepted row prefix."""
        changed = dict(self.history, prefix_sha256="f" * 64)
        with (
            patch.object(QA.TX, "history_snapshot", return_value=changed),
            self.assertRaisesRegex(ValueError, "accepted history changed"),
        ):
            D.stopped_history(
                QA.START,
                self.root,
                {"history": self.history},
                {"baseline": self.history},
                "paused",
            )

    def test_paused_metadata_drift(self):
        """Do not certify a modified paused report state."""
        changed = dict(self.history, metadata_sha256="f" * 64)
        with (
            patch.object(QA.TX, "history_snapshot", return_value=changed),
            self.assertRaisesRegex(ValueError, "paused metadata changed"),
        ):
            D.stopped_history(
                QA.START,
                self.root,
                {"history": self.history},
                {"baseline": self.history},
                "paused",
            )
