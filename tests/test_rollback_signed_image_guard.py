"""Protect verified signed OCI images from source-rollback drift."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rollback-signed-image-guard.py"
SPEC = importlib.util.spec_from_file_location("signed_rollback", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
DIGEST = "sha256:" + "a" * 64
IMAGE = "ghcr.io/isentinel/shelly-voltage-telegram@" + DIGEST


class SignedRollbackTests(unittest.TestCase):
    """Use only private disposable source and updater-state fixtures."""

    def setUp(self) -> None:
        """Create a pinned signed image and matching source snapshot."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.snapshot = self.root / "snapshot"
        self.state = self.root / "state"
        self.snapshot.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.write_versions("0.5.19-hw")
        self.receipt = self.state / "deployed.json"
        self.receipt.write_text(
            json.dumps(
                {
                    "version": "0.5.19-hw",
                    "digest": DIGEST,
                }
            )
        )
        self.receipt.chmod(0o600)
        self.override = self.state / "override.yaml"
        self.override.write_text(
            f"services:\n  shelly-voltage:\n    image: {IMAGE}\n"
        )
        self.override.chmod(0o600)

    def write_versions(self, version: str) -> None:
        """Build a minimal archive without executing extracted files."""
        with tarfile.open(self.snapshot / "source.tar.gz", "w:gz") as out:
            for name in ("VERSION", "RELEASE_VERSION"):
                contents = (version + "\n").encode()
                info = tarfile.TarInfo("./" + name)
                info.size = len(contents)
                info.mode = 0o644
                out.addfile(info, io.BytesIO(contents))

    def outcome(self, image: str = IMAGE) -> str:
        """Exercise guard using a mocked, read-only Docker inspect."""
        result = subprocess.CompletedProcess([], 0, image + "\n", "")
        with patch.object(
            AUDIT.subprocess, "run", return_value=result
        ) as call:
            outcome = AUDIT.check(self.snapshot, self.state)
        call.assert_called_once()
        self.assertEqual(call.call_args.args[0][:2], ["docker", "inspect"])
        return outcome

    def blocked(self, code: str, image: str = IMAGE) -> None:
        """Assert exact refusal code without showing protected state."""
        with self.assertRaises(AUDIT.Blocked) as error:
            self.outcome(image)
        self.assertEqual(str(error.exception), code)

    def test_matching_snapshot_can_use_only_signed_image(self) -> None:
        """A release-aligned snapshot selects immutable no-build restore."""
        self.assertEqual(self.outcome(), "SIGNED")

    def test_old_source_snapshot_fails_before_service_stop(self) -> None:
        """Current old 0.5.15 source cannot roll back signed v0.5.19."""
        self.write_versions("0.5.15-hw")
        self.blocked("SNAPSHOT_SIGNED_VERSION_MISMATCH")

    def test_legacy_without_signed_state_is_still_supported(self) -> None:
        """Unsigned old installations retain their explicit build route."""
        self.receipt.unlink()
        self.override.unlink()
        self.assertEqual(
            self.outcome("shelly-voltage-shelly-voltage"), "LEGACY"
        )

    def test_signed_image_missing_receipt_fails_closed(self) -> None:
        """A missing receipt never authorizes rebuilding signed Docker."""
        self.receipt.unlink()
        self.override.unlink()
        self.blocked("SIGNED_IMAGE_WITHOUT_PROTECTED_RECEIPT")

    def test_different_running_digest_is_blocked(self) -> None:
        """Actual Docker digest must exactly equal updater receipt."""
        self.blocked("SIGNED_IMAGE_DIGEST_MISMATCH", IMAGE[:-1] + "b")

    def test_modified_override_is_blocked(self) -> None:
        """Immutable override mismatch cannot be accepted."""
        self.override.write_text(
            f"services:\n  shelly-voltage:\n    image: {IMAGE[:-1]}b\n"
        )
        self.blocked("SIGNED_IMAGE_DIGEST_MISMATCH")

    def test_pending_update_is_blocked(self) -> None:
        """An interrupted updater makes all source rollback unsafe."""
        (self.state / "pending-update").write_text("unresolved")
        self.blocked("UPDATE_PENDING")

    def test_insecure_state_directory_is_blocked(self) -> None:
        """A non-private signed receipt directory is not trustworthy."""
        self.state.chmod(0o755)
        self.blocked("SIGNED_STATE_DIRECTORY_UNSAFE")

    def test_linked_receipt_is_blocked(self) -> None:
        """Linked state cannot be used to justify a signed restore."""
        target = self.state / "receipt-old.json"
        self.receipt.rename(target)
        self.receipt.symlink_to(target)
        self.blocked("LOCAL_FILE_LINKED")

    def test_source_manifest_mismatch_is_blocked(self) -> None:
        """Both archived release markers must be identical."""
        with tarfile.open(self.snapshot / "source.tar.gz", "w:gz") as out:
            for name, value in (
                ("VERSION", "0.5.19-hw\n"),
                ("RELEASE_VERSION", "0.5.18-hw\n"),
            ):
                encoded = value.encode()
                info = tarfile.TarInfo("./" + name)
                info.size = len(encoded)
                out.addfile(info, io.BytesIO(encoded))
        self.blocked("SNAPSHOT_SIGNED_VERSION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
