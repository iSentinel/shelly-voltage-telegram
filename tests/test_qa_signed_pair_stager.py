"""Check that QA receipt files appear only after exact pair proof."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import signal
import stat
import subprocess
import sys
import tempfile
import time
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
REAL_RESCUE_PREFLIGHT = STAGE.verify_qa_rescue
VERSION = "0.5.26-hw"
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


CHILD_SOURCE = """
import argparse, importlib.util, sys, time
from pathlib import Path
script, folder, out, marker = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("isolated_qa_stager", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
name = "shelly-voltage-telegram-0.5.26-hw.tar.gz"
args = argparse.Namespace(
    version="0.5.26-hw", commit="a" * 40, digest="sha256:" + "b" * 64,
    archive=folder / name, bundle=folder / (name + ".sigstore.json"),
    sha256sums=folder / "SHA256SUMS", token="qa-subprocess-token")
def paused_verifier(_):
    marker.write_text("copied")
    time.sleep(60)
module.PAIR.verify = paused_verifier
module.stage_release(args, out)
"""


class QaPairReceiptStagerTests(unittest.TestCase):
    """Use unsigned bytes and explicitly mocked crypto only in this suite."""

    def setUp(self) -> None:
        """Make three source fixtures, never using live release files."""
        preflight = patch.object(STAGE, "verify_qa_rescue")
        preflight.start()
        self.addCleanup(preflight.stop)
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
            token="qa-test-verified-pair-20261119",
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

    def test_lost_pending_after_signature_cannot_publish_receipt(self) -> None:
        """A removed marker after proof must block final receipt creation."""
        with (
            patch.object(
                STAGE.PAIR, "verify", return_value=self.evidence()
            ) as verify,
            patch.object(
                STAGE,
                "verify_qa_rescue",
                side_effect=STAGE.StageBlocked(
                    "SVT_QA_PENDING_TOKEN_MISMATCH"
                ),
            ) as pending,
            self.assertRaisesRegex(
                STAGE.StageBlocked, "SVT_QA_PENDING_TOKEN_MISMATCH"
            ),
        ):
            STAGE.stage_release(self.args, self.output)
        verify.assert_called_once()
        pending.assert_called_once()
        self.assertFalse((self.output / "deployed.json").exists())

    def test_signature_is_checked_before_pending_reconfirmation(self) -> None:
        """The final pending check follows crypto and precedes receipt."""
        calls = []

        def crypto(_):
            calls.append("signed_pair")
            return self.evidence()

        def pending(_):
            calls.append("rescue_pending")

        with (
            patch.object(STAGE.PAIR, "verify", side_effect=crypto),
            patch.object(STAGE, "verify_qa_rescue", side_effect=pending),
        ):
            STAGE.stage_release(self.args, self.output)
        self.assertEqual(calls, ["signed_pair", "rescue_pending"])
        self.assertTrue((self.output / "deployed.json").exists())

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

    def test_sigkill_before_final_receipt_keeps_stage_unapproved(self) -> None:
        """An updater crash after copying assets cannot publish approval."""
        marker = self.root / "verifier-entered"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                CHILD_SOURCE,
                str(SCRIPT),
                str(self.args.archive.parent),
                str(self.output),
                str(marker),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(marker.exists(), "QA verifier was never entered")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        self.assertTrue((self.output / self.args.archive.name).exists())
        self.assertFalse((self.output / "deployed.json").exists())
        with self.assertRaisesRegex(
            STAGE.StageBlocked, "STAGE_EXISTING_OUTPUT_REFUSED"
        ):
            STAGE.stage_release(self.args, self.output)

    def test_receipt_binds_same_pending_transaction_token(self) -> None:
        """The staged release receipt must name the guarded SQLite update."""
        with patch.object(STAGE.PAIR, "verify", return_value=self.evidence()):
            STAGE.stage_release(self.args, self.output)
        receipt = json.loads((self.output / "deployed.json").read_text())
        self.assertEqual(receipt["transaction_token"], self.args.token)

    def test_invalid_or_missing_transaction_token_is_refused(self) -> None:
        """Never commit a source pair without its pending rescue token."""
        for token in ("", "other-transaction", "qa-bad/slash"):
            with self.subTest(token=token):
                self.args.token = token
                with self.assertRaisesRegex(
                    STAGE.StageBlocked, "STAGE_TRANSACTION_TOKEN_INVALID"
                ):
                    REAL_RESCUE_PREFLIGHT(self.args)

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
