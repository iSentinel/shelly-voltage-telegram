"""Keep the EM-50 candidate separate from published and live releases."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "scripts/image_release_policy.py"


def version(name: str) -> str:
    """Read one immutable release checkpoint marker."""

    return (ROOT / name).read_text(encoding="utf-8").strip()


class Em50ReleaseCandidateTests(unittest.TestCase):
    """Never authorize a stable tag from an unaccepted candidate."""

    def test_candidate_has_distinct_version(self):
        """Candidate name must not rewrite published/live markers."""

        self.assertEqual(version("VERSION"), "0.5.14-hw")
        self.assertNotEqual(version("VERSION"), version("RELEASE_VERSION"))
        self.assertNotEqual(version("VERSION"), version("LIVE_VERSION"))

    def test_unpublished_candidate_cannot_pass_stable_tag_policy(self):
        """A release tag is blocked while RELEASE_VERSION is unchanged."""

        run = subprocess.run(
            [
                sys.executable,
                str(POLICY),
                "--event",
                "push",
                "--ref",
                "refs/tags/v0.5.14-hw",
                "--root",
                str(ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("VERSION differs from RELEASE_VERSION", run.stderr)

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
