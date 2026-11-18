"""Require a promoted EM-50 release to match its exact signed tag."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import image_release_policy as policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "scripts/image_release_policy.py"


def version(name: str) -> str:
    """Read one immutable release checkpoint marker."""

    return (ROOT / name).read_text(encoding="utf-8").strip()


class Em50ReleaseCandidateTests(unittest.TestCase):
    """Allow only the newly approved exact stable release tag."""

    def test_promotion_markers_are_equal(self):
        """The approved release must have matching stable version files."""

        self.assertEqual(version("VERSION"), "0.5.24-hw")
        self.assertEqual(version("RELEASE_VERSION"), "0.5.24-hw")
        self.assertEqual(version("LIVE_VERSION"), "0.5.6-hw")
        self.assertNotEqual(version("VERSION"), version("LIVE_VERSION"))

    def test_signed_release_accepts_only_its_exact_tag(self):
        """Never republish the old version under the new release source."""

        with (
            mock.patch.object(policy, "tag_commit", return_value="a" * 40),
            mock.patch.object(policy, "current_commit", return_value="a" * 40),
        ):
            policy.validate("push", "refs/tags/v0.5.24-hw", "", "", ROOT)
            with self.assertRaisesRegex(ValueError, "tag differs"):
                policy.validate("push", "refs/tags/v0.5.23-hw", "", "", ROOT)

    def test_a_tag_must_point_to_the_exact_approved_commit(self):
        """Even equal test markers must reject a mismatched Git SHA."""

        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory)
            for name in ("VERSION", "RELEASE_VERSION"):
                (sample / name).write_text("0.5.23-hw\n")
            with (
                mock.patch.object(policy, "tag_commit", return_value="a" * 40),
                mock.patch.object(
                    policy, "current_commit", return_value="b" * 40
                ),
                self.assertRaisesRegex(ValueError, "commit differs"),
            ):
                policy.validate("push", "refs/tags/v0.5.23-hw", "", "", sample)

    def test_published_marker_is_the_exact_release_target(self):
        """Stable and candidate markers may match only on release."""

        self.assertEqual(version("RELEASE_VERSION"), "0.5.24-hw")
        self.assertEqual(version("VERSION"), "0.5.24-hw")

    def test_manual_qa_build_ref_is_allowed(self):
        """Only a manual namespace may name an unsigned candidate."""

        run = subprocess.run(
            [
                sys.executable,
                str(POLICY),
                "--event",
                "workflow_dispatch",
                "--ref",
                "refs/heads/qa-em50",
                "--build-ref",
                "HEAD",
                "--image-tag",
                "manual-em50-rc1",
                "--root",
                str(ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_yasno_text_card_stays_disabled_by_default(self):
        """Runtime must require an explicit enabled feature flag."""

        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn(
            'os.getenv("TELEGRAM_YASNO_STATUS_ENABLED") != "1"',
            source,
        )
        self.assertIn('os.getenv("YASNO_TRUSTED_CACHE_FILE")', source)


if __name__ == "__main__":
    unittest.main()
