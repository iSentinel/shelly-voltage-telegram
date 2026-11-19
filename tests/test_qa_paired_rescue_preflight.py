"""Offline paired source, OCI and newest-history transaction checks."""

from __future__ import annotations

import argparse
import importlib.util
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/qa-paired-rescue-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("svt_qa_pair_rescue", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PairedRescuePreflightTests(unittest.TestCase):
    """Require the full QA history check alongside signed pair identity."""

    def setUp(self) -> None:
        """Declare only isolated, synthetic QA paths."""
        self.args = argparse.Namespace(
            qa_only=True,
            source_snapshot=Path("/var/lib/svt-qa-proof/upgrades"),
            signed_snapshot=Path(
                "/var/lib/svt-qa-proof/20261118T015250Z-0.5.25-hw"
            ),
            new_receipt=Path("/var/lib/svt-qa-proof/new-deployed.json"),
            new_version=Path("/var/lib/svt-qa-proof/RELEASE_VERSION"),
            database=Path("/var/tmp/svt-qa-proof/history.sqlite3"),
            fence_dir=Path("/var/lib/svt-qa-proof-fence"),
            output=Path("/var/lib/svt-qa-proof-rescue/newest.sqlite3"),
            container="svt-qa-proof",
            token="qa-proof",
            jsonl=True,
        )
        self.old = {
            "source_version": "0.5.24-hw",
            "target_version": "0.5.25-hw",
            "old_digest_suffix": "aaaaaaaaaaaa",
            "signature_verified": False,
        }

    def mocks(self):
        """Mock only the individual already-tested offline preflights."""
        return (
            patch.object(MODULE.STAGE, "check_paths"),
            patch.object(MODULE.STAGE, "inspect_container", return_value={}),
            patch.object(MODULE.STAGE, "check_container"),
            patch.object(MODULE.STAGE, "inspect_stage"),
            patch.object(MODULE.PAIR, "check_pair", return_value=self.old),
            patch.object(
                MODULE.OWNER,
                "source_version",
                return_value="0.5.25-hw",
            ),
            patch.object(
                MODULE.OWNER,
                "verify_receipt",
                return_value=MODULE.OWNER.IMAGE + "sha256:" + "b" * 64,
            ),
        )

    def mocked(self) -> ExitStack:
        """Enable helper mocks and a simulated QA root."""
        stack = ExitStack()
        for patcher in self.mocks():
            stack.enter_context(patcher)
        stack.enter_context(patch.object(MODULE.os, "geteuid", return_value=0))
        return stack

    def test_valid_identity_and_history_never_authorize_release(self) -> None:
        """Identity-only evidence cannot become a signed OTA approval."""
        with (
            self.mocked(),
        ):
            result = MODULE.evaluate(self.args)
        self.assertEqual(result["status"], "PASS_QA_IDENTITY_AND_WAL_ONLY")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["rollback_authorized"])
        self.assertFalse(result["old_oci_signature_verified"])
        self.assertFalse(result["new_oci_signature_verified"])
        self.assertTrue(result["sqlite_wal_photo_parity"])

    def test_mismatched_old_pair_target_refused(self) -> None:
        """The old transition must target the installed new version."""
        with (
            self.mocked(),
            patch.object(
                MODULE.OWNER,
                "source_version",
                return_value="0.5.26-hw",
            ),
            self.assertRaisesRegex(
                MODULE.PairBlocked, "TARGET_VERSION_MISMATCH"
            ),
        ):
            MODULE.evaluate(self.args)

    def test_staged_backup_refusal_propagates(self) -> None:
        """Tampered or missing pending rescue stops the full preflight."""
        with (
            self.mocked(),
            patch.object(
                MODULE.STAGE,
                "inspect_stage",
                side_effect=MODULE.STAGE.StageBlocked(
                    "SVT_QA_RESCUE_HISTORY_MISMATCH"
                ),
            ),
            self.assertRaisesRegex(
                MODULE.STAGE.StageBlocked, "HISTORY_MISMATCH"
            ),
        ):
            MODULE.evaluate(self.args)

    def test_old_signed_pair_refusal_propagates(self) -> None:
        """A wrong old digest must refuse the entire joined transaction."""
        with (
            self.mocked(),
            patch.object(
                MODULE.PAIR,
                "check_pair",
                side_effect=MODULE.PAIR.Blocked(
                    "OLD_OVERRIDE_DIGEST_MISMATCH"
                ),
            ),
            self.assertRaisesRegex(
                MODULE.PAIR.Blocked, "OLD_OVERRIDE_DIGEST_MISMATCH"
            ),
        ):
            MODULE.evaluate(self.args)

    def test_nonqa_paths_are_refused(self) -> None:
        """Never inspect production receipt paths with this QA wrapper."""
        with self.assertRaisesRegex(
            MODULE.PairBlocked, "QA_SIGNED_PATH_INVALID"
        ):
            MODULE.private_qa_path(
                Path("/var/lib/shelly-voltage-updater/deployed.json")
            )

    def test_empty_signed_status_not_promoted(self) -> None:
        """Even signature metadata inconsistencies must fail closed."""
        self.old["signature_verified"] = True
        with (
            self.mocked(),
            self.assertRaisesRegex(
                MODULE.PairBlocked, "SIGNATURE_STATUS_UNEXPECTED"
            ),
        ):
            MODULE.evaluate(self.args)


if __name__ == "__main__":
    unittest.main()
