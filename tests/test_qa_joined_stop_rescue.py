"""Verify chained QA observations and fail closed on identity drift."""

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import Journal, JournalBlocked, digest  # noqa: E402
from qa_joined_stop_rescue import record_rescue, record_stop  # noqa: E402

IDENTITY = {
    "version": "0.5.26-hw",
    "commit": "a" * 40,
    "source_sha256": "b" * 64,
    "oci_digest": "sha256:" + "c" * 64,
    "token": "qa-joined-stop",
}
STOP = {
    "stage": "stop_legacy_writer",
    "container_id": "d" * 64,
    "container_image": "sha256:" + "e" * 64,
    "selected_container_stopped": True,
}
INHIBIT = dict(
    STOP, stage="inhibit_legacy_restarts", selected_container_stopped=False
)
EDITOR = {"container_id": STOP["container_id"]}
RESCUE = {
    "container_id": STOP["container_id"],
    "container_image": STOP["container_image"],
    "token": IDENTITY["token"],
    "pending_token_verified": True,
    "logical_history_parity": True,
}


class JoinedTests(unittest.TestCase):
    """Only synthetic test reports are used by these unit tests."""

    def setUp(self):
        """Prepare a journal with a synthetic first-stage observation."""
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.journal = Journal(Path(root.name), IDENTITY)
        self.journal.initialize()
        self.journal.append("intent", "verify_signed_pair")
        self.journal.append("observe", "verify_signed_pair", "f" * 64)
        self.journal.append("intent", "inhibit_legacy_restarts")
        self.journal.append(
            "observe", "inhibit_legacy_restarts", digest(INHIBIT)
        )
        self.journal.append("intent", "stop_legacy_writer")
        self.args = argparse.Namespace(
            require_stopped=True,
            token=IDENTITY["token"],
            container="svt-qa-old",
        )
        self.editor = argparse.Namespace(container="svt-qa-old")

    def stopped(self):
        """Record a matching stop, then make the next intent explicit."""
        with patch("qa_joined_stop_rescue.observe_legacy", return_value=STOP):
            record_stop(self.journal, self.args)
        self.journal.append("intent", "capture_newest_rescue")

    def attempt(self, stop=STOP, editor=EDITOR, rescue=RESCUE):
        """Mock the three live boundaries, not the journal."""
        with (
            patch("qa_joined_stop_rescue.observe_legacy", return_value=stop),
            patch("qa_joined_stop_rescue.observe_editor", return_value=editor),
            patch("qa_joined_stop_rescue.observe_rescue", return_value=rescue),
        ):
            return record_rescue(self.journal, self.args, self.editor)

    def test_join(self):
        """Matching observations advance only to review of strict staging."""
        self.stopped()
        report = self.attempt()
        self.assertEqual(
            self.journal.inspect()["stage"], "stage_strict_supervisor"
        )
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["all_writers_verified"])

    def test_changed_stop(self):
        """A different stopped ID cannot follow the earlier inhibition."""
        with (
            patch(
                "qa_joined_stop_rescue.observe_legacy",
                return_value=dict(STOP, container_id="0" * 64),
            ),
            self.assertRaisesRegex(JournalBlocked, "LEGACY_IDENTITY_CHANGED"),
        ):
            record_stop(self.journal, self.args)
        self.assertEqual(self.journal.inspect()["records"], 5)

    def test_cross_container_and_token(self):
        """Keep rescue intent unresolved on any inconsistent component."""
        self.stopped()
        for editor, rescue in (
            (dict(EDITOR, container_id="0" * 64), RESCUE),
            (EDITOR, dict(RESCUE, container_image="sha256:" + "0" * 64)),
            (EDITOR, dict(RESCUE, token="qa-unrelated")),
        ):
            with (
                self.subTest(rescue=rescue),
                self.assertRaises(JournalBlocked),
            ):
                self.attempt(editor=editor, rescue=rescue)
            self.assertEqual(self.journal.inspect()["records"], 7)

    def test_changed_prior_stop(self):
        """A changed old container is rejected before rescue checks."""
        self.stopped()
        with self.assertRaisesRegex(
            JournalBlocked, "STOP_OBSERVATION_CHANGED"
        ):
            self.attempt(stop=dict(STOP, container_id="0" * 64))

    def test_stale_journal_head(self):
        """A stale observation cannot be appended against a new head."""
        state = self.journal.read()
        with patch("qa_joined_stop_rescue.observe_legacy", return_value=STOP):
            record_stop(self.journal, self.args)
        with self.assertRaisesRegex(JournalBlocked, "JOURNAL_HEAD_CHANGED"):
            self.journal.append(
                "intent",
                "capture_newest_rescue",
                expected_checksum=state["checksum"],
            )

    def test_editor_failure_keeps_intent(self):
        """No rescue stage completion while the editor is not retired."""
        self.stopped()
        with (
            patch("qa_joined_stop_rescue.observe_legacy", return_value=STOP),
            patch(
                "qa_joined_stop_rescue.observe_editor",
                side_effect=JournalBlocked("EDITOR_NOT_RETIRED"),
            ),
            self.assertRaises(JournalBlocked),
        ):
            record_rescue(self.journal, self.args, self.editor)
        self.assertEqual(
            self.journal.inspect()["decision"], "RECONCILE_REQUIRED"
        )


if __name__ == "__main__":
    unittest.main()
