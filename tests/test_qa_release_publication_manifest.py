"""Fail-closed tests for an exact-source non-authorizing QA manifest."""

from __future__ import annotations

import argparse
import importlib.util
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "svt_qa_manifest",
    ROOT / "scripts/qa-release-publication-manifest.py",
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def inputs(**changes) -> argparse.Namespace:
    """Use only synthetic, explicitly pinned source and archive identities."""
    data = {
        "qa_only": True,
        "repo": Path("/tmp/svt-forward-qa-fixture"),
        "version": "0.5.26-hw",
        "commit": "a" * 40,
        "archive": Path(
            "/root/svt-qa-fixture/shelly-voltage-telegram-0.5.26-hw.tar.gz"
        ),
        "sha256sums": Path("/root/svt-qa-fixture/SHA256SUMS"),
        "output": None,
        "jsonl": True,
    }
    data.update(changes)
    return argparse.Namespace(**data)


class SourceManifestTests(unittest.TestCase):
    """Never confuse local source-byte parity with authorization to publish."""

    def test_qa_only_required(self) -> None:
        """Reject production-shaped invocations."""
        with self.assertRaisesRegex(
            QA.ManifestBlocked, "MANIFEST_QA_INPUT_INVALID"
        ):
            QA.validate_repo(inputs(qa_only=False))

    def test_bad_commit_and_version_blocked(self) -> None:
        """Reject mutable refs and wrong release format."""
        for changes in ({"commit": "HEAD"}, {"version": "latest"}):
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(
                    QA.ManifestBlocked, "MANIFEST_QA_INPUT_INVALID"
                ),
            ):
                QA.validate_repo(inputs(**changes))

    def test_source_root_and_sha_must_match(self) -> None:
        """A local Git directory is not evidence of the selected commit."""
        with (
            patch.object(Path, "is_dir", return_value=True),
            patch.object(
                QA,
                "git",
                side_effect=["/tmp/svt-forward-qa-fixture", "b" * 40],
            ),
            self.assertRaisesRegex(
                QA.ManifestBlocked, "MANIFEST_GIT_SHA_MISMATCH"
            ),
        ):
            QA.validate_repo(inputs())

    def test_dirty_tree_never_passes(self) -> None:
        """Untracked draft changes invalidate exact Git reproducibility."""
        with (
            patch.object(Path, "is_dir", return_value=True),
            patch.object(
                QA,
                "git",
                side_effect=[
                    "/tmp/svt-forward-qa-fixture",
                    "a" * 40,
                    " M VERSION",
                ],
            ),
            self.assertRaisesRegex(
                QA.ManifestBlocked, "MANIFEST_DIRTY_SOURCE"
            ),
        ):
            QA.validate_repo(inputs())

    def test_wrong_archive_name_blocked(self) -> None:
        """The archive name must bind the exact candidate version."""
        with self.assertRaisesRegex(
            QA.ManifestBlocked, "MANIFEST_ARCHIVE_PATH_MISMATCH"
        ):
            QA.verify_archive(inputs(archive=Path("/root/other.tar.gz")))

    def test_checksum_and_archive_validation_called(self) -> None:
        """Reuse existing source validators and never bypass malformed tar."""
        with (
            patch.object(QA.SOURCE, "safe_file") as safe,
            patch.object(
                QA.SOURCE, "verify_checksums", side_effect=ValueError("bad")
            ),
            self.assertRaises(ValueError),
        ):
            QA.verify_archive(inputs())
        self.assertEqual(safe.call_count, 2)

    def test_rebuild_mismatch_rejected(self) -> None:
        """An exact SHA alone does not authenticate altered archive bytes."""
        with (
            patch.object(QA, "git", return_value=b"bad archive"),
            self.assertRaises((QA.ManifestBlocked, tarfile.TarError)),
        ):
            QA.reproduce(inputs(), 12345, "b" * 64)

    def test_positive_evidence_never_approves_release(self) -> None:
        """Even mocked full source parity is not a signed release."""
        with (
            patch.object(QA, "validate_repo", return_value=12345),
            patch.object(QA, "verify_archive", return_value="b" * 64),
            patch.object(QA, "reproduce") as reproduction,
        ):
            report = QA.plan(inputs())
        reproduction.assert_called_once()
        self.assertEqual(report["status"], "PASS_QA_EXACT_SOURCE_BYTES_ONLY")
        for key in (
            "ci_authentication_verified",
            "source_sigstore_verified",
            "oci_signature_verified",
            "owner_approval_verified",
            "release_authorized",
            "rollback_authorized",
            "production_modified",
        ):
            self.assertFalse(report[key])

    def test_report_output_must_be_private_and_exact(self) -> None:
        """Do not write release manifests into an arbitrary user path."""
        with self.assertRaisesRegex(
            QA.ManifestBlocked, "MANIFEST_OUTPUT_PATH_INVALID"
        ):
            QA.save_report(
                Path("/tmp/not-private.json"), {"source_commit": "a" * 40}
            )

    def test_report_existing_name_cannot_be_replaced(self) -> None:
        """A second QA run must not overwrite a published evidence file."""
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            name = folder / ("source-" + "a" * 40 + ".json")
            name.write_text("keep")
            with (
                patch.object(QA, "MANIFEST_DIR", folder),
                self.assertRaises((FileExistsError, QA.ManifestBlocked)),
            ):
                QA.save_report(name, {"source_commit": "a" * 40})
            self.assertEqual(name.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
