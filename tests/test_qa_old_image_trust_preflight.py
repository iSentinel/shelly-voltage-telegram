"""Negative-first tests for non-authorizing old-image-only OCI audit."""

from __future__ import annotations

import argparse
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "svt_qa_old_oci_trust", SCRIPT / "qa-old-image-trust-preflight.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def old_args(**updates) -> argparse.Namespace:
    """Use a well-formed fictional version/commit/digest in isolated QA."""
    data = {
        "qa_only": True,
        "version": "0.5.25-hw",
        "commit": "a" * 40,
        "digest": "sha256:" + "b" * 64,
    }
    data.update(updates)
    return argparse.Namespace(**data)


class OldImageTrustTests(unittest.TestCase):
    """Verify failed old OCI proofs never become rollback permission."""

    def test_qa_flag_required(self) -> None:
        """Do not silently promote an audit into a production entrypoint."""
        with self.assertRaisesRegex(
            MODULE.OldImageBlocked, "OLD_OCI_QA_ONLY_REQUIRED"
        ):
            MODULE.verify_old_image(old_args(qa_only=False))

    def test_invalid_immutable_identity(self) -> None:
        """Reject mutable or malformed old OCI identity before network."""
        for changes in (
            {"version": "latest"},
            {"commit": "main"},
            {"digest": "ghcr.io/image:latest"},
        ):
            with (
                self.subTest(changes=changes),
                patch.object(MODULE.VERIFY, "verify_image_signature") as sig,
            ):
                with self.assertRaisesRegex(
                    MODULE.OldImageBlocked, "OLD_OCI_IDENTITY_INVALID"
                ):
                    MODULE.verify_old_image(old_args(**changes))
                sig.assert_not_called()

    def test_missing_public_signature_fails_closed(self) -> None:
        """An exact local digest is not proof of a signed release."""
        with (
            patch.object(
                MODULE.VERIFY,
                "verify_image_signature",
                side_effect=MODULE.VERIFY.PairBlocked("no signatures"),
            ),
            patch.object(
                MODULE.VERIFY, "verify_oci_revision_and_attestations"
            ) as att,
        ):
            with self.assertRaisesRegex(
                MODULE.OldImageBlocked, "OLD_OCI_SIGNATURE_UNVERIFIED"
            ):
                MODULE.verify_old_image(old_args())
            att.assert_not_called()

    def test_attestation_mismatch_fails_closed(self) -> None:
        """A signature without exact OCI revision proof is insufficient."""
        with (
            patch.object(
                MODULE.VERIFY, "verify_image_signature", return_value=None
            ),
            patch.object(
                MODULE.VERIFY,
                "verify_oci_revision_and_attestations",
                side_effect=MODULE.VERIFY.PairBlocked("revision drift"),
            ),
            self.assertRaisesRegex(
                MODULE.OldImageBlocked, "OLD_OCI_ATTESTATIONS_UNVERIFIED"
            ),
        ):
            MODULE.verify_old_image(old_args())

    def test_unexpected_verifier_results_fail_closed(self) -> None:
        """Treat unexpected verifier return objects as invalid proof."""
        with (
            patch.object(
                MODULE.VERIFY, "verify_image_signature", return_value=False
            ),
            self.assertRaisesRegex(
                MODULE.OldImageBlocked, "OLD_OCI_SIGNATURE_PROOF_INVALID"
            ),
        ):
            MODULE.verify_old_image(old_args())
        with (
            patch.object(
                MODULE.VERIFY, "verify_image_signature", return_value=None
            ),
            patch.object(
                MODULE.VERIFY,
                "verify_oci_revision_and_attestations",
                return_value=False,
            ),
            self.assertRaisesRegex(
                MODULE.OldImageBlocked, "OLD_OCI_ATTESTATION_PROOF_INVALID"
            ),
        ):
            MODULE.verify_old_image(old_args())

    def test_verified_oci_still_has_no_rollback_authority(self) -> None:
        """A signed image does not establish WAL, PHOTO or writer safety."""
        with (
            patch.object(
                MODULE.VERIFY, "verify_image_signature", return_value=None
            ),
            patch.object(
                MODULE.VERIFY,
                "verify_oci_revision_and_attestations",
                return_value=None,
            ),
        ):
            report = MODULE.verify_old_image(old_args())
        self.assertEqual(report["status"], "PASS_OLD_OCI_TRUST_EVIDENCE_ONLY")
        self.assertTrue(report["old_oci_signature_verified"])
        self.assertFalse(report["old_source_signature_verified"])
        self.assertFalse(report["sqlite_wal_photo_parity_verified"])
        self.assertFalse(report["sole_writer_cutover_verified"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["rollback_authorized"])
        self.assertFalse(report["production_modified"])


if __name__ == "__main__":
    unittest.main()
