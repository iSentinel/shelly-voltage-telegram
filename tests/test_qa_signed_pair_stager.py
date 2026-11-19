"""Check that QA receipt files appear only after exact pair proof."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/qa-stage-verified-pair-receipt.py"
)
SPEC = importlib.util.spec_from_file_location("qa_pair_stager", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)
VERSION = "0.5.26-hw"
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


class QaPairReceiptStagerTests(unittest.TestCase):
    """Use unsigned bytes and explicitly mocked crypto only in this suite."""

    def setUp(self) -> None:
        """Make three source fixtures, never using live release files."""
        temp = tempfile.TemporaryDirectory(prefix="svt-qa-pair-stage-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.root / "source"
        source.mkdir()
        name = f"shelly-voltage-telegram-{VERSION}.tar.gz"
        archive = source / name
        archive.write_bytes(b"unsigned synthetic QA source bytes")
        bundle = source / (name + ".sigstore.json")
        bundle.write_text('{"mock":true}')
        sums = source / "SHA256SUMS"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sums.write_text(f"{digest}  {name}\n")
        self.args = argparse.Namespace(
            version=VERSION,
            commit=COMMIT,
            digest=DIGEST,
            archive=archive,
            bundle=bundle,
            sha256sums=sums,
            qa_stage=True,
            dry_run=False,
            jsonl=False,
        )
        self.output = self.root / "private"

    def evidence(self) -> dict:
        """Model successful independent signature evidence in unit tests."""
        return {
            "status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
            "version": VERSION,
            "commit": COMMIT,
            "digest_suffix": DIGEST[-12:],
            "source_signature_verified": True,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
            "release_authorized": False,
            "rollback_authorized": False,
        }

    def test_complete_pair_publishes_private_receipt_last(self) -> None:
        """The verifier sees private bytes and no preexisting receipt."""

        def evidence(copied):
            self.assertEqual(copied.archive.parent, self.output)
            self.assertFalse((self.output / "deployed.json").exists())
            return self.evidence()

        with patch.object(STAGE.PAIR, "verify", side_effect=evidence) as proof:
            result = STAGE.stage_release(self.args, self.output)
        proof.assert_called_once()
        self.assertEqual(result["status"], "PASS_QA_PAIR_RECEIPT_STAGED")
        receipt = json.loads((self.output / "deployed.json").read_text())
        self.assertEqual(receipt["source_commit"], COMMIT)
        self.assertEqual(receipt["digest"], DIGEST)
        self.assertEqual(
            receipt["source_sha256"],
            hashlib.sha256(self.args.archive.read_bytes()).hexdigest(),
        )
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for entry in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o600)
            self.assertEqual(entry.stat().st_nlink, 1)

    def test_incomplete_signature_proof_never_creates_receipt(self) -> None:
        """Bogus status or missing proof flags must leave output pending."""
        for number, changed in enumerate(
            (
                {"status": "PASS_QA_IDENTITY_AND_WAL_ONLY"},
                {"source_signature_verified": 1},
                {"oci_attestations_verified": False},
                {"commit": "f" * 40},
                {"release_authorized": True},
                {"digest_suffix": "wrong"},
            )
        ):
            output = self.root / f"blocked-{number}"
            with (
                self.subTest(changed=changed),
                patch.object(
                    STAGE.PAIR,
                    "verify",
                    return_value=dict(self.evidence(), **changed),
                ),
                self.assertRaisesRegex(
                    STAGE.StageBlocked, "STAGE_PAIR_EVIDENCE_INVALID"
                ),
            ):
                STAGE.stage_release(self.args, output)
            self.assertFalse((output / "deployed.json").exists())

    def test_real_verifier_error_leaves_no_boot_receipt(self) -> None:
        """Missing signatures must not be replaced by an unsigned receipt."""
        with (
            patch.object(
                STAGE.PAIR,
                "verify",
                side_effect=ValueError("unsigned source rejected"),
            ),
            self.assertRaisesRegex(ValueError, "unsigned source rejected"),
        ):
            STAGE.stage_release(self.args, self.output)
        self.assertFalse((self.output / "deployed.json").exists())

    def test_reentry_does_not_overwrite_approved_pair(self) -> None:
        """Do not silently replace any existing private release evidence."""
        with patch.object(
            STAGE.PAIR, "verify", return_value=self.evidence()
        ) as verify:
            STAGE.stage_release(self.args, self.output)
            before = (self.output / "deployed.json").read_bytes()
            with self.assertRaisesRegex(
                STAGE.StageBlocked, "STAGE_EXISTING_OUTPUT_REFUSED"
            ):
                STAGE.stage_release(self.args, self.output)
        verify.assert_called_once()
        self.assertEqual((self.output / "deployed.json").read_bytes(), before)

    def test_bad_name_rejected_before_any_mutation(self) -> None:
        """Reject wrong version before creating even a private directory."""
        self.args.version = "0.5.27-hw"
        with self.assertRaisesRegex(
            STAGE.StageBlocked, "STAGE_ARCHIVE_NAME_INVALID"
        ):
            STAGE.stage_release(self.args, self.output)
        self.assertFalse(self.output.exists())

    def test_linked_source_is_rejected(self) -> None:
        """Do not copy a symbolic link into authoritative release evidence."""
        source = self.args.archive
        target = self.root / "outside"
        target.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(target)
        with self.assertRaisesRegex(STAGE.StageBlocked, "STAGE_INPUT_UNSAFE"):
            STAGE.stage_release(self.args, self.output)
        self.assertFalse((self.output / "deployed.json").exists())

    def test_qa_only_host_guard(self) -> None:
        """Never stage on the Proxmox host or in a production guest."""
        with (
            patch.object(STAGE.os, "geteuid", return_value=0),
            patch.object(STAGE.socket, "gethostname", return_value="proxmox"),
            self.assertRaisesRegex(
                STAGE.StageBlocked, "STAGE_DISPOSABLE_LXC114_ONLY"
            ),
        ):
            STAGE.require_isolated_root()

    def test_dry_run_does_not_write_any_receipt(self) -> None:
        """Read-only proof is not permission to create an installed record."""
        self.args.dry_run = True
        self.args.qa_stage = False
        with (
            patch.object(STAGE, "arguments", return_value=self.args),
            patch.object(STAGE.PAIR, "verify_inputs"),
            patch.object(
                STAGE.PAIR, "verify", return_value=self.evidence()
            ) as proof,
        ):
            self.assertEqual(STAGE.main(), 0)
        proof.assert_called_once()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
