"""Test sole-owner NVMe waiver without turning it into release consent."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_owner_risk_audit", ROOT / "scripts/em50-owner-risk-audit.py"
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)

COMMIT = "a" * 40


def receipt_data() -> dict:
    """Create a complete synthetic owner governance decision."""

    result = dict(AUDIT.REQUIRED)
    result.update(
        {
            "recorded_at_kyiv": "2026-11-17T12:07:11+02:00",
            "exact_version_or_tag_approved": None,
            "sole_owner_source_review_of_specific_candidate": None,
            "signed_current_oci_verified": False,
        }
    )
    return result


class OwnerSingleNvmeAuditTests(unittest.TestCase):
    """Use only disposable local decision files and no live Telegram API."""

    def setUp(self) -> None:
        """Create a private directory and a synthetic decision receipt."""

        self.directory = tempfile.TemporaryDirectory(prefix="owner-risk-qa-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "decision.json"
        self.write(receipt_data())

    def write(self, data: object) -> None:
        """Store a root/nobody-owned receipt with strict local permissions."""

        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.path.chmod(0o600)

    def inspect(self) -> dict:
        """Run the validated governance check on a fixed dummy commit."""

        return AUDIT.inspect(self.path, COMMIT, datetime.now(timezone.utc))

    def test_valid_owner_risk_not_release(self) -> None:
        """The single-device waiver must not authorize a source promotion."""

        result = self.inspect()
        self.assertEqual(result["status"], "RISK_ACCEPTED")
        self.assertTrue(result["owner_single_nvme_risk_approved"])
        self.assertFalse(result["offhost_backup_verified"])
        self.assertFalse(result["candidate_commit_verified"])
        self.assertFalse(result["source_review_approved"])
        self.assertFalse(result["signed_oci_verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["photo_authorized"])

    def test_each_required_field_fail_closed(self) -> None:
        """Reject missing or wrong-typed governance values."""

        for key in AUDIT.REQUIRED:
            with self.subTest(field=key):
                data = receipt_data()
                data.pop(key)
                self.write(data)
                self.assertEqual(self.inspect()["status"], "BLOCKED")
                data = receipt_data()
                data[key] = not AUDIT.REQUIRED[key]
                self.write(data)
                self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_an_attempted_photo_grant_fails(self) -> None:
        """Do not accept a risk waiver that claims a PHOTO cutover."""

        data = receipt_data()
        data["public_telegram_photo_approved"] = True
        self.write(data)
        self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_specific_release_scope_is_not_risk_approval(self) -> None:
        """Separate a source review or version from the NVMe waiver."""

        for field, value in (
            ("exact_version_or_tag_approved", "0.5.22-hw"),
            ("sole_owner_source_review_of_specific_candidate", COMMIT),
            ("signed_current_oci_verified", True),
        ):
            with self.subTest(field=field):
                data = receipt_data()
                data[field] = value
                self.write(data)
                self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_future_timestamp_rejected(self) -> None:
        """Prevent a not-yet-issued waiver from passing."""

        data = receipt_data()
        data["recorded_at_kyiv"] = (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat()
        self.write(data)
        self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_naive_timestamp_rejected(self) -> None:
        """Do not interpret an unzoned receipt as approved."""

        data = receipt_data()
        data["recorded_at_kyiv"] = "2026-11-17T12:07:11"
        self.write(data)
        self.assertEqual(self.inspect()["status"], "BLOCKED")

    def test_invalid_commit_rejected(self) -> None:
        """Require an exact lowercase SHA label even for a risk report."""

        for value in ("latest", "A" * 40, "a" * 39, "../main"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AUDIT.inspect(self.path, value, datetime.now(timezone.utc))

    def test_file_mode_rejected(self) -> None:
        """World-readable owner decisions must not be trusted."""

        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.inspect()

    def test_symlink_rejected(self) -> None:
        """Reject a symbolic link regardless of its target permissions."""

        link = self.path.with_name("alias.json")
        link.symlink_to(self.path)
        with self.assertRaises(ValueError):
            AUDIT.protected_receipt(link)

    def test_hardlink_rejected(self) -> None:
        """Avoid trusting multiply linked private receipts."""

        link = self.path.with_name("hardlink.json")
        os.link(self.path, link)
        with self.assertRaises(ValueError):
            self.inspect()

    def test_malformed_json_rejected(self) -> None:
        """Do not accept corrupted owner evidence."""

        self.path.write_text("{invalid", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.inspect()

    def test_enforce_release_fails_even_with_approved_risk(self) -> None:
        """CLI release mode must never treat a risk waiver as a cutover."""

        cmd = [
            sys.executable,
            str(ROOT / "scripts/em50-owner-risk-audit.py"),
            "--risk-receipt",
            str(self.path),
            "--candidate-commit",
            COMMIT,
            "--enforce-release",
            "--jsonl",
        ]
        result = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=10
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["release_authorized"])


if __name__ == "__main__":
    unittest.main()
