"""Test refusal and stable backup observation without real runtime actions."""

import argparse
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import JournalBlocked  # noqa: E402
from qa_observe_rescue import observe, record_observation  # noqa: E402

OWNER = {"container_id": "a" * 64, "container_image": "sha256:" + "b" * 64}


class RescueObservationTests(unittest.TestCase):
    """Mock verifier boundaries, not a production backup."""

    def setUp(self):
        """Use synthetic evidence and isolated path arguments."""
        self.args = argparse.Namespace(
            qa_only=True,
            require_stopped=True,
            output=Path("/qa/rescue"),
            token="qa-rescue-1",
        )

    def test_stable_observation(self):
        """Both verifier passes and stable identity are mandatory."""
        with (
            patch("qa_observe_rescue.observe_legacy", return_value=OWNER),
            patch("qa_observe_rescue.inspect_rescue") as inspect,
            patch("qa_observe_rescue.hash_rescue", return_value="c" * 64),
        ):
            result = observe(self.args)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual(result["rescue_sha256"], "c" * 64)
        self.assertFalse(result["all_writers_verified"])
        self.assertFalse(result["rollback_authorized"])

    def test_reject_changed_file(self):
        """Reject a changing backup or replacement container."""
        with (
            patch("qa_observe_rescue.observe_legacy", return_value=OWNER),
            patch("qa_observe_rescue.inspect_rescue"),
            patch(
                "qa_observe_rescue.hash_rescue",
                side_effect=["c" * 64, "d" * 64],
            ),
            self.assertRaises(JournalBlocked),
        ):
            observe(self.args)

    def test_failed_inspect(self):
        """Do not accept a failed actual verification."""
        with (
            patch("qa_observe_rescue.observe_legacy", return_value=OWNER),
            patch(
                "qa_observe_rescue.inspect_rescue",
                side_effect=JournalBlocked("failure"),
            ),
            self.assertRaises(JournalBlocked),
        ):
            observe(self.args)

    def test_token_mismatch(self):
        """Do not record a backup belonging to another transaction."""
        journal = Mock()
        journal.identity = {"token": "qa-other"}
        journal.inspect.return_value = {
            "decision": "RECONCILE_REQUIRED",
            "stage": "capture_newest_rescue",
        }
        with self.assertRaises(JournalBlocked):
            record_observation(journal, self.args)
        journal.append.assert_not_called()

    def test_no_stopped_owner(self):
        """Cannot observe a rescue while allowing a running owner."""
        self.args.require_stopped = False
        with self.assertRaises(JournalBlocked):
            observe(self.args)


if __name__ == "__main__":
    unittest.main()
