"""Keep old/new QA cutover coherence read-only and non-authorizing."""

from __future__ import annotations

import argparse
import importlib.util
import unittest
from contextlib import nullcontext
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
OLD_COMMIT = "b" * 40
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
            old_source_archive_dir=None,
            target_commit=COMMIT,
            old_commit="",
            verify_signatures=False,
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
            "old_digest_suffix": OLD_DIGEST[-12:],
            "sqlite_wal_photo_parity": True,
            "old_oci_signature_verified": False,
            "new_oci_signature_verified": False,
            "release_authorized": False,
            "rollback_authorized": False,
        }

    def receipt(self):
        """A staged receipt never authorizes installation by itself."""
        return {
            "transaction_token": self.args().token,
            "source_commit": COMMIT,
            "version": "0.5.26-hw",
            "digest": NEW_DIGEST,
        }

    def source_pair_proof(self):
        """A complete synthetic source-Sigstore and OCI result."""
        return {
            "status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
            "version": "0.5.26-hw",
            "commit": COMMIT,
            "digest": NEW_DIGEST,
            "digest_suffix": NEW_DIGEST[-12:],
            "source_signature_verified": True,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
            "release_authorized": False,
            "rollback_authorized": False,
        }

    def old_source_pair_proof(self):
        """Synthetic but exact old signed source and attested OCI identity."""
        return dict(
            self.source_pair_proof(),
            version="0.5.25-hw",
            commit=OLD_COMMIT,
            digest=OLD_DIGEST,
            digest_suffix=OLD_DIGEST[-12:],
        )

    def signed_arguments(self):
        """Supply a strict old source QA slot for two signed release checks."""
        args = self.args()
        args.verify_signatures = True
        args.old_commit = OLD_COMMIT
        args.old_source_archive_dir = CHECK.OLD_SOURCE_STAGE
        return args

    def assert_signed_report(self, result, args, source_verify) -> None:
        """Check both exact signatures without asserting activation."""
        self.assertEqual(source_verify.call_count, 2)
        old, new = [call.args[0] for call in source_verify.call_args_list]
        self.assertEqual(
            (old.version, old.commit, old.digest),
            ("0.5.25-hw", OLD_COMMIT, OLD_DIGEST),
        )
        self.assertEqual(old.archive.parent, CHECK.OLD_SOURCE_STAGE)
        self.assertEqual(
            (new.commit, new.digest, new.bundle.name),
            (
                COMMIT,
                NEW_DIGEST,
                "shelly-voltage-telegram-0.5.26-hw.tar.gz.sigstore.json",
            ),
        )
        self.assertEqual(
            result["status"], "PASS_QA_CUTOVER_SIGNED_OCI_COHERENCE_ONLY"
        )
        for key in (
            "old_source_signature_reverified",
            "new_source_signature_reverified",
            "old_oci_signature_reverified",
            "new_oci_signature_reverified",
        ):
            self.assertTrue(result[key])
        for key in (
            "release_authorized",
            "rollback_authorized",
            "image_actually_changed",
        ):
            self.assertFalse(result[key])

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

    def test_signed_cutover_rechecks_both_exact_git_shas(self) -> None:
        """Verify both source Sigstore bundles and OCI against exact SHAs."""
        args = self.signed_arguments()
        evidence = dict(
            self.baseline(),
            status="PASS_QA_SIGNED_OCI_AND_WAL_ONLY",
            old_oci_signature_verified=True,
            new_oci_signature_verified=True,
        )
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=evidence),
            patch.object(CHECK, "check_target", return_value=self.receipt()),
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            patch.object(
                CHECK.SOURCE_PAIR,
                "verify",
                side_effect=[
                    self.old_source_pair_proof(),
                    self.source_pair_proof(),
                ],
            ) as verify,
            patch.object(
                CHECK.SEALED,
                "sealed_pending_receipt",
                return_value=nullcontext(),
            ) as sealed,
        ):
            result = CHECK.evaluate(args)
        sealed.assert_called_once_with(args)
        self.assert_signed_report(result, args, verify)

    def test_mismatched_staged_source_commit_blocks_before_cosign(
        self,
    ) -> None:
        """Never reverify a TAR under a different source commit."""
        args = self.args()
        wrong = dict(self.receipt(), source_commit="c" * 40)
        with (
            patch.object(CHECK.SOURCE_PAIR, "verify") as verifier,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_NEW_PAIR_IDENTITY_INVALID"
            ),
        ):
            CHECK.reverify_signed_new_pair(args, wrong)
        verifier.assert_not_called()

    def test_missing_source_sigstore_bundle_blocks_signed_cutover(
        self,
    ) -> None:
        """Missing staged Sigstore bytes must block a signed QA report."""
        args = self.signed_arguments()
        evidence = dict(
            self.baseline(),
            status="PASS_QA_SIGNED_OCI_AND_WAL_ONLY",
            old_oci_signature_verified=True,
            new_oci_signature_verified=True,
        )
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=evidence),
            patch.object(CHECK, "check_target", return_value=self.receipt()),
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            patch.object(
                CHECK.SOURCE_PAIR,
                "verify",
                side_effect=[
                    self.old_source_pair_proof(),
                    ValueError("SOURCE_FILE_UNSAFE"),
                ],
            ) as verifier,
            self.assertRaisesRegex(ValueError, "SOURCE_FILE_UNSAFE"),
        ):
            CHECK.evaluate(args)
        self.assertEqual(verifier.call_count, 2)

    def test_incomplete_source_pair_result_blocks_signed_cutover(self) -> None:
        """Signed-looking OCI evidence cannot hide unsigned source bytes."""
        args = self.args()
        bad = {
            "status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
            "version": "0.5.26-hw",
            "commit": COMMIT,
            "digest": NEW_DIGEST,
            "digest_suffix": NEW_DIGEST[-12:],
            "source_signature_verified": False,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
            "release_authorized": False,
            "rollback_authorized": False,
        }
        with (
            patch.object(CHECK.SOURCE_PAIR, "verify", return_value=bad),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_NEW_PAIR_PROOF_INCOMPLETE"
            ),
        ):
            CHECK.reverify_signed_new_pair(args, self.receipt())

    def test_final_signed_recheck_blocks_changed_pair_receipt(self):
        """A changed receipt after network Cosign must not report PASS."""
        args = self.signed_arguments()
        replaced = dict(self.receipt(), transaction_token="qa-changed-token")
        with (
            patch.object(
                CHECK.SEALED,
                "sealed_pending_receipt",
                return_value=nullcontext(),
            ),
            patch.object(
                CHECK,
                "check_target",
                return_value=replaced,
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_PAIR_RECEIPT_DRIFT"
            ),
        ):
            CHECK.final_signed_pending_recheck(
                args, self.baseline(), self.receipt()
            )

    def test_final_signed_recheck_blocks_missing_pending(self):
        """A cleared marker during Cosign invalidates the staged WAL."""
        args = self.signed_arguments()
        with (
            patch.object(
                CHECK.SEALED,
                "sealed_pending_receipt",
                side_effect=ValueError("STAGE_PENDING_MARKER_UNSAFE"),
            ),
            patch.object(CHECK, "check_target") as target,
            self.assertRaisesRegex(ValueError, "STAGE_PENDING_MARKER_UNSAFE"),
        ):
            CHECK.final_signed_pending_recheck(
                args, self.baseline(), self.receipt()
            )
        target.assert_not_called()

    def test_final_signed_recheck_blocks_changed_old_image(self):
        """The final lock must also freeze prior rollback OCI identity."""
        args = self.signed_arguments()
        with (
            patch.object(
                CHECK.SEALED,
                "sealed_pending_receipt",
                return_value=nullcontext(),
            ),
            patch.object(CHECK, "check_target", return_value=self.receipt()),
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", NEW_DIGEST),
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_OLD_PAIR_IDENTITY_CHANGED"
            ),
        ):
            CHECK.final_signed_pending_recheck(
                args, self.baseline(), self.receipt()
            )

    def test_identity_only_never_enters_sealed_cryptographic_gate(self):
        """Plain coherence reports must not imply a signed transaction."""
        args = self.args()
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=self.baseline()),
            patch.object(CHECK, "check_target", return_value=self.receipt()),
            patch.object(CHECK.SEALED, "sealed_pending_receipt") as sealed,
        ):
            outcome = CHECK.evaluate(args)
        sealed.assert_not_called()
        self.assertFalse(outcome["source_signature_reverified"])

    def test_signed_mode_needs_private_old_source_directory(self) -> None:
        """Missing old Sigstore cannot be silently treated as OCI-only."""
        args = self.args()
        args.verify_signatures = True
        args.old_commit = OLD_COMMIT
        with (
            patch.object(CHECK.os, "geteuid", return_value=0),
            patch.object(
                CHECK.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(CHECK.BASE, "evaluate") as verify,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_OLD_SOURCE_STAGE_REQUIRED"
            ),
        ):
            CHECK.evaluate(args)
        verify.assert_not_called()

    def test_old_unsigned_source_blocks_before_new_probe(self) -> None:
        """A valid old OCI signature does not sign its source tarball."""
        args = self.signed_arguments()
        with (
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            patch.object(
                CHECK.SOURCE_PAIR,
                "verify",
                side_effect=ValueError("SOURCE_FILE_MISSING"),
            ) as verifier,
            self.assertRaisesRegex(ValueError, "SOURCE_FILE_MISSING"),
        ):
            CHECK.reverify_signed_old_pair(args, self.baseline())
        verifier.assert_called_once()
        self.assertEqual(
            verifier.call_args.args[0].bundle.parent,
            CHECK.OLD_SOURCE_STAGE,
        )

    def test_changed_old_oci_digest_blocks_source_proof(self) -> None:
        """A changed protected rollback image invalidates old TAR identity."""
        args = self.signed_arguments()
        with (
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", NEW_DIGEST),
            ),
            patch.object(CHECK.SOURCE_PAIR, "verify") as verifier,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_OLD_PAIR_IDENTITY_CHANGED"
            ),
        ):
            CHECK.reverify_signed_old_pair(args, self.baseline())
        verifier.assert_not_called()

    def test_old_source_incomplete_crypto_proof_fails_closed(self) -> None:
        """A valid SHA256 is not a substitute for old Sigstore identity."""
        args = self.signed_arguments()
        forged = dict(
            self.old_source_pair_proof(),
            source_signature_verified=False,
        )
        with (
            patch.object(
                CHECK.OLD,
                "receipt_identity",
                return_value=("0.5.25-hw", OLD_DIGEST),
            ),
            patch.object(CHECK.SOURCE_PAIR, "verify", return_value=forged),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_NEW_PAIR_PROOF_INCOMPLETE"
            ),
        ):
            CHECK.reverify_signed_old_pair(args, self.baseline())

    def test_signed_mode_requires_old_git_sha_before_io(self) -> None:
        """An old image tag without its source commit cannot claim proof."""
        args = self.args()
        args.verify_signatures = True
        with (
            patch.object(CHECK.os, "geteuid", return_value=0),
            patch.object(
                CHECK.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(CHECK.BASE, "evaluate") as verify,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_OLD_COMMIT_REQUIRED"
            ),
        ):
            CHECK.evaluate(args)
        verify.assert_not_called()

    def test_unsigned_mode_rejects_silent_commit_credential(self) -> None:
        """Supplying old SHA without requesting Cosign is ambiguous."""
        args = self.args()
        args.old_commit = OLD_COMMIT
        with (
            patch.object(CHECK.os, "geteuid", return_value=0),
            patch.object(
                CHECK.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_SIGNATURES_NOT_REQUESTED"
            ),
        ):
            CHECK.check_qa_boundary(args)

    def test_fake_signed_base_with_missing_oci_proof_refused(self) -> None:
        """A signed-looking status without both Cosign results is blocked."""
        args = self.signed_arguments()
        evidence = dict(
            self.baseline(),
            status="PASS_QA_SIGNED_OCI_AND_WAL_ONLY",
            old_oci_signature_verified=True,
            new_oci_signature_verified=False,
        )
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=evidence),
            patch.object(CHECK, "check_target") as target,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_OCI_PROOF_INCOMPLETE"
            ),
        ):
            CHECK.evaluate(args)
        target.assert_not_called()

    def test_identity_only_cannot_borrow_signed_status(self) -> None:
        """Signed-only evidence in unsigned mode is a hard policy conflict."""
        args = self.args()
        evidence = dict(
            self.baseline(),
            status="PASS_QA_SIGNED_OCI_AND_WAL_ONLY",
            old_oci_signature_verified=True,
            new_oci_signature_verified=True,
        )
        with (
            patch.object(CHECK, "check_qa_boundary"),
            patch.object(CHECK.BASE, "evaluate", return_value=evidence),
            patch.object(CHECK, "check_target") as target,
            self.assertRaisesRegex(
                CHECK.CutoverBlocked, "QA_CUTOVER_BASE_PROOF_INCOMPLETE"
            ),
        ):
            CHECK.evaluate(args)
        target.assert_not_called()

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
