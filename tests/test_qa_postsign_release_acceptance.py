"""Refuse incomplete public source/OCI pairs without any live actions."""

from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "svt_qa_postsign", ROOT / "scripts/qa-postsign-release-acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def args(**overrides) -> argparse.Namespace:
    """Create syntactically valid but nonexistent QA input paths."""
    data = {
        "qa_only": True,
        "version": "0.5.26-hw",
        "commit": "a" * 40,
        "digest": "sha256:" + "b" * 64,
        "release_json": Path("/tmp/svt-qa-release-fixture.json"),
        "tag_ref_json": Path("/tmp/svt-qa-tag-fixture.json"),
        "archive": Path("/tmp/svt-qa-source-fixture.tar.gz"),
        "bundle": Path("/tmp/svt-qa-bundle-fixture.sigstore.json"),
        "sha256sums": Path("/tmp/svt-qa-SHA256SUMS"),
    }
    data.update(overrides)
    return argparse.Namespace(**data)


def inventory_proof(values: argparse.Namespace) -> dict:
    """Mock only the shape of the existing asset inventory checker."""
    return {
        "status": "PASS_PUBLIC_INVENTORY_ONLY",
        "version": values.version,
        "tag_commit_matches_input": True,
        "commit_format_valid": True,
        "digest_format_valid": True,
        "release_authorized": False,
    }


def signed_proof(values: argparse.Namespace) -> dict:
    """Mock exact-SHA, digest-bound proof for offline unit tests only."""
    return {
        "status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
        "version": values.version,
        "commit": values.commit,
        "digest_suffix": values.digest[-12:],
        "source_signature_verified": True,
        "oci_signature_verified": True,
        "oci_attestations_verified": True,
        "release_authorized": False,
        "rollback_authorized": False,
    }


class PostSignReleaseTests(unittest.TestCase):
    """No synthetic public asset may become deployment authorization."""

    def test_immutable_identity_required(self) -> None:
        """Reject absent QA flag, a mutable version or invalid source SHA."""
        for change in (
            {"qa_only": False},
            {"version": "latest"},
            {"commit": "HEAD"},
            {"digest": "sha256:latest"},
        ):
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(
                    QA.PostSignBlocked, "POSTSIGN_IMMUTABLE_IDENTITY_REQUIRED"
                ),
            ):
                QA.evaluate(args(**change))

    def test_missing_public_assets_fails_before_signature(self) -> None:
        """No signed-verifier network call for nonexistent release assets."""
        with (
            patch.object(
                QA.INVENTORY, "check", side_effect=ValueError("none")
            ),
            patch.object(QA.PAIR, "verify") as signed,
            self.assertRaisesRegex(
                QA.PostSignBlocked, "POSTSIGN_PUBLIC_INVENTORY_UNVERIFIED"
            ),
        ):
            QA.evaluate(args())
        signed.assert_not_called()

    def test_mismatched_public_tag_is_denied(self) -> None:
        """A fake matching tag or missing asset cannot reach Cosign."""
        with (
            patch.object(QA.INVENTORY, "check") as inventory,
            patch.object(QA.PAIR, "verify") as signed,
        ):
            inventory.side_effect = lambda v: {
                **inventory_proof(v),
                "tag_commit_matches_input": False,
            }
            with self.assertRaisesRegex(
                QA.PostSignBlocked, "POSTSIGN_INVENTORY_INCOMPLETE"
            ):
                QA.evaluate(args())
        signed.assert_not_called()

    def test_missing_signed_source_or_index_denied(self) -> None:
        """Require both Sigstore source and OCI without a local fallback."""
        with (
            patch.object(QA.INVENTORY, "check", side_effect=inventory_proof),
            patch.object(
                QA.PAIR, "verify", side_effect=ValueError("no signature")
            ),
            self.assertRaisesRegex(
                QA.PostSignBlocked, "POSTSIGN_SIGNED_PAIR_UNVERIFIED"
            ),
        ):
            QA.evaluate(args())

    def test_incomplete_cryptographic_proof_denied(self) -> None:
        """Refuse missing source, index signature or platform provenance."""
        fields = (
            "source_signature_verified",
            "oci_signature_verified",
            "oci_attestations_verified",
        )
        for field in fields:
            with (
                self.subTest(field=field),
                patch.object(
                    QA.INVENTORY, "check", side_effect=inventory_proof
                ),
                patch.object(QA.PAIR, "verify") as signed,
            ):
                signed.side_effect = lambda v, field=field: {
                    **signed_proof(v),
                    field: False,
                }
                with self.assertRaisesRegex(
                    QA.PostSignBlocked, "POSTSIGN_SIGNED_PAIR_INCOMPLETE"
                ):
                    QA.evaluate(args())

    def test_signed_commit_or_digest_must_match_exactly(self) -> None:
        """Never join an image and source from different Git revisions."""
        for key, value in (("commit", "c" * 40), ("digest_suffix", "d" * 12)):
            with (
                self.subTest(key=key),
                patch.object(
                    QA.INVENTORY, "check", side_effect=inventory_proof
                ),
                patch.object(QA.PAIR, "verify") as signed,
            ):
                signed.side_effect = lambda v, key=key, value=value: {
                    **signed_proof(v),
                    key: value,
                }
                with self.assertRaisesRegex(
                    QA.PostSignBlocked, "POSTSIGN_SIGNED_PAIR_INCOMPLETE"
                ):
                    QA.evaluate(args())

    def test_a_positive_pair_is_still_non_authorizing(self) -> None:
        """All actual release and cutover decisions remain separate."""
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "public.tar.gz"
            archive.write_bytes(b"synthetic-not-signed")
            with (
                patch.object(
                    QA.INVENTORY, "check", side_effect=inventory_proof
                ),
                patch.object(QA.PAIR, "verify", side_effect=signed_proof),
            ):
                report = QA.evaluate(args(archive=archive))
        self.assertEqual(
            report["status"], "PASS_QA_POSTSIGN_PUBLIC_PAIR_EVIDENCE_ONLY"
        )
        for key in (
            "public_inventory_authenticated_independently",
            "source_git_rebuild_parity_verified_here",
            "owner_approval_verified",
            "sole_writer_cutover_verified",
            "release_authorized",
            "rollback_authorized",
            "production_modified",
        ):
            self.assertFalse(report[key])

    def test_real_nonexistent_assets_never_pass(self) -> None:
        """Use actual inventory code with a missing release JSON."""
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "absent.json"
            with self.assertRaisesRegex(
                QA.PostSignBlocked, "POSTSIGN_PUBLIC_INVENTORY_UNVERIFIED"
            ):
                QA.evaluate(args(release_json=missing))


if __name__ == "__main__":
    unittest.main()
