"""A prior in-principle owner decision is not a new release approval."""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "svt_owner_release_qa", ROOT / "scripts/qa-owner-release-review.py"
)
assert SPEC is not None and SPEC.loader is not None
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class OwnerReleaseReviewTests(unittest.TestCase):
    """Use private disposable files, not real approval or live manifests."""

    def setUp(self) -> None:
        """Create a synthetic source and CI packet under a private parent."""
        owner = tempfile.TemporaryDirectory(prefix="svt-approval-qa-")
        self.addCleanup(owner.cleanup)
        self.root = Path(owner.name)
        self.commit = "a" * 40
        self.old = "b" * 40
        self.source = self.root / "source.json"
        self.ci = self.root / "ci.json"
        self.history = self.root / "old.md"
        self.base = argparse.Namespace(
            qa_only=True,
            version="0.5.26-hw",
            commit=self.commit,
            source_manifest=self.source,
            ci_snapshot=self.ci,
            historical_approval=self.history,
            jsonl=True,
        )
        self.make_source()
        self.make_ci()
        self.history.write_text(
            "# Owner approval\n"
            "Decision: **Owner Approval GRANTED in principle**\n"
            f"latest checked source commit {self.old}\n"
            "  at the time of approval.\n"
            "not yet established. Do not treat a future build as\n"
            "implicitly approved\n"
        )

    def save(self, path: Path, value: dict) -> None:
        """Write one root- or user-owned private JSON fixture."""
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def make_source(self, **overrides) -> None:
        """Create plausible but wholly synthetic source-byte evidence."""
        data = {
            "status": "PASS_QA_EXACT_SOURCE_BYTES_ONLY",
            "source_commit": self.commit,
            "version": "0.5.26-hw",
            "source_sha256": "c" * 64,
            "git_source_reproducible": True,
            "source_sigstore_verified": False,
            "release_authorized": False,
        }
        data.update(overrides)
        self.save(self.source, data)

    def make_ci(self, **overrides) -> None:
        """List exact CI matrix names with no signature or real provenance."""
        data = {
            "headSha": self.commit,
            "status": "completed",
            "conclusion": "success",
            "jobs": [
                {
                    "name": name,
                    "status": "completed",
                    "conclusion": "success",
                }
                for name in (
                    "resolve",
                    "test (3.11)",
                    "test (3.12)",
                    "test (3.13)",
                )
            ],
        }
        data.update(overrides)
        self.save(self.ci, data)

    def test_matching_packet_is_not_owner_approval(self) -> None:
        """A PASS packet never grants publication or production changes."""
        report = QA.evaluate(self.base)
        self.assertEqual(report["status"], "PASS_OWNER_REVIEW_PACKET_ONLY")
        self.assertEqual(report["decision"], "NO_GO")
        self.assertEqual(report["historical_scope_commit"], self.old)
        self.assertEqual(report["candidate_commit"], self.commit)
        self.assertTrue(report["ci_snapshot_metadata_matches_candidate"])
        for field in (
            "historical_approval_authorizes_candidate",
            "ci_snapshot_independently_authenticated",
            "new_source_sigstore_verified",
            "new_oci_signature_verified",
            "new_release_specific_owner_approval",
            "sole_writer_cutover_verified",
            "release_authorized",
            "rollback_authorized",
            "production_modified",
        ):
            self.assertFalse(report[field])

    def test_qa_only_required(self) -> None:
        """Reject any production-intent invocation."""
        self.base.qa_only = False
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_REVIEW_IDENTITY_INVALID"
        ):
            QA.evaluate(self.base)

    def test_bad_commit_or_version_rejected(self) -> None:
        """Mutable release refs are never sufficient for a review packet."""
        for name, value in (("commit", "HEAD"), ("version", "latest")):
            with self.subTest(name=name):
                setattr(self.base, name, value)
                with self.assertRaisesRegex(
                    QA.ReviewBlocked, "OWNER_REVIEW_IDENTITY_INVALID"
                ):
                    QA.evaluate(self.base)
                setattr(
                    self.base,
                    name,
                    self.commit if name == "commit" else "0.5.26-hw",
                )

    def test_old_owner_scope_is_never_extended(self) -> None:
        """Even the same old source scope remains only in-principle."""
        self.make_source(source_commit=self.old)
        self.make_ci(headSha=self.old)
        self.base.commit = self.old
        report = QA.evaluate(self.base)
        self.assertFalse(report["historical_approval_is_release_specific"])
        self.assertFalse(report["historical_approval_authorizes_candidate"])

    def test_missing_owner_history_blocks(self) -> None:
        """Never guess a prior owner decision from surrounding docs."""
        self.history.unlink()
        with self.assertRaises(FileNotFoundError):
            QA.evaluate(self.base)

    def test_ambiguous_prior_approval_blocks(self) -> None:
        """Historical scope must disclose old exact SHA explicitly."""
        self.history.write_text("Owner Approval GRANTED in principle")
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_PRIOR_APPROVAL_SCOPE_UNKNOWN"
        ):
            QA.evaluate(self.base)

    def test_changed_candidate_sha_blocks(self) -> None:
        """Source manifest must belong to exact requested commit."""
        self.make_source(source_commit="d" * 40)
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_SOURCE_SCOPE_MISMATCH"
        ):
            QA.evaluate(self.base)

    def test_sigstore_boolean_cannot_be_forged(self) -> None:
        """A local source record claiming signatures cannot be promoted."""
        self.make_source(source_sigstore_verified=True)
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_SOURCE_SCOPE_MISMATCH"
        ):
            QA.evaluate(self.base)

    def test_stale_or_failed_ci_blocks(self) -> None:
        """A green source manifest alone cannot replace exact-head CI."""
        for change in (
            {"headSha": "d" * 40},
            {"status": "in_progress"},
            {"conclusion": "failure"},
        ):
            with self.subTest(change=change):
                self.make_ci(**change)
                with self.assertRaisesRegex(
                    QA.ReviewBlocked, "OWNER_CI_SNAPSHOT_NOT_GREEN"
                ):
                    QA.evaluate(self.base)

    def test_incomplete_python_matrix_blocks(self) -> None:
        """All Python versions must be present with distinct job names."""
        self.make_ci(
            jobs=[
                {
                    "name": "resolve",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        )
        with self.assertRaisesRegex(QA.ReviewBlocked, "OWNER_CI_JOBS_INVALID"):
            QA.evaluate(self.base)

    def test_duplicate_ci_job_blocks(self) -> None:
        """Duplicated CI names cannot overrule a failed matrix member."""
        valid = json.loads(self.ci.read_text())
        valid["jobs"].append(valid["jobs"][0])
        self.save(self.ci, valid)
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_CI_JOB_DUPLICATED"
        ):
            QA.evaluate(self.base)

    def test_public_readable_source_manifest_blocks(self) -> None:
        """Source review uses only owner-private evidence files."""
        self.source.chmod(0o644)
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_PRIVATE_EVIDENCE_UNSAFE"
        ):
            QA.evaluate(self.base)

    def test_symlink_ci_snapshot_blocks(self) -> None:
        """Reject linked CI snapshots rather than following new content."""
        link = self.root / "link-ci.json"
        link.symlink_to(self.ci)
        self.base.ci_snapshot = link
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_EVIDENCE_LINKED_PATH"
        ):
            QA.evaluate(self.base)

    def test_wrong_json_type_blocks(self) -> None:
        """A JSON array is not a source manifest."""
        self.source.write_text("[]")
        self.source.chmod(0o600)
        with self.assertRaisesRegex(
            QA.ReviewBlocked, "OWNER_SOURCE_MANIFEST_INVALID"
        ):
            QA.evaluate(self.base)


if __name__ == "__main__":
    unittest.main()
