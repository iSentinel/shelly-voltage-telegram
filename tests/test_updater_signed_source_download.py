"""Enforce a signed source blob before any legacy updater activation."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/apply-update.sh"
VERSION = "0.5.26-hw"
ARCHIVE_NAME = f"shelly-voltage-telegram-{VERSION}.tar.gz"
SHELL = """
source "$1"
ROOT="$2"
VERSION="0.5.26-hw"
curl() {
    local url="" output=""
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == "-o" ]]; then
            output="$2"
            shift 2
            continue
        fi
        case "$1" in
            https://*) url="$1" ;;
        esac
        shift
    done
    cp "$FIXTURE_ASSETS/$(basename "$url")" "$output"
}
verify_source_archive "$3"
"""


class UpdaterSourceDownloadTests(unittest.TestCase):
    """Use a local fake curl; never download or activate a release."""

    def setUp(self) -> None:
        """Create an unsigned source fixture and an isolated workspace."""
        temp = tempfile.TemporaryDirectory(prefix="svt-qa-download-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.assets = self.root / "assets"
        self.download = self.root / "download"
        self.assets.mkdir()
        self.download.mkdir()
        self.archive = self.assets / ARCHIVE_NAME
        source = {
            "VERSION",
            "RELEASE_VERSION",
            "app.py",
            "writer_fence.py",
            "Dockerfile",
            "compose.yaml",
            "scripts/backup.sh",
            "scripts/verify-source-release-bundle.py",
            "scripts/verify-signed-release-pair.py",
        }
        with tarfile.open(self.archive, "w:gz") as bundle:
            for name in sorted(source):
                value = (
                    VERSION if name in {"VERSION", "RELEASE_VERSION"} else name
                )
                content = (value + "\n").encode()
                item = tarfile.TarInfo(
                    f"shelly-voltage-telegram-{VERSION}/{name}"
                )
                item.size = len(content)
                bundle.addfile(item, io.BytesIO(content))
        self.signature = self.assets / (ARCHIVE_NAME + ".sigstore.json")
        self.signature.write_text('{"unsigned":true}\n')
        self.write_checksum()

    def write_checksum(self) -> None:
        """Write a self-consistent but unauthenticated public checksum."""
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        (self.assets / "SHA256SUMS").write_text(f"{digest}  {ARCHIVE_NAME}\n")

    def invoke(self) -> subprocess.CompletedProcess[str]:
        """Source only the download verifier, not updater main."""
        return subprocess.run(
            [
                "bash",
                "-c",
                SHELL,
                "bash",
                str(SCRIPT),
                str(ROOT),
                str(self.download),
            ],
            env={**os.environ, "FIXTURE_ASSETS": str(self.assets)},
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )

    def test_unsigned_source_is_not_admitted_by_sha256_only(self) -> None:
        """The real verifier must reject a synthetic unsigned bundle."""
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SIGNED SOURCE: BLOCKED", result.stdout)
        self.assertTrue(
            (self.download / (ARCHIVE_NAME + ".sigstore.json")).is_file()
        )
        self.assertTrue((self.download / "SHA256SUMS").is_file())
        self.assertTrue((self.download / ARCHIVE_NAME).is_file())

    def test_missing_bundle_blocks_while_downloading(self) -> None:
        """No source signature is not an implicit checksum-only fallback."""
        self.signature.unlink()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SIGNED SOURCE: PASS", result.stdout)
        self.assertFalse(
            (self.download / (ARCHIVE_NAME + ".sigstore.json")).exists()
        )

    def test_tampered_archive_blocks_before_signature(self) -> None:
        """A modified tarball cannot pass through a signed update."""
        with self.archive.open("ab") as output:
            output.write(b"TAMPER")
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SOURCE_ARCHIVE_SHA256_MISMATCH", result.stdout)

    def test_wrong_version_archive_is_not_trusted(self) -> None:
        """An old version must not inherit a new version's bundle."""
        renamed = self.assets / "old.tar.gz"
        self.archive.rename(renamed)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SIGNED SOURCE: PASS", result.stdout)
        self.assertFalse((self.download / ARCHIVE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
