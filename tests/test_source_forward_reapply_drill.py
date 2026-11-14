"""Rehearse partial-source recovery without a writer, Docker, or Telegram."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"
ARCHIVER = ROOT / "scripts/source-tar.sh"


class SourceForwardReapplyTests(unittest.TestCase):
    """Use disposable paths only; the production updater is never invoked."""

    def setUp(self) -> None:
        """Build a partial source with external SQLite and backup state."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.candidate = self.home / "signed-source"
        self.target = self.home / "installed-source"
        self.candidate.mkdir()
        self.target.mkdir()
        (self.candidate / "scripts").mkdir()
        (self.candidate / "secrets").mkdir()
        (self.target / "secrets").mkdir()
        shutil.copy2(ARCHIVER, self.candidate / "scripts/source-tar.sh")
        self.write_source("0.5.21-hw", "new-code\n")
        self.write_installed("0.5.15-hw", "old-code\n")
        self.db = self.home / "live.sqlite3"
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(
                "CREATE TABLE measurements(voltage REAL);"
                "INSERT INTO measurements VALUES(217.8);"
            )
        self.backup = self.home / "older-B-backup"
        self.backup.mkdir(mode=0o700)
        (self.backup / "retained").write_text("keep-forensics\n")
        self.before = self.fingerprint()

    def write_source(self, version: str, code: str) -> None:
        """Create candidates with untrusted secrets that must stay excluded."""
        for marker in ("VERSION", "RELEASE_VERSION"):
            (self.candidate / marker).write_text(version + "\n")
        (self.candidate / "app.py").write_text(code)
        (self.candidate / ".env").write_text("DO_NOT_COPY_NEW_ENV\n")
        (self.candidate / "secrets/telegram_token").write_text("DO_NOT_COPY")

    def write_installed(self, version: str, code: str) -> None:
        """Keep pre-existing secrets and local checkpoint outside the tar."""
        for marker in ("VERSION", "RELEASE_VERSION"):
            (self.target / marker).write_text(version + "\n")
        (self.target / "app.py").write_text(code)
        (self.target / ".env").write_text("KEEP_PRIVATE_ENV\n")
        (self.target / "secrets/telegram_token").write_text("KEEP_TOKEN")
        (self.target / "ROADMAP.md").write_text("KEEP_ROADMAP\n")

    def fingerprint(self) -> str:
        """Read bytes, never modify the SQLite database under test."""
        return hashlib.sha256(self.db.read_bytes()).hexdigest()

    def apply_isolated_source_copy(self) -> subprocess.CompletedProcess[str]:
        """Invoke only install.sh copy_source with disposable target paths."""
        fragment = self.home / "install-functions.sh"
        text = INSTALLER.read_text()
        self.assertTrue(text.rstrip().endswith('main "$@"'))
        fragment.write_text(text.rsplit('main "$@"', 1)[0])
        code = (
            'source "$1"; SOURCE="$2"; TARGET="$3"; DRY_RUN=false; copy_source'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                code,
                "bash",
                str(fragment),
                str(self.candidate),
                str(self.target),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def assert_data_preserved(self) -> None:
        """Preserve credentials, backups and every SQLite byte."""
        self.assertEqual(self.fingerprint(), self.before)
        self.assertEqual(
            (self.target / ".env").read_text(), "KEEP_PRIVATE_ENV\n"
        )
        self.assertEqual(
            (self.target / "secrets/telegram_token").read_text(), "KEEP_TOKEN"
        )
        self.assertEqual(
            (self.backup / "retained").read_text(), "keep-forensics\n"
        )
        self.assertEqual(
            (self.target / "ROADMAP.md").read_text(), "KEEP_ROADMAP\n"
        )

    def test_interrupted_copy_forward_reapplies_without_db_changes(
        self,
    ) -> None:
        """Reapply matching source after an interrupted copy."""
        (self.target / "VERSION").write_text("0.5.21-hw\n")
        (self.target / "app.py").write_text("partial-")
        result = self.apply_isolated_source_copy()
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in ("VERSION", "RELEASE_VERSION"):
            self.assertEqual((self.target / marker).read_text(), "0.5.21-hw\n")
        self.assertEqual((self.target / "app.py").read_text(), "new-code\n")
        self.assert_data_preserved()

    def test_source_reapply_is_idempotent_on_disposable_files(self) -> None:
        """Repeated source replay cannot destroy retained local state."""
        for _ in range(2):
            result = self.apply_isolated_source_copy()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assert_data_preserved()
        self.assertEqual((self.target / "app.py").read_text(), "new-code\n")

    def test_hardlinked_source_is_rejected_before_recovery(self) -> None:
        """A shared source inode cannot be silently replayed."""
        twin = self.candidate / "app-copy.py"
        twin.hardlink_to(self.candidate / "app.py")
        result = self.apply_isolated_source_copy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hard-linked", result.stderr)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assert_data_preserved()

    def test_installer_copy_step_never_builds_or_restores_sqlite(self) -> None:
        """Keep this drill limited to the source tar copy operation."""
        code = INSTALLER.read_text().split("copy_source() {", 1)[1]
        code = code.split("\nprepare_state() {", 1)[0]
        self.assertIn("source-tar.sh", code)
        self.assertNotIn("docker", code)
        self.assertNotIn("sqlite3", code)
        self.assertNotIn("--build", code)


if __name__ == "__main__":
    unittest.main()
