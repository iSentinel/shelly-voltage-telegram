"""Keep the EM-50 candidate separate from published and live releases."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import image_release_policy as policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "scripts/image_release_policy.py"


def version(name: str) -> str:
    """Read one immutable release checkpoint marker."""

    return (ROOT / name).read_text(encoding="utf-8").strip()


class Em50ReleaseCandidateTests(unittest.TestCase):
    """Never authorize a stable tag from an unaccepted candidate."""

    def test_finalized_marker_matches_without_changing_live(self):
        """Final source marker remains separate from installed runtime."""

        self.assertEqual(version("VERSION"), "0.5.15-hw")
        self.assertEqual(version("RELEASE_VERSION"), "0.5.14-hw")
        self.assertNotEqual(version("VERSION"), version("RELEASE_VERSION"))
        self.assertNotEqual(version("VERSION"), version("LIVE_VERSION"))

    def test_finalized_tag_requires_exact_source_commit(self):
        """Tag creation must not permit a different checked-out SHA."""

        with self.assertRaisesRegex(ValueError, "VERSION differs"):
            policy.validate("push", "refs/tags/v0.5.15-hw", "", "", ROOT)

        # Mock ONLY finalized markers so this release identity regression
        # stays meaningful while the real unpublished marker stays old.
        with (
            patch.object(policy, "version_file", return_value="0.5.15-hw"),
            patch.object(policy, "tag_commit", return_value="wrong"),
            patch.object(policy, "current_commit", return_value="expected"),
            self.assertRaisesRegex(ValueError, "commit differs"),
        ):
            policy.validate("push", "refs/tags/v0.5.15-hw", "", "", ROOT)

        with (
            patch.object(policy, "version_file", return_value="0.5.15-hw"),
            patch.object(policy, "tag_commit", return_value="expected"),
            patch.object(policy, "current_commit", return_value="expected"),
        ):
            policy.validate("push", "refs/tags/v0.5.15-hw", "", "", ROOT)

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
