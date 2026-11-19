"""Fail-closed signature and source provenance tests without registry calls."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "scripts/verify-source-release-bundle.py"
)
SPEC = importlib.util.spec_from_file_location("svt_signed_source", SOURCE)
assert SPEC is not None and SPEC.loader is not None
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
VERSION = "0.5.26-hw"


class SignedSourceBundleTests(unittest.TestCase):
    """Only an exact source version and verified Cosign bundle may pass."""

    def setUp(self) -> None:
        """Create a tiny unsigned synthetic release with correct metadata."""
        folder = tempfile.TemporaryDirectory(prefix="svt-qa-source-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        name = f"shelly-voltage-telegram-{VERSION}.tar.gz"
        self.archive = self.root / name
        self.bundle = self.root / (name + ".sigstore.json")
        self.sums = self.root / "SHA256SUMS"
        self.bundle.write_text('{"test":"synthetic-unsigned"}\n')
        self.files = {
            key: (VERSION + "\n").encode()
            if key in {"VERSION", "RELEASE_VERSION"}
            else b"QA fixture\n"
            for key in CHECK.REQUIRED
        }
        self.make_archive()

    def make_archive(self, extra: tuple[str, bytes] | None = None) -> None:
        """Create release bytes and matching untrusted SHA256 sidecar."""
        prefix = f"shelly-voltage-telegram-{VERSION}"
        with tarfile.open(self.archive, "w:gz") as tar:
            entries = list(self.files.items())
            if extra is not None:
                entries.append(extra)
            for name, body in entries:
                member = tarfile.TarInfo(f"{prefix}/{name}")
                member.size = len(body)
                tar.addfile(member, io.BytesIO(body))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.sums.write_text(f"{digest}  {self.archive.name}\n")

    def args(self):
        """Provide explicit checked filenames without a production default."""
        return type(
            "Args",
            (),
            {
                "version": VERSION,
                "archive": self.archive,
                "bundle": self.bundle,
                "sha256sums": self.sums,
            },
        )()

    def test_signed_source_evidence_never_authorizes_deploy(self) -> None:
        """Even an accepted signature is not approval to update."""
        with patch.object(CHECK, "verify_signature") as signature:
            result = CHECK.verify(self.args())
        signature.assert_called_once()
        self.assertEqual(result["status"], "PASS_SIGNED_SOURCE_BYTES_ONLY")
        self.assertTrue(result["signed_source_verified"])
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["production_modified"])

    def test_missing_bundle_blocks_before_signature(self) -> None:
        """SHA256SUMS by itself cannot authenticate release source bytes."""
        self.bundle.unlink()
        with (
            patch.object(CHECK, "verify_signature") as signature,
            self.assertRaises(OSError),
        ):
            CHECK.verify(self.args())
        signature.assert_not_called()

    def test_modified_archive_fails_even_if_bundle_exists(self) -> None:
        """A changed archive cannot inherit an older source signature."""
        with self.archive.open("ab") as output:
            output.write(b"tamper")
        with self.assertRaisesRegex(
            CHECK.SourceBlocked, "SOURCE_ARCHIVE_SHA256_MISMATCH"
        ):
            CHECK.verify(self.args())

    def test_missing_writer_fence_in_source_is_not_complete(self) -> None:
        """A published rollback source must contain the writer fence."""
        del self.files["writer_fence.py"]
        self.make_archive()
        with self.assertRaisesRegex(
            CHECK.SourceBlocked, "SOURCE_ARCHIVE_INCOMPLETE"
        ):
            CHECK.verify(self.args())

    def test_mismatched_source_version_refused(self) -> None:
        """The signed tarball must match both release version markers."""
        self.files["VERSION"] = b"0.5.25-hw\n"
        self.make_archive()
        with self.assertRaisesRegex(
            CHECK.SourceBlocked, "SOURCE_ARCHIVE_VERSION_MISMATCH"
        ):
            CHECK.verify(self.args())

    def test_wrong_asset_path_refused(self) -> None:
        """The source verifier must use one exact release archive name."""
        args = self.args()
        args.archive = self.archive.parent / "different.tar.gz"
        with self.assertRaisesRegex(
            CHECK.SourceBlocked, "SOURCE_RELEASE_ASSET_MISMATCH"
        ):
            CHECK.verify(args)

    def test_archive_traversal_and_noncanonical_paths_refused(self) -> None:
        """Reject paths that could collide or escape after extraction."""
        for member in ("nested/../app.py", "nested//app.py"):
            with self.subTest(member=member):
                self.make_archive((member, b"extra"))
                with self.assertRaisesRegex(
                    CHECK.SourceBlocked, "SOURCE_ARCHIVE_MEMBER_INVALID"
                ):
                    CHECK.verify(self.args())

    def test_private_archive_entry_refused(self) -> None:
        """Release signatures cannot excuse an accidentally bundled secret."""
        for member in (".env", "secrets/token", "ROADMAP.md"):
            with self.subTest(member=member):
                self.make_archive((member, b"secret"))
                with self.assertRaisesRegex(
                    CHECK.SourceBlocked, "SOURCE_ARCHIVE_PRIVATE_MEMBER"
                ):
                    CHECK.verify(self.args())

    def test_duplicate_normalized_member_refused(self) -> None:
        """Do not allow two entries to target the same rollback file."""
        self.make_archive(("VERSION", (VERSION + "\\n").encode()))
        with self.assertRaisesRegex(
            CHECK.SourceBlocked, "SOURCE_ARCHIVE_MEMBER_INVALID"
        ):
            CHECK.verify(self.args())

    def test_symlinked_bundle_refused(self) -> None:
        """Do not follow a private source signature outside its directory."""
        target = self.root / "replacement"
        target.write_text("fake bundle")
        self.bundle.unlink()
        self.bundle.symlink_to(target)
        with self.assertRaisesRegex(CHECK.SourceBlocked, "SOURCE_FILE_UNSAFE"):
            CHECK.verify(self.args())

    def test_ambiguous_checksum_is_not_accepted(self) -> None:
        """A duplicated selected checksum must not be treated as valid."""
        line = self.sums.read_text()
        self.sums.write_text(line + line)
        with self.assertRaisesRegex(
            CHECK.SourceBlocked, "SOURCE_SHA256_ENTRY_INVALID"
        ):
            CHECK.verify(self.args())

    def test_signature_command_requires_exact_tag_and_tlog(self) -> None:
        """Verify the immutable source using a pinned release workflow."""
        with (
            patch.object(
                CHECK, "trusted_cosign", return_value="/usr/local/bin/cosign"
            ),
            patch.object(
                CHECK.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0),
            ) as command,
        ):
            CHECK.verify_signature(self.args())
        invocation = command.call_args.args[0]
        self.assertIn("verify-blob", invocation)
        self.assertIn(str(self.bundle), invocation)
        self.assertIn(str(self.archive), invocation)
        self.assertIn(
            "https://github.com/iSentinel/shelly-voltage-telegram/"
            ".github/workflows/release.yml@refs/tags/v0.5.26-hw",
            invocation,
        )
        self.assertIn(CHECK.ISSUER, invocation)
        self.assertNotIn("--insecure-ignore-tlog", invocation)

    def test_invalid_signature_is_blocking(self) -> None:
        """A rejected bundle must not silently degrade to SHA256-only."""
        with (
            patch.object(
                CHECK, "trusted_cosign", return_value="/usr/local/bin/cosign"
            ),
            patch.object(
                CHECK.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 3),
            ),
            self.assertRaisesRegex(
                CHECK.SourceBlocked, "SOURCE_BLOB_SIGNATURE_UNVERIFIED"
            ),
        ):
            CHECK.verify_signature(self.args())

    def test_missing_cosign_is_not_pass(self) -> None:
        """An absent trusted verifier cannot validate synthetic bundles."""
        with (
            patch.object(CHECK.Path, "lstat", side_effect=FileNotFoundError()),
            self.assertRaises(OSError),
        ):
            CHECK.trusted_cosign()


if __name__ == "__main__":
    unittest.main()
