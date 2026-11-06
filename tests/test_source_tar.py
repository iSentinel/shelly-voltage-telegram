"""Check that source archives never carry local credentials."""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/source-tar.sh"


def make_fixture(root: Path) -> None:
    """Write expected source files and files containing sample secrets."""

    files = {
        ".env": "TELEGRAM_TOKEN=private",
        ".env.pre-ip-fix-20261104": "TELEGRAM_TOKEN=private",
        ".env.bak": "TELEGRAM_TOKEN=private",
        ".env.example": "TELEGRAM_TOKEN=",
        "secrets/telegram_token": "secret",
        ".git/config": "secret",
        "nested/.env.hidden": "secret",
        "nested/README.md": "safe",
        "__pycache__/generated.pyc": "private",
        "scripts/run.py": "print(123)",
        "validation/private.json": "hidden",
        "dist/private.tar.gz": "hidden",
        "dist-audit/private.json": "hidden",
        "dist-publish/private.tar.gz": "hidden",
        ".ruff_cache/analysis": "hidden",
        "nested/local.sqlite3": "hidden",
        "nested/debug.log": "hidden",
        "ROADMAP.md.pre-0.5.3": "hidden",
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


class SourceTarTests(unittest.TestCase):
    """Validate the actual helper used by installer and rollback snapshots."""

    def test_help_and_missing_source(self):
        """Offer CLI help and reject missing source directories."""

        help_result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("Usage:", help_result.stdout)
        missing = subprocess.run(
            ["bash", str(SCRIPT), "/not-a-source-directory"],
            check=False,
            capture_output=True,
        )
        self.assertEqual(missing.returncode, 2)

    def test_excludes_local_artifacts_and_symlinks(self):
        """Keep private QA data, generated files and links out of sources."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_fixture(root)
            (root / "linked-secret").symlink_to(root / ".env")
            (root / "linked-folder").symlink_to(
                root / "secrets", target_is_directory=True
            )
            result = subprocess.run(
                ["bash", str(SCRIPT), str(root)],
                check=True,
                capture_output=True,
            )
            with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
                names = set(archive.getnames())
                for value in (
                    "./linked-secret",
                    "./linked-folder",
                    "./validation/private.json",
                    "./dist/private.tar.gz",
                    "./dist-audit/private.json",
                    "./dist-publish/private.tar.gz",
                    "./.ruff_cache/analysis",
                    "./nested/local.sqlite3",
                    "./nested/debug.log",
                    "./ROADMAP.md.pre-0.5.3",
                ):
                    self.assertNotIn(value, names)
                self.assertIn("./scripts/run.py", names)

    def test_excludes_env_backups_and_preserves_template(self):
        """Omit credentials even when their names resemble local backups."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            make_fixture(root)

            proc = subprocess.run(
                ["bash", str(SCRIPT), str(root)],
                check=True,
                capture_output=True,
            )
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
                names = set(archive.getnames())
                for permitted in (
                    "./.env.example",
                    "./scripts/run.py",
                    "./nested/README.md",
                ):
                    self.assertIn(permitted, names)
                for forbidden in (
                    "./.env",
                    "./.env.bak",
                    "./.env.pre-ip-fix-20261104",
                    "./nested/.env.hidden",
                    "./secrets/telegram_token",
                    "./.git/config",
                    "./__pycache__/generated.pyc",
                ):
                    self.assertNotIn(forbidden, names)
                template = archive.extractfile("./.env.example")
                self.assertIsNotNone(template)
                self.assertEqual(template.read(), b"TELEGRAM_TOKEN=")


if __name__ == "__main__":
    unittest.main()
