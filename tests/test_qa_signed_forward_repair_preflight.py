"""Negative first checks for the non-deployable signed forward repair."""

from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "svt_signed_forward_test",
    SCRIPTS / "qa-signed-forward-repair-preflight.py",
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def inputs(**updates) -> argparse.Namespace:
    """Use synthetic image identities and nonexistent source bundle."""
    data = {
        "qa_only": True,
        "current_version": "0.5.25-hw",
        "current_digest": "sha256:" + "a" * 64,
        "candidate_version": "0.5.26-hw",
        "candidate_commit": "c" * 40,
        "candidate_digest": "sha256:" + "b" * 64,
        "archive": Path("/tmp/fictional-source.tar.gz"),
        "bundle": Path("/tmp/fictional-source.tar.gz.sigstore.json"),
        "sha256sums": Path("/tmp/fictional-SHA256SUMS"),
        "mounts_json": Path("/tmp/fictional-mounts.json"),
    }
    data.update(updates)
    return argparse.Namespace(**data)


def signed_result(pair: argparse.Namespace) -> dict:
    """Simulate a complete proof, never claim a real signature."""
    return {
        "status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
        "version": pair.version,
        "commit": pair.commit,
        "digest_suffix": pair.digest[-12:],
        "source_signature_verified": True,
        "oci_signature_verified": True,
        "oci_attestations_verified": True,
    }


class ForwardRepairTests(unittest.TestCase):
    """Require forward version and exact signed pair even in synthetic QA."""

    def test_forward_version_is_mandatory(self) -> None:
        """Older, same and mutable versions never trigger a repair."""
        for version in ("0.5.25-hw", "0.5.24-hw", "latest", "0.5.26-rc1"):
            with (
                self.subTest(version=version),
                self.assertRaises(QA.RepairBlocked),
            ):
                QA.evaluate(inputs(candidate_version=version))

    def test_qa_flag_is_mandatory(self) -> None:
        """A production request cannot use this diagnostic."""
        with self.assertRaisesRegex(
            QA.RepairBlocked, "REPAIR_QA_ONLY_REQUIRED"
        ):
            QA.evaluate(inputs(qa_only=False))

    def test_bad_current_digest_denied(self) -> None:
        """Baseline must be an immutable image digest, not a tag."""
        with self.assertRaisesRegex(
            QA.RepairBlocked, "REPAIR_CURRENT_DIGEST_INVALID"
        ):
            QA.evaluate(inputs(current_digest="latest"))

    def test_reused_oci_digest_denied(self) -> None:
        """A no-op image cannot be mislabelled as a repair."""
        with self.assertRaisesRegex(
            QA.RepairBlocked, "REPAIR_NEW_IMAGE_REQUIRED"
        ):
            QA.evaluate(inputs(candidate_digest="sha256:" + "a" * 64))

    def test_missing_signed_source_fails_closed(self) -> None:
        """No old image or unsigned source fallback is acceptable."""
        with (
            patch.object(
                QA.SIGNED, "verify", side_effect=ValueError("missing")
            ),
            patch.object(QA.MOUNTS, "read_mounts") as read,
        ):
            with self.assertRaisesRegex(
                QA.RepairBlocked, "REPAIR_SIGNED_PAIR_UNVERIFIED"
            ):
                QA.evaluate(inputs())
            read.assert_not_called()

    def test_incomplete_source_or_oci_proof_denied(self) -> None:
        """All source, index and provenance fields must be verified."""
        for key in (
            "source_signature_verified",
            "oci_signature_verified",
            "oci_attestations_verified",
        ):
            with (
                self.subTest(key=key),
                patch.object(QA.SIGNED, "verify") as signed,
            ):
                signed.side_effect = lambda pair, key=key: {
                    **signed_result(pair),
                    key: False,
                }
                with self.assertRaisesRegex(
                    QA.RepairBlocked, "REPAIR_SIGNED_PAIR_INCOMPLETE"
                ):
                    QA.evaluate(inputs())

    def test_exact_release_mismatch_denied(self) -> None:
        """Authenticated candidate must use its own tag, SHA and digest."""
        for key, value in (
            ("version", "0.5.27-hw"),
            ("commit", "d" * 40),
            ("digest_suffix", "e" * 12),
        ):
            with (
                self.subTest(key=key),
                patch.object(QA.SIGNED, "verify") as signed,
            ):
                signed.side_effect = lambda pair, key=key, value=value: {
                    **signed_result(pair),
                    key: value,
                }
                with self.assertRaisesRegex(
                    QA.RepairBlocked, "REPAIR_SIGNED_PAIR_INCOMPLETE"
                ):
                    QA.evaluate(inputs())

    def test_unsafe_mount_layout_denied(self) -> None:
        """Code bind mounts cannot be waived by a signed source."""
        with (
            patch.object(QA.SIGNED, "verify", side_effect=signed_result),
            patch.object(QA.MOUNTS, "read_mounts", return_value=[]),
            patch.object(
                QA.MOUNTS,
                "check_mounts",
                side_effect=QA.MOUNTS.MountBlocked("unexpected"),
            ),
            self.assertRaisesRegex(
                QA.RepairBlocked, "REPAIR_RUNTIME_LAYOUT_UNVERIFIED"
            ),
        ):
            QA.evaluate(inputs())

    def test_pass_still_forbids_start_and_database_restore(self) -> None:
        """A forged positive evidence report never authorizes actions."""
        with (
            patch.object(QA.SIGNED, "verify", side_effect=signed_result),
            patch.object(QA.MOUNTS, "read_mounts", return_value=[]),
            patch.object(
                QA.MOUNTS,
                "check_mounts",
                return_value={
                    "status": "PASS_RUNTIME_MOUNT_LAYOUT_EVIDENCE_ONLY"
                },
            ),
        ):
            report = QA.evaluate(inputs())
        self.assertEqual(
            report["status"], "PASS_SIGNED_FORWARD_REPAIR_EVIDENCE_ONLY"
        )
        for key in (
            "newest_wal_photo_pending_verified",
            "sole_writer_cutover_verified",
            "boot_recovery_verified",
            "release_authorized",
            "rollback_authorized",
            "production_modified",
        ):
            self.assertFalse(report[key])

    def test_nonexistent_assets_fail_without_network(self) -> None:
        """Real missing source bundles cannot pass via synthetic metadata."""
        with (
            tempfile.TemporaryDirectory() as temp,
            self.assertRaisesRegex(
                QA.RepairBlocked, "REPAIR_SIGNED_PAIR_UNVERIFIED"
            ),
        ):
            QA.evaluate(
                inputs(
                    archive=Path(temp) / "missing.tar.gz",
                    bundle=Path(temp) / "missing.sigstore.json",
                )
            )


if __name__ == "__main__":
    unittest.main()
