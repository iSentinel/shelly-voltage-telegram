"""Exact-SHA signed source+OCI pair policy without public registry traffic."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/verify-signed-release-pair.py"
)
SPEC = importlib.util.spec_from_file_location(
    "svt_signed_release_pair", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
PAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAIR)
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
VERSION = "0.5.26-hw"
SOURCE_OK = {
    "status": "PASS_SIGNED_SOURCE_BYTES_ONLY",
    "source_commit_bound": True,
    "release_authorized": False,
}


class SignedReleasePairTests(unittest.TestCase):
    """The pair proves immutable matching, not deployment authorization."""

    def args(self):
        """Create synthetic source paths, never touching production."""
        return argparse.Namespace(
            version=VERSION,
            commit=COMMIT,
            digest=DIGEST,
            archive=Path("/var/lib/qa-source/fake.tar.gz"),
            bundle=Path("/var/lib/qa-source/fake.sigstore.json"),
            sha256sums=Path("/var/lib/qa-source/SHA256SUMS"),
        )

    def test_signed_source_oci_evidence_does_not_deploy(self) -> None:
        """All three proofs are mandatory, yet release stays unauthorized."""
        with (
            patch.object(
                PAIR.SOURCE, "verify", return_value=SOURCE_OK
            ) as source,
            patch.object(PAIR, "verify_image_signature") as image,
            patch.object(PAIR, "verify_oci_revision_and_attestations") as oci,
        ):
            result = PAIR.verify(self.args())
        self.assertEqual(source.call_args.args[0].expected_commit, COMMIT)
        image.assert_called_once()
        oci.assert_called_once()
        self.assertEqual(result["status"], "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY")
        self.assertEqual(result["commit"], COMMIT)
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["rollback_authorized"])
        self.assertFalse(result["production_modified"])

    def test_malformed_immutable_pair_rejected_before_files(self) -> None:
        """Reject mutable tags, short digests, and invalid Git SHAs."""
        for change in (
            {"version": "latest"},
            {"commit": "refs/heads/main"},
            {"digest": "sha256:abc"},
        ):
            with self.subTest(change=change):
                args = self.args()
                for name, value in change.items():
                    setattr(args, name, value)
                with (
                    patch.object(PAIR.SOURCE, "verify") as source,
                    self.assertRaises(ValueError),
                ):
                    PAIR.verify(args)
                source.assert_not_called()

    def test_source_without_commit_certificate_is_blocked(self) -> None:
        """Version and SHA256 alone are not enough for a matching pair."""
        incomplete = dict(SOURCE_OK, source_commit_bound=False)
        with (
            patch.object(PAIR.SOURCE, "verify", return_value=incomplete),
            patch.object(PAIR, "verify_image_signature") as image,
            self.assertRaisesRegex(
                PAIR.PairBlocked, "PAIR_SOURCE_PROOF_INCOMPLETE"
            ),
        ):
            PAIR.verify(self.args())
        image.assert_not_called()

    def test_missing_source_bundle_blocks_before_oci(self) -> None:
        """The registry is never queried when source trust is incomplete."""
        with (
            patch.object(
                PAIR.SOURCE,
                "verify",
                side_effect=PAIR.SOURCE.SourceBlocked("SOURCE_FILE_MISSING"),
            ),
            patch.object(PAIR, "verify_image_signature") as image,
            self.assertRaisesRegex(
                PAIR.SOURCE.SourceBlocked, "SOURCE_FILE_MISSING"
            ),
        ):
            PAIR.verify(self.args())
        image.assert_not_called()

    def test_oci_signature_is_exact_digest_and_tag_workflow(self) -> None:
        """The signed index is verified, never an unpinned image tag."""
        with (
            patch.object(
                PAIR.SOURCE,
                "trusted_cosign",
                return_value="/usr/local/bin/cosign",
            ),
            patch.object(
                PAIR.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as execute,
        ):
            PAIR.verify_image_signature(self.args())
        command = execute.call_args.args[0]
        self.assertIn(
            "https://github.com/iSentinel/shelly-voltage-telegram/"
            ".github/workflows/image.yml@refs/tags/v0.5.26-hw",
            command,
        )
        self.assertEqual(command[-1], PAIR.IMAGE + DIGEST)
        self.assertIn(PAIR.SOURCE.ISSUER, command)
        self.assertNotIn("--insecure-ignore-tlog", command)

    def test_oci_signature_rejection_blocks_pair(self) -> None:
        """Even a valid source cannot bypass a failed Cosign OCI proof."""
        with (
            patch.object(
                PAIR.SOURCE,
                "trusted_cosign",
                return_value="/usr/local/bin/cosign",
            ),
            patch.object(
                PAIR.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ),
            self.assertRaisesRegex(
                PAIR.PairBlocked, "PAIR_OCI_SIGNATURE_UNVERIFIED"
            ),
        ):
            PAIR.verify_image_signature(self.args())

    def test_oci_index_kind_must_be_supported(self) -> None:
        """An unexpected registry manifest is not an attested image index."""
        with (
            patch.object(
                PAIR.OCI, "token_for_public_image", return_value="qa"
            ),
            patch.object(
                PAIR.OCI,
                "fetch_object",
                return_value={"mediaType": "application/not-an-oci-index"},
            ),
            patch.object(PAIR.OCI, "verify_image") as verify,
            self.assertRaisesRegex(PAIR.PairBlocked, "PAIR_OCI_INDEX_INVALID"),
        ):
            PAIR.verify_oci_revision_and_attestations(self.args())
        verify.assert_not_called()

    def test_each_architecture_requires_exact_commit_provenance(self) -> None:
        """Delegate both platforms, SLSA and SPDX to pinned public verifier."""
        index = {"mediaType": PAIR.OCI.INDEX_TYPES[0]}
        with (
            patch.object(
                PAIR.OCI, "token_for_public_image", return_value="qa"
            ),
            patch.object(PAIR.OCI, "fetch_object", return_value=index),
            patch.object(PAIR.OCI, "verify_image") as verify,
        ):
            PAIR.verify_oci_revision_and_attestations(self.args())
        verify.assert_called_once_with("qa", index, COMMIT)

    def test_mismatched_oci_revision_fails_closed(self) -> None:
        """Signed OCI with another source SHA cannot match the source blob."""
        index = {"mediaType": PAIR.OCI.INDEX_TYPES[0]}
        with (
            patch.object(
                PAIR.OCI, "token_for_public_image", return_value="qa"
            ),
            patch.object(PAIR.OCI, "fetch_object", return_value=index),
            patch.object(
                PAIR.OCI,
                "verify_image",
                side_effect=ValueError("Unexpected source revision"),
            ),
            self.assertRaisesRegex(ValueError, "source revision"),
        ):
            PAIR.verify_oci_revision_and_attestations(self.args())


if __name__ == "__main__":
    unittest.main()
