"""Keep the EM-50 candidate separate from published and live releases."""

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
    """Never authorize a stable tag from an unaccepted candidate."""

    def test_promotion_target_is_distinct_from_live_device(self):
        """Release proposal must not pretend the meter has been upgraded."""

        self.assertEqual(version("VERSION"), "0.5.22-hw")
        self.assertEqual(version("RELEASE_VERSION"), "0.5.21-hw")
        self.assertEqual(version("LIVE_VERSION"), "0.5.6-hw")
        self.assertNotEqual(version("VERSION"), version("LIVE_VERSION"))
        self.assertNotEqual(version("VERSION"), version("RELEASE_VERSION"))

    def test_unsigned_candidate_rejects_both_stable_tags(self):
        """Unpromoted VERSION and RELEASE_VERSION must prevent signing."""

        with (
            mock.patch.object(policy, "tag_commit") as tag_commit,
            mock.patch.object(policy, "current_commit") as current,
        ):
            for ref in ("v0.5.21-hw", "v0.5.22-hw"):
                with (
                    self.subTest(ref=ref),
                    self.assertRaisesRegex(ValueError, "VERSION differs"),
                ):
                    policy.validate("push", "refs/tags/" + ref, "", "", ROOT)
            tag_commit.assert_not_called()
            current.assert_not_called()

    def test_a_tag_must_point_to_the_exact_approved_commit(self):
        """Even equal test markers must reject a mismatched Git SHA."""

        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory)
            for name in ("VERSION", "RELEASE_VERSION"):
                (sample / name).write_text("0.5.22-hw\n")
            with (
                mock.patch.object(policy, "tag_commit", return_value="a" * 40),
                mock.patch.object(
                    policy, "current_commit", return_value="b" * 40
                ),
                self.assertRaisesRegex(ValueError, "commit differs"),
            ):
                policy.validate("push", "refs/tags/v0.5.22-hw", "", "", sample)

    def test_staging_does_not_retag_existing_release(self):
        """The published 0.5.21 marker must remain untouched."""

        self.assertEqual(version("RELEASE_VERSION"), "0.5.21-hw")
        self.assertEqual(version("VERSION"), "0.5.22-hw")

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
