"""Strict, non-deploying Cosign verification for both OCI versions."""

from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/qa-paired-rescue-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("svt_qa_sig_pair", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
OLD = "0.5.24-hw"
NEW = "0.5.25-hw"
OLD_DIGEST = "sha256:" + "a" * 64
NEW_DIGEST = "sha256:" + "b" * 64


class QAPairCosignTests(unittest.TestCase):
    """The signature option may only yield evidence, never deployment."""

    def test_verification_uses_pinned_workflow_and_exact_digest(self):
        """Do not accept mutable tags or a different GitHub workflow."""
        with patch.object(
            MODULE.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as execute:
            MODULE.verify_oci_signature(
                OLD, OLD_DIGEST, "/usr/local/bin/cosign"
            )
        invocation = execute.call_args.args[0]
        self.assertEqual(invocation[:2], ["/usr/local/bin/cosign", "verify"])
        self.assertIn(
            "https://github.com/iSentinel/shelly-voltage-telegram/"
            ".github/workflows/image.yml@refs/tags/v0.5.24-hw",
            invocation,
        )
        self.assertIn(
            "https://token.actions.githubusercontent.com", invocation
        )
        self.assertEqual(invocation[-1], MODULE.PAIR.IMAGE + OLD_DIGEST)

    def test_old_and_new_verifications_are_both_required(self):
        """Both old and new OCI proofs must pass independently."""
        with (
            patch.object(
                MODULE, "trusted_cosign", return_value="/usr/local/bin/cosign"
            ),
            patch.object(
                MODULE.PAIR, "receipt_identity", return_value=(OLD, OLD_DIGEST)
            ),
            patch.object(MODULE, "verify_oci_signature") as verify,
        ):
            MODULE.verify_old_new_signatures(
                OLD,
                Path("/var/lib/svt-qa-old"),
                NEW,
                MODULE.PAIR.IMAGE + NEW_DIGEST,
            )
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(verify.call_args_list[0].args[:2], (OLD, OLD_DIGEST))
        self.assertEqual(verify.call_args_list[1].args[:2], (NEW, NEW_DIGEST))

    def test_second_signature_failure_blocks_transaction(self):
        """A signed old image does not validate an unsigned new image."""
        with (
            patch.object(
                MODULE, "trusted_cosign", return_value="/usr/local/bin/cosign"
            ),
            patch.object(
                MODULE.PAIR, "receipt_identity", return_value=(OLD, OLD_DIGEST)
            ),
            patch.object(
                MODULE,
                "verify_oci_signature",
                side_effect=[
                    None,
                    MODULE.PairBlocked("QA_OCI_SIGNATURE_UNVERIFIED"),
                ],
            ),
            self.assertRaisesRegex(MODULE.PairBlocked, "SIGNATURE_UNVERIFIED"),
        ):
            MODULE.verify_old_new_signatures(
                OLD,
                Path("/var/lib/svt-qa-old"),
                NEW,
                MODULE.PAIR.IMAGE + NEW_DIGEST,
            )

    def test_changed_old_receipt_blocks_before_cosign(self):
        """Do not verify a changed old pair after reading the transition."""
        with (
            patch.object(
                MODULE, "trusted_cosign", return_value="/usr/local/bin/cosign"
            ),
            patch.object(
                MODULE.PAIR,
                "receipt_identity",
                return_value=("0.5.23-hw", OLD_DIGEST),
            ),
            patch.object(MODULE, "verify_oci_signature") as verify,
            self.assertRaisesRegex(
                MODULE.PairBlocked, "OLD_SIGNED_PAIR_CHANGED"
            ),
        ):
            MODULE.verify_old_new_signatures(
                OLD,
                Path("/var/lib/svt-qa-old"),
                NEW,
                MODULE.PAIR.IMAGE + NEW_DIGEST,
            )
        verify.assert_not_called()

    def test_unverified_signature_fails_closed(self):
        """Cosign exit failure is a blocking error."""
        with (
            patch.object(
                MODULE.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1),
            ),
            self.assertRaisesRegex(MODULE.PairBlocked, "SIGNATURE_UNVERIFIED"),
        ):
            MODULE.verify_oci_signature(
                NEW, NEW_DIGEST, "/usr/local/bin/cosign"
            )

    def test_cosign_timeout_blocks(self):
        """An unavailable remote transparency lookup cannot be a pass."""
        with (
            patch.object(
                MODULE.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["cosign"], 70),
            ),
            self.assertRaisesRegex(
                MODULE.PairBlocked, "SIGNATURE_UNAVAILABLE"
            ),
        ):
            MODULE.verify_oci_signature(
                NEW, NEW_DIGEST, "/usr/local/bin/cosign"
            )

    def test_missing_cosign_is_not_silent_fallback(self):
        """An uninstalled verifier never turns a digest check into a PASS."""
        with (
            patch.object(
                MODULE.Path, "lstat", side_effect=FileNotFoundError()
            ),
            self.assertRaisesRegex(MODULE.PairBlocked, "COSIGN_UNAVAILABLE"),
        ):
            MODULE.trusted_cosign()

    def test_version_and_digest_must_be_immutable(self):
        """Reject anything other than exact pinned versions and digests."""
        for version, digest in (
            ("latest", OLD_DIGEST),
            (OLD, "latest"),
            (OLD, "sha256:1234"),
        ):
            with (
                self.subTest(version=version, digest=digest),
                self.assertRaisesRegex(MODULE.PairBlocked, "IDENTITY_INVALID"),
            ):
                MODULE.verify_oci_signature(
                    version, digest, "/usr/local/bin/cosign"
                )


if __name__ == "__main__":
    unittest.main()
