"""Validate the receipt identity contract without manufacturing proof."""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import JournalBlocked  # noqa: E402
from qa_observe_strict_receipt import check_identity, observe  # noqa: E402

IDENTITY = {
    "version": "0.5.26-hw",
    "commit": "a" * 40,
    "source_sha256": "b" * 64,
    "oci_digest": "sha256:" + "c" * 64,
    "token": "qa-strict-receipt",
}
RECORD = {
    "version": IDENTITY["version"],
    "digest": IDENTITY["oci_digest"],
    "source_commit": IDENTITY["commit"],
    "source_sha256": IDENTITY["source_sha256"],
    "transaction_token": IDENTITY["token"],
}


class ReceiptIdentityTests(unittest.TestCase):
    """Synthetic policy checks do not certify a live receipt."""

    def setUp(self):
        """Prepare a synthetic pending journal and checker responses."""
        self.journal = Mock(identity=IDENTITY)
        self.journal.inspect.return_value = {
            "decision": "RECONCILE_REQUIRED",
            "stage": "stage_strict_supervisor",
        }
        self.supervisor = Mock(IMAGE="registry/image@")
        self.supervisor.verify_receipt.return_value = (
            self.supervisor.IMAGE + IDENTITY["oci_digest"]
        )
        self.rescue = Mock()
        self.rescue.read_private_receipt.return_value = dict(RECORD)

    def run_observe(self):
        """Mock the QA boundary and delegate identity validation."""
        with (
            patch("qa_observe_strict_receipt.os.geteuid", return_value=0),
            patch(
                "qa_observe_strict_receipt.socket.gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch(
                "qa_observe_strict_receipt.helper",
                side_effect=[self.supervisor, self.rescue],
            ),
        ):
            return observe(self.journal, Path("/qa/VERSION"))

    def test_matching_identity(self):
        """An exact receipt is not proof of installed supervisor."""
        check_identity(RECORD, IDENTITY)
        result = self.run_observe()
        self.assertFalse(result["journal_advanced"])
        self.assertFalse(result["supervisor_installed"])
        self.journal.append.assert_not_called()

    def test_mismatched_fields(self):
        """Reject any drift, including an otherwise matching token."""
        for key in RECORD:
            with self.subTest(key=key), self.assertRaises(JournalBlocked):
                check_identity(dict(RECORD, **{key: "other"}), IDENTITY)

    def test_missing_receipt(self):
        """Absent evidence never completes the journal stage."""
        self.rescue.read_private_receipt.side_effect = FileNotFoundError
        with self.assertRaises(FileNotFoundError):
            self.run_observe()
        self.journal.append.assert_not_called()

    def test_changed_receipt(self):
        """Recheck the same receipt after the supervisor policy check."""
        self.rescue.read_private_receipt.side_effect = [
            RECORD,
            dict(RECORD, transaction_token="qa-different"),
        ]
        with self.assertRaisesRegex(JournalBlocked, "RECEIPT_CHANGED"):
            self.run_observe()

    def test_wrong_stage(self):
        """Do not permit observation for a different journal stage."""
        self.journal.inspect.return_value["stage"] = "activate_new_writer"
        with self.assertRaisesRegex(JournalBlocked, "INTENT_REQUIRED"):
            self.run_observe()

    def test_nonqa(self):
        """Never inspect a production receipt."""
        with (
            patch(
                "qa_observe_strict_receipt.socket.gethostname",
                return_value="production",
            ),
            self.assertRaises(JournalBlocked),
        ):
            observe(self.journal, Path("/qa/VERSION"))


if __name__ == "__main__":
    unittest.main()
