"""Exercise exact signed-pair journal observations without a network."""

import argparse
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cutover_journal import Journal, JournalBlocked  # noqa: E402
from qa_observe_signed_pair import observe, record_observation  # noqa: E402

IDENTITY = {
    "version": "0.5.26-hw",
    "commit": "a" * 40,
    "source_sha256": "b" * 64,
    "oci_digest": "sha256:" + "c" * 64,
    "token": "qa-pair-observation",
}
REPORT = {
    "status": "PASS_QA_POSTSIGN_PUBLIC_PAIR_EVIDENCE_ONLY",
    "version": IDENTITY["version"],
    "source_commit": IDENTITY["commit"],
    "source_sha256": IDENTITY["source_sha256"],
    "oci_digest": IDENTITY["oci_digest"],
    "signed_source_verified": True,
    "signed_oci_verified": True,
    "oci_attestations_verified": True,
    "public_asset_inventory_matched": True,
    "owner_approval_verified": False,
    "sole_writer_cutover_verified": False,
    "release_authorized": False,
    "rollback_authorized": False,
    "production_modified": False,
}


class PairObservationTests(unittest.TestCase):
    """Mock the verifier boundary; never treat mock data as live proof."""

    def setUp(self):
        """Create a private synthetic journal and exact arguments."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.journal = Journal(Path(temp.name), IDENTITY)
        self.journal.initialize()
        self.args = argparse.Namespace(
            qa_only=True,
            version=IDENTITY["version"],
            commit=IDENTITY["commit"],
            digest=IDENTITY["oci_digest"],
        )
        self.module = Mock()
        self.module.evaluate.return_value = dict(REPORT)
        patcher = patch(
            "qa_observe_signed_pair.verifier", return_value=self.module
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_observe_requires_real_verifier_call(self):
        """Return a bound evidence digest, never release approval."""
        result = observe(self.args, IDENTITY)
        self.module.evaluate.assert_called_once_with(self.args)
        self.assertEqual(len(result["evidence_sha256"]), 64)
        self.assertFalse(result["release_authorized"])

    def test_record_requires_intent(self):
        """Do not implicitly initialize or advance a transaction."""
        with self.assertRaises(JournalBlocked):
            record_observation(self.journal, self.args)
        self.module.evaluate.assert_not_called()

    def test_matching_report_advances_one_stage(self):
        """Preserve journal ordering after the observation."""
        self.journal.append("intent", "verify_signed_pair")
        record_observation(self.journal, self.args)
        self.assertEqual(
            self.journal.inspect()["stage"], "inhibit_legacy_restarts"
        )
        self.assertEqual(self.journal.inspect()["records"], 2)

    def test_wrong_full_identity_refused(self):
        """A different digest with a similar suffix must not pass."""
        self.journal.append("intent", "verify_signed_pair")
        self.module.evaluate.return_value["oci_digest"] = (
            "sha256:" + "d" * 52 + "c" * 12
        )
        with self.assertRaises(JournalBlocked):
            record_observation(self.journal, self.args)
        self.assertEqual(
            self.journal.inspect()["decision"], "RECONCILE_REQUIRED"
        )

    def test_incomplete_or_authorizing_report_refused(self):
        """Fail closed on missing or contradictory proof fields."""
        self.journal.append("intent", "verify_signed_pair")
        for key, value in (
            ("signed_source_verified", False),
            ("oci_attestations_verified", None),
            ("source_sha256", "d" * 64),
            ("source_commit", "d" * 40),
            ("release_authorized", True),
            ("production_modified", True),
        ):
            with self.subTest(key=key):
                self.module.evaluate.return_value = dict(
                    REPORT, **{key: value}
                )
                with self.assertRaises(JournalBlocked):
                    record_observation(self.journal, self.args)
                self.assertEqual(self.journal.inspect()["records"], 1)

    def test_failed_verifier_leaves_intent(self):
        """A failure remains reconcilable, not a successful observation."""
        self.journal.append("intent", "verify_signed_pair")
        self.module.evaluate.side_effect = OSError("network unavailable")
        with self.assertRaises(OSError):
            record_observation(self.journal, self.args)
        self.assertEqual(self.journal.inspect()["records"], 1)

    def test_mismatching_arguments_refused_before_verifier(self):
        """The requested release must match the journal identity."""
        self.args.commit = "d" * 40
        with self.assertRaises(JournalBlocked):
            observe(self.args, IDENTITY)
        self.module.evaluate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
