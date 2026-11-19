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
OLD_SHA = "c" * 40


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

    def replace_archive_with_entry(
        self, entry: tarfile.TarInfo, data: bytes | None = None
    ) -> None:
        """Add one adversarial TAR member to a self-checksummed fixture."""
        archive = self.source / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for marker in ("VERSION", "RELEASE_VERSION"):
                content = (OLD + "\n").encode("ascii")
                info = tarfile.TarInfo("./" + marker)
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))
            if data is not None:
                entry.size = len(data)
            bundle.addfile(
                entry, io.BytesIO(data) if data is not None else None
            )
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
                self.source,
                self.signed,
                verify_signature=True,
                expected_commit=OLD_SHA,
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

    def test_old_oci_signature_requires_exact_source_commit(self):
        """A versioned rollback tag alone is not immutable source proof."""
        with (
            patch.object(MODULE.subprocess, "run") as command,
            self.assertRaisesRegex(
                MODULE.Blocked, "OLD_IMAGE_CERT_COMMIT_REQUIRED"
            ),
        ):
            MODULE.check_pair(self.source, self.signed, verify_signature=True)
        command.assert_not_called()

    def test_old_oci_cert_claims_are_bound_to_rollback_git_sha(self):
        """The actual Cosign command includes exact SHA, ref and repo."""
        with patch.object(
            MODULE.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0),
        ) as execute:
            MODULE.verify_old_signature(OLD, DIGEST, OLD_SHA)
        command = execute.call_args.args[0]
        expected = {
            "--certificate-github-workflow-sha": OLD_SHA,
            "--certificate-github-workflow-ref": "refs/tags/v" + OLD,
            "--certificate-github-workflow-repository": (
                "iSentinel/shelly-voltage-telegram"
            ),
        }
        for flag, value in expected.items():
            with self.subTest(flag=flag):
                self.assertIn(flag, command)
                self.assertEqual(command[command.index(flag) + 1], value)
        self.assertEqual(command[-1], MODULE.IMAGE + DIGEST)

    def test_invalid_cosign_signature_fails_closed(self):
        """Never label a rejected old signature as a recovery candidate."""

        result = subprocess.CompletedProcess([], 1)
        with (
            patch.object(MODULE.subprocess, "run", return_value=result),
            self.assertRaisesRegex(
                MODULE.Blocked, "OLD_IMAGE_SIGNATURE_UNVERIFIED"
            ),
        ):
            MODULE.check_pair(
                self.source,
                self.signed,
                verify_signature=True,
                expected_commit=OLD_SHA,
            )

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
            MODULE.check_pair(
                self.source,
                self.signed,
                verify_signature=True,
                expected_commit=OLD_SHA,
            )

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

    def test_source_tar_traversal_rejected(self):
        """Signed pair identity cannot accept a parent-directory entry."""
        self.replace_archive_with_entry(
            tarfile.TarInfo("../outside.txt"), b"disposable"
        )
        self.blocked("SOURCE_ARCHIVE_MEMBER_UNSAFE")

    def test_source_tar_absolute_path_rejected(self):
        """Archive identity check refuses absolute member paths."""
        self.replace_archive_with_entry(
            tarfile.TarInfo("/etc/disposable.txt"), b"disposable"
        )
        self.blocked("SOURCE_ARCHIVE_MEMBER_UNSAFE")

    def test_source_tar_symlink_rejected(self):
        """A TAR symlink is invalid even when SHA256 sidecar agrees."""
        entry = tarfile.TarInfo("./linked")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "/etc/hostname"
        self.replace_archive_with_entry(entry)
        self.blocked("SOURCE_ARCHIVE_MEMBER_UNSAFE")

    def test_source_tar_duplicate_canonical_path_rejected(self):
        """VERSION and ./VERSION cannot bypass duplicate-path checks."""
        self.replace_archive_with_entry(
            tarfile.TarInfo("VERSION"), (OLD + "\n").encode("ascii")
        )
        self.blocked("SOURCE_ARCHIVE_MEMBER_DUPLICATE")

    def test_source_tar_oversized_member_header_refused(self):
        """Oversized members are blocked before tarfile extraction."""
        entry = tarfile.TarInfo("./large")
        entry.size = MODULE.MAX_SOURCE_MEMBER_BYTES + 1
        with self.assertRaisesRegex(
            MODULE.Blocked, "SOURCE_ARCHIVE_MEMBER_UNSAFE"
        ):
            MODULE.safe_source_member(entry, set(), 1, entry.size)

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
