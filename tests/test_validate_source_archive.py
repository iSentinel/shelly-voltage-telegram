"""Validate complete source recovery archives before Docker changes."""

from __future__ import annotations

import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate-source-archive.py"
REQUIRED = (
    "VERSION",
    "app.py",
    "Dockerfile",
    "compose.yaml",
    "requirements.txt",
    "scripts/backup.sh",
    "systemd/shelly-voltage-compose.service",
    "systemd/shelly-voltage-backup.service",
)


def create_archive(path: Path, missing: str = "") -> None:
    """Write a small disposable gzip TAR with optional missing component."""

    with tarfile.open(path, "w:gz") as archive:
        for name in REQUIRED:
            if name == missing:
                continue
            data = f"valid fixture for {name}\n".encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))


def check_archive(path: Path) -> subprocess.CompletedProcess[str]:
    """Invoke public archive validation without touching production."""

    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )


class SourceArchiveValidationTests(unittest.TestCase):
    """Test restorable TAR and full gzip checksum verification."""

    def test_complete_source_accepted(self) -> None:
        """All nonempty rollback essentials must pass."""

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.tar.gz"
            create_archive(path)
            self.assertEqual(check_archive(path).returncode, 0)

    def test_missing_application_rejected(self) -> None:
        """Cannot proceed with a source snapshot missing runtime files."""

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.tar.gz"
            create_archive(path, missing="app.py")
            self.assertEqual(check_archive(path).returncode, 3)

    def test_invalid_gzip_trailer_rejected(self) -> None:
        """A valid TAR header is insufficient if gzip CRC is damaged."""

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.tar.gz"
            create_archive(path)
            raw = path.read_bytes()
            path.write_bytes(raw[:-8] + b"\x00" * 8)
            self.assertEqual(check_archive(path).returncode, 3)


if __name__ == "__main__":
    unittest.main()
