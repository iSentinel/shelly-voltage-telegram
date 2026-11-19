"""Keep old/new QA cutover coherence read-only and non-authorizing."""

from __future__ import annotations

import argparse
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/qa-cutover-readiness-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("qa_cutover_policy", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
COMMIT = "a" * 40
OLD_DIGEST = "sha256:" + "1" * 64
NEW_DIGEST = "sha256:" + "2" * 64


class QaCutoverReadinessTests(unittest.TestCase):
    """Use synthetic identities and no network, Docker or production data."""

    def args(self):
        """Provide all explicit isolated paths used in the cutover checks."""
        return argparse.Namespace(
            qa_only=True,
            source_snapshot=Path("/var/lib/svt-qa-pair/upgrades"),
            signed_snapshot=Path("/var/lib/svt-qa-pair/old"),
            new_receipt=CHECK.SOURCE_STAGE / "deployed.json",
            new_version=Path("/var/lib/svt-qa-pair/RELEASE_VERSION"),
            source_archive_dir=CHECK.SOURCE_STAGE,
            target_commit=COMMIT,
            container="svt-qa-disposable",
            database=Path("/var/tmp/svt-qa-data/voltage.sqlite3"),
            fence_dir=Path("/var/lib/svt-qa-fence"),
            output=Path("/var/lib/svt-qa-rescue/newest.sqlite3"),
            token="qa-transaction-20261119",
        )

    def baseline(self):
        """Reflect old-versus-new pairing and newest WAL parity evidence."""
        return {
            "status": "PASS_QA_IDENTITY_AND_WAL_ONLY",
            "old_version": "0.5.25-hw",
            "new_version": "0.5.26-hw",
            "sqlite_wal_photo_parity": True,
            "release_authorized": False,
        }

    def receipt(self):
        """A staged receipt never authorizes installation by itself."""
        return {
            "transaction_token": self.args().token,
            "source_commit": COMMIT,
        }

    def test_combined_report_never_authorizes_release(self) -> None:
        """Matching pending and old/new pair is only QA coherence."""
        args = self.args()
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=self.baseline()),
            patch.object(CHECK, "check_target", return_value=self.receipt()),
        ):
            result = CHECK.evaluate(args)
        self.assertEqual(result["status"], "PASS_QA_CUTOVER_COHERENCE_ONLY")
        self.assertEqual(result["old_version"], "0.5.25-hw")
        self.assertEqual(result["new_version"], "0.5.26-hw")
        self.assertTrue(result["sqlite_wal_photo_parity"])
        self.assertFalse(result["source_signature_reverified"])
        self.assertFalse(result["image_actually_changed"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["rollback_authorized"])

    def test_wrong_host_refuses_source_receipt_evaluation(self) -> None:
        """Only an isolated QA guest may inspect a future cutover."""
        with (
            patch.object(CHECK.os, "geteuid", return_value=0),
            patch.object(CHECK.socket, "gethostname", return_value="proxmox"),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_DISPOSABLE_ONLY"
            ),
        ):
            CHECK.check_qa_boundary(self.args())

    def test_wrong_source_receipt_path_rejected(self) -> None:
        """Only the root-controlled private QA source slot is supported."""
        args = self.args()
        args.new_receipt = Path("/var/lib/svt-qa-other/deployed.json")
        with (
            patch.object(CHECK.os, "geteuid", return_value=0),
            patch.object(
                CHECK.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "SOURCE_RECEIPT_PATH_INVALID"
            ),
        ):
            CHECK.check_qa_boundary(args)

    def test_mutable_or_invalid_source_commit_rejected(self) -> None:
        """No image tag or shortened Git SHA can replace exact revision."""
        args = self.args()
        args.target_commit = "refs/heads/main"
        with (
            patch.object(CHECK.os, "geteuid", return_value=0),
            patch.object(
                CHECK.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "TARGET_COMMIT_INVALID"
            ),
        ):
            CHECK.check_qa_boundary(args)

    def test_root_receipt_must_match_current_pending_token(self) -> None:
        """A receipt for another update cannot borrow the newest WAL."""
        with (
            patch.object(
                CHECK.STAGE,
                "read_private_receipt",
                return_value={"transaction_token": "qa-other"},
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "TRANSACTION_TOKEN_MISMATCH"
            ),
        ):
            CHECK.check_target(self.args(), self.baseline())

    def test_same_old_new_oci_digest_is_not_image_change(self) -> None:
        """Do not claim a version change when the OCI bytes are identical."""
        args = self.args()
        with (
            patch.object(
                CHECK.STAGE,
                "read_private_receipt",
                return_value=self.receipt(),
            ),
            patch.object(
                CHECK.OWNER,
                "verify_receipt",
                return_value=CHECK.OWNER.IMAGE + OLD_DIGEST,
            ),
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "IMAGE_DIGEST_UNCHANGED"
            ),
        ):
            CHECK.check_target(args, self.baseline())

    def test_source_pair_receipt_receives_commit_binding(self) -> None:
        """Pass source tar and Git revision to the supervisor verifier."""
        args = self.args()
        with (
            patch.object(
                CHECK.STAGE,
                "read_private_receipt",
                return_value=self.receipt(),
            ),
            patch.object(
                CHECK.OWNER,
                "verify_receipt",
                return_value=CHECK.OWNER.IMAGE + NEW_DIGEST,
            ) as call,
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            patch.object(
                CHECK.OWNER, "source_version", return_value="0.5.26-hw"
            ),
        ):
            CHECK.check_target(args, self.baseline())
        self.assertEqual(call.call_args.args[2], True)
        self.assertEqual(call.call_args.args[3], COMMIT)
        self.assertEqual(call.call_args.args[4], CHECK.SOURCE_STAGE)

    def test_new_version_drift_refuses_transition(self) -> None:
        """Do not use one source version for two incompatible releases."""
        with (
            patch.object(
                CHECK.STAGE,
                "read_private_receipt",
                return_value=self.receipt(),
            ),
            patch.object(
                CHECK.OWNER,
                "verify_receipt",
                return_value=CHECK.OWNER.IMAGE + NEW_DIGEST,
            ),
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            patch.object(
                CHECK.OWNER, "source_version", return_value="0.5.27-hw"
            ),
            self.assertRaisesRegex(CHECK.CutoverBlocked, "NEW_VERSION_DRIFT"),
        ):
            CHECK.check_target(self.args(), self.baseline())

    def test_old_version_drift_refuses_transition(self) -> None:
        """A different prior signed image cannot use this rescue."""
        with (
            patch.object(
                CHECK.STAGE,
                "read_private_receipt",
                return_value=self.receipt(),
            ),
            patch.object(
                CHECK.OWNER,
                "verify_receipt",
                return_value=CHECK.OWNER.IMAGE + NEW_DIGEST,
            ),
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.24-hw", OLD_DIGEST),
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "OLD_VERSION_CHANGED"
            ),
        ):
            CHECK.check_target(self.args(), self.baseline())

    def test_incomplete_wal_rescue_blocks_before_receipt(self) -> None:
        """No approved-looking result when newest SQLite parity is absent."""
        broken = dict(self.baseline(), sqlite_wal_photo_parity=False)
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=broken),
            patch.object(CHECK, "check_target") as target,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "BASE_PROOF_INCOMPLETE"
            ),
        ):
            CHECK.evaluate(self.args())
        target.assert_not_called()


if __name__ == "__main__":
    unittest.main()
