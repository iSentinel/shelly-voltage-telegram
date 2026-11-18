"""Disposable fail-closed tests for old signed-image snapshot pairing."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/signed-rollback-pair-preflight.py"
)
SPEC = importlib.util.spec_from_file_location("signed_pair_preflight", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
OLD = "0.5.24-hw"
NEW = "0.5.25-hw"
DIGEST = "sha256:" + "a" * 64


def write_archive(path: Path, version: str) -> None:
    """Write one synthetic source archive with version markers."""

    with tarfile.open(path, "w:gz") as bundle:
        for marker in ("VERSION", "RELEASE_VERSION"):
            content = (version + "\n").encode()
            entry = tarfile.TarInfo("./" + marker)
            entry.size = len(content)
            bundle.addfile(entry, io.BytesIO(content))
    add_checksum(path)


def add_checksum(path: Path) -> None:
    """Generate the exact sidecar format used in production."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(
        f"{digest}  {path}\n", encoding="ascii"
    )


class SignedPairPreflightTests(unittest.TestCase):
    """Test receipt, source, digest, archive and filesystem safety."""

    def setUp(self) -> None:
        """Build two versioned snapshots in a disposable private root."""

        tmp = tempfile.TemporaryDirectory(prefix="svt-signed-pair-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "upgrades"
        self.signed = self.root / "20261118T015250Z-0.5.25-hw"
        self.source.mkdir(mode=0o700)
        self.signed.mkdir(mode=0o700)
        self.manifest = self.source / "manifest.env"
        self.manifest.write_text(f"from_version={OLD}\nto_version={NEW}\n")
        write_archive(self.source / "source.tar.gz", OLD)
        self.receipt = self.signed / "deployed.json"
        self.override = self.signed / "override.yaml"
        self.write_receipt(OLD, DIGEST)
        for folder in (self.source, self.signed):
            archive = folder / "voltage-123.sqlite3.gz"
            archive.write_bytes(b"synthetic-db-archive")
            add_checksum(archive)

    def write_receipt(self, version: str, digest: str) -> None:
        """Set a private signed pair fixture."""

        self.receipt.write_text(
            json.dumps({"version": version, "digest": digest})
        )
        self.override.write_text(
            "services:\n  shelly-voltage:\n"
            f"    image: ghcr.io/isentinel/shelly-voltage-telegram@{DIGEST}\n"
        )
        self.receipt.chmod(0o600)
        self.override.chmod(0o600)

    def blocked(self, reason: str) -> None:
        """Require one stable fail-closed code."""

        with self.assertRaises(MODULE.Blocked) as caught:
            MODULE.check_pair(self.source, self.signed)
        self.assertEqual(str(caught.exception), reason)

    def test_valid_pair_never_claims_signature_or_rollback(self):
        """A matching pair is evidence, not authorization."""

        result = MODULE.check_pair(self.source, self.signed)
        self.assertEqual(result["status"], "PASS_PAIR_IDENTITY_ONLY")
        self.assertEqual(result["source_version"], OLD)
        self.assertFalse(result["signature_verified"])
        self.assertFalse(result["sqlite_restore_verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["rollback_authorized"])

    def test_verified_signature_only_confirms_old_image_identity(self):
        """A trusted signature cannot itself authorize a DB rollback."""

        result = subprocess.CompletedProcess([], 0)
        with patch.object(
            MODULE.subprocess, "run", return_value=result
        ) as call:
            report = MODULE.check_pair(
                self.source, self.signed, verify_signature=True
            )
        self.assertEqual(report["status"], "PASS_PAIR_SIGNED_IDENTITY_ONLY")
        self.assertTrue(report["signature_verified"])
        self.assertFalse(report["sqlite_restore_verified"])
        self.assertFalse(report["release_authorized"])
        self.assertFalse(report["rollback_authorized"])
        args = call.call_args.args[0]
        self.assertEqual(args[:2], ["/usr/local/bin/cosign", "verify"])
        self.assertIn(
            "https://github.com/iSentinel/shelly-voltage-telegram/"
            ".github/workflows/image.yml@refs/tags/v0.5.24-hw",
            args,
        )
        self.assertEqual(args[-1].split("@")[-1], DIGEST)

    def test_invalid_cosign_signature_fails_closed(self):
        """Never label a rejected old signature as a recovery candidate."""

        result = subprocess.CompletedProcess([], 1)
        with (
            patch.object(MODULE.subprocess, "run", return_value=result),
            self.assertRaisesRegex(
                MODULE.Blocked, "OLD_IMAGE_SIGNATURE_UNVERIFIED"
            ),
        ):
            MODULE.check_pair(self.source, self.signed, verify_signature=True)

    def test_cosign_timeout_fails_closed(self):
        """No release endorsement follows a timeout of signature checks."""

        with (
            patch.object(
                MODULE.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["cosign"], 70),
            ),
            self.assertRaisesRegex(
                MODULE.Blocked, "OLD_IMAGE_SIGNATURE_UNAVAILABLE"
            ),
        ):
            MODULE.check_pair(self.source, self.signed, verify_signature=True)

    def test_missing_deployed_receipt(self):
        """No version guess from a source archive is allowed."""

        self.receipt.unlink()
        self.blocked("SNAPSHOT_FILE_MISSING")

    def test_unprotected_deployed_receipt(self):
        """Reject exposed signed receipts."""

        self.receipt.chmod(0o644)
        self.blocked("SIGNED_RECEIPT_PERMISSIONS_UNSAFE")

    def test_unprotected_override(self):
        """The archived Compose digest must be private."""

        self.override.chmod(0o644)
        self.blocked("SIGNED_RECEIPT_PERMISSIONS_UNSAFE")

    def test_receipt_version_drift(self):
        """Reject old source associated with wrong signed release."""

        self.write_receipt("0.5.23-hw", DIGEST)
        self.blocked("SOURCE_SIGNED_VERSION_MISMATCH")

    def test_invalid_digest(self):
        """Reject mutable tags in the receipt."""

        self.write_receipt(OLD, "latest")
        self.blocked("OLD_DIGEST_INVALID")

    def test_wrong_override_digest(self):
        """Never pair an unrelated container digest."""

        self.override.write_text(
            self.override.read_text().replace(DIGEST, "sha256:" + "b" * 64)
        )
        self.blocked("OLD_OVERRIDE_DIGEST_MISMATCH")

    def test_mutable_image_tag(self):
        """Reject mutable image references instead of guessing."""

        self.override.write_text(
            "services:\n  shelly-voltage:\n    image: latest\n"
        )
        self.blocked("OLD_OVERRIDE_DIGEST_MISMATCH")

    def test_archive_version_mismatch(self):
        """Old source tar must agree with manifest."""

        write_archive(self.source / "source.tar.gz", "0.5.23-hw")
        self.blocked("SOURCE_MANIFEST_VERSION_MISMATCH")

    def test_source_manifest_version_mismatch(self):
        """Old manifest must identify actual source contents."""

        self.manifest.write_text(
            "from_version=0.5.23-hw\nto_version=0.5.25-hw\n"
        )
        self.blocked("SOURCE_MANIFEST_VERSION_MISMATCH")

    def test_duplicate_manifest_keys(self):
        """Duplicated keys are always ambiguous."""

        self.manifest.write_text(
            self.manifest.read_text() + "to_version=0.5.26-hw\n"
        )
        self.blocked("UPGRADE_MANIFEST_INVALID")

    def test_signed_snapshot_target_mismatch(self):
        """Signed snapshot name must match source upgrade target."""

        target = self.root / "20261118T015250Z-0.5.26-hw"
        self.signed.rename(target)
        self.signed = target
        self.blocked("SIGNED_SNAPSHOT_TARGET_MISMATCH")

    def test_tampered_source_archive(self):
        """SHA256 sidecar must bind actual source contents."""

        with (self.source / "source.tar.gz").open("ab") as stream:
            stream.write(b"bad")
        self.blocked("SNAPSHOT_CHECKSUM_MISMATCH")

    def test_missing_archive_checksum(self):
        """No source backup is accepted without a checksum."""

        (self.source / "source.tar.gz.sha256").unlink()
        self.blocked("SNAPSHOT_FILE_MISSING")

    def test_corrupt_source_database(self):
        """Check source snapshot SQLite archive checksum."""

        (self.source / "voltage-123.sqlite3.gz").write_bytes(b"bad")
        self.blocked("SNAPSHOT_CHECKSUM_MISMATCH")

    def test_corrupt_signed_database(self):
        """Check signed snapshot SQLite archive checksum."""

        (self.signed / "voltage-123.sqlite3.gz").write_bytes(b"bad")
        self.blocked("SNAPSHOT_CHECKSUM_MISMATCH")

    def test_missing_signed_database(self):
        """Reject an old OCI snapshot without its SQLite backup."""

        (self.signed / "voltage-123.sqlite3.gz").unlink()
        self.blocked("SQLITE_ARCHIVE_COUNT_INVALID")

    def test_ambiguous_database_archive(self):
        """Reject snapshots containing more than one SQLite archive."""

        extra = self.source / "voltage-extra.sqlite3.gz"
        extra.write_bytes(b"second")
        add_checksum(extra)
        self.blocked("SQLITE_ARCHIVE_COUNT_INVALID")

    def test_symlinked_receipt(self):
        """Never follow a linked protected deployment record."""

        destination = self.signed / "original.json"
        self.receipt.rename(destination)
        self.receipt.symlink_to(destination)
        self.blocked("SNAPSHOT_FILE_LINKED")

    def test_hardlinked_receipt(self):
        """Reject a multiply linked protected deployment record."""

        os.link(self.receipt, self.signed / "another.json")
        self.blocked("SNAPSHOT_FILE_UNSAFE")

    def test_world_readable_signed_directory(self):
        """Signed rollback snapshots must remain owner-only."""

        self.signed.chmod(0o755)
        self.blocked("SNAPSHOT_DIRECTORY_UNSAFE")

    def test_symlinked_directory(self):
        """Reject linked snapshot locations before file reads."""

        linked = self.root / "alias"
        linked.symlink_to(self.signed, target_is_directory=True)
        self.signed = linked
        self.blocked("SNAPSHOT_PATH_LINKED_OR_RELATIVE")

    def test_relative_path(self):
        """Do not accept arbitrary relative snapshot locations."""

        with self.assertRaisesRegex(
            MODULE.Blocked, "SNAPSHOT_PATH_LINKED_OR_RELATIVE"
        ):
            MODULE.check_pair(Path("wrong"), self.signed)


if __name__ == "__main__":
    unittest.main()
