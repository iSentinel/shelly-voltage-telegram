"""Verify stable image publication requires the exact release Git tag."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import image_release_policy as policy


class ImageReleasePolicyTests(unittest.TestCase):
    """Reject unsafe GitHub Actions tags before any image is pushed."""

    def setUp(self):
        """Create version metadata without a real Git repository."""

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "VERSION").write_text("0.5.13-hw\n")
        (self.root / "RELEASE_VERSION").write_text("0.5.13-hw\n")

    def validate(self, event, ref, build_ref="", image_tag=""):
        """Call the release policy with isolated fixture metadata."""

        policy.validate(event, ref, build_ref, image_tag, self.root)

    def test_main_push_has_no_stable_tag(self):
        """Allow ordinary development builds on the main branch."""

        self.validate("push", "refs/heads/main")

    def test_manual_build_must_use_manual_tag_namespace(self):
        """Prevent manual jobs from overwriting signed stable image tags."""

        self.validate(
            "workflow_dispatch",
            "refs/heads/main",
            "main",
            "manual-qa-image",
        )
        self.validate("workflow_dispatch", "refs/heads/main", "main")
        for tag in ("v0.5.13-hw", "latest", "sha-abcd", "main"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.validate(
                    "workflow_dispatch", "refs/heads/main", "main", tag
                )

    def test_manual_build_ref_rejects_command_characters(self):
        """Reject shell metacharacters in manual build references."""

        with self.assertRaisesRegex(ValueError, "build ref"):
            self.validate(
                "workflow_dispatch", "refs/heads/main", "main;exit 1"
            )

    def test_signed_tag_push_matches_both_version_files_and_commit(self):
        """Sign only a checked-out commit matching a stable release tag."""

        with (
            patch.object(policy, "tag_commit", return_value="abc123"),
            patch.object(policy, "current_commit", return_value="abc123"),
        ):
            self.validate("push", "refs/tags/v0.5.13-hw")

    def test_reject_tag_version_mismatch(self):
        """Release metadata must agree with the pushed Git tag."""

        with self.assertRaisesRegex(ValueError, "release tag differs"):
            self.validate("push", "refs/tags/v0.5.14-hw")

    def test_reject_release_version_mismatch(self):
        """A candidate cannot be published as an accepted release."""

        (self.root / "RELEASE_VERSION").write_text("0.5.12-hw\n")
        with self.assertRaisesRegex(ValueError, "RELEASE_VERSION"):
            self.validate("push", "refs/tags/v0.5.13-hw")

    def test_reject_unexpected_tag_format(self):
        """Only numeric vX.Y.Z-hw release tags are allowed."""

        for ref in (
            "refs/tags/v0.5.13-rc1",
            "refs/tags/v0.5.13-hw-malicious",
            "refs/tags/latest",
            "refs/heads/other",
        ):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.validate("push", ref)

    def test_reject_tag_pointing_to_other_commit(self):
        """Refuse an image build if Git checkout is not at the tag."""

        with (
            patch.object(policy, "tag_commit", return_value="oldcommit"),
            patch.object(policy, "current_commit", return_value="newcommit"),
            self.assertRaisesRegex(ValueError, "commit differs"),
        ):
            self.validate("push", "refs/tags/v0.5.13-hw")

    def test_cli_help(self):
        """Document the validation tool without running any checks."""

        result = subprocess.run(
            ["python3", "scripts/image_release_policy.py", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--image-tag", result.stdout)


if __name__ == "__main__":
    unittest.main()
