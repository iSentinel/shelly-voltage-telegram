"""Release tar file modes must be stable across umask and source copies."""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OMIT = shutil.ignore_patterns(
    ".git",
    ".ruff_cache",
    "__pycache__",
    "dist",
    "dist-audit",
    "dist-publish",
    "validation",
    ".work-state",
)
VERSION = (ROOT / "VERSION").read_text(encoding="ascii").strip()
ARCHIVE = f"shelly-voltage-telegram-{VERSION}.tar.gz"


class ReleaseModePortabilityTests(unittest.TestCase):
    """Use two private source trees, never the host deployment paths."""

    def build(self, source: Path, output: Path, mode: int) -> Path:
        """Build one public archive with an explicit child umask."""
        result = subprocess.run(
            [
                "bash",
                str(source / "scripts/release.sh"),
                "--output-dir",
                str(output),
                "--source-date-epoch",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            umask=mode,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return output / ARCHIVE

    def assert_modes(self, archive: Path) -> None:
        """Verify normalized file and directory permissions inside tar."""
        with tarfile.open(archive, "r:gz") as bundle:
            entries = {m.name.rstrip("/"): m for m in bundle}
            base = f"shelly-voltage-telegram-{VERSION}"
            self.assertEqual(entries[base + "/README.md"].mode, 0o644)
            self.assertEqual(entries[base + "/FILES.SHA256"].mode, 0o644)
            self.assertEqual(entries[base + "/scripts/release.sh"].mode, 0o755)
            self.assertEqual(entries[base + "/docs"].mode, 0o755)

    def test_group_writable_checkout_cannot_change_archive(self) -> None:
        """Source tree and umask changes cannot alter signed-source bytes."""
        with tempfile.TemporaryDirectory(prefix="svt-modes-qa-") as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            shutil.copytree(ROOT, first, ignore=OMIT)
            shutil.copytree(first, second)
            (second / "README.md").chmod(0o664)
            (second / "docs").chmod(0o775)
            (second / "scripts/release.sh").chmod(0o775)
            a = self.build(first, root / "out-a", 0o022)
            b = self.build(second, root / "out-b", 0o002)
            self.assertEqual(a.read_bytes(), b.read_bytes())
            self.assert_modes(a)


if __name__ == "__main__":
    unittest.main()
