"""Exercise a WAL-safe synthetic SQLite backup under hostile env inputs."""

from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "scripts/backup.sh"
UPDATER = ROOT / "scripts/apply-update.sh"
UNIT = ROOT / "systemd/shelly-voltage-backup.service"


class BackupProtectedRuntimeTests(unittest.TestCase):
    """Back up a disposable database only, never production SQLite."""

    def setUp(self) -> None:
        """Create an isolated DB, owner-only output and hostile startup."""
        temp = tempfile.TemporaryDirectory(prefix="qa-backup-env-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "fixture.sqlite3"
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE sample (value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('fixture')")
        self.dest = self.root / "backups"
        self.dest.mkdir(mode=0o700)
        self.bash_marker = self.root / "bash-injected"
        self.python_marker = self.root / "python-injected"
        bash_poison = self.root / "bash-env"
        bash_poison.write_text('printf "bad" > "$QA_BASH_MARKER"\n')
        python_dir = self.root / "python-poison"
        python_dir.mkdir()
        (python_dir / "sqlite3.py").write_text(
            'import os\nfrom pathlib import Path\n'
            'Path(os.environ["QA_PYTHON_MARKER"]).write_text("bad")\n'
        )
        self.env = {
            **os.environ,
            "DATABASE_PATH": str(self.db),
            "BACKUP_DIR": str(self.dest),
            "BACKUP_KEEP": "1",
            "BASH_ENV": str(bash_poison),
            "PYTHONPATH": str(python_dir),
            "QA_BASH_MARKER": str(self.bash_marker),
            "QA_PYTHON_MARKER": str(self.python_marker),
        }

    def invoke(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Launch a documented help or scratch-only backup process."""
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=15,
        )

    def test_backup_and_unit_use_protected_shebang(self) -> None:
        """The systemd direct executable must suppress BASH_ENV."""
        self.assertEqual(BACKUP.read_text().splitlines()[0], "#!/bin/bash -p")
        unit = UNIT.read_text()
        self.assertIn("ExecStart=/opt/shelly-voltage/scripts/backup.sh", unit)
        result = self.invoke([str(BACKUP), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.bash_marker.exists())

    def test_updater_snapshot_handoff_is_privileged(self) -> None:
        """The update snapshot must not force a plain Bash interpreter."""
        self.assertIn(
            '/bin/bash -p "$ROOT/scripts/backup.sh"',
            UPDATER.read_text(),
        )

    def test_backup_inline_python_is_isolated(self) -> None:
        """Never import local or PYTHONPATH-overridden sqlite3."""
        self.assertIn(
            '/usr/bin/python3 -I - "$DB" "$TMP"',
            BACKUP.read_text(),
        )

    def test_real_backup_of_disposable_sqlite_passes(self) -> None:
        """Verify archive, checksum and independent SQLite restore."""
        result = self.invoke([str(BACKUP)])
        self.assertEqual(result.returncode, 0, result.stderr)
        archives = list(self.dest.glob("voltage-*.sqlite3.gz"))
        self.assertEqual(len(archives), 1)
        archive = archives[0]
        checksum = Path(f"{archive}.sha256")
        self.assertTrue(checksum.is_file())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(checksum.read_text().split()[0], digest)
        restored = self.root / "restored.sqlite3"
        restored.write_bytes(gzip.decompress(archive.read_bytes()))
        with sqlite3.connect(restored) as connection:
            self.assertEqual(
                connection.execute("PRAGMA quick_check").fetchone()[0],
                "ok",
            )
            self.assertEqual(
                connection.execute("SELECT value FROM sample").fetchone()[0],
                "fixture",
            )
        self.assertFalse(self.bash_marker.exists())
        self.assertFalse(self.python_marker.exists())

    def test_unisolated_python_import_negative_control(self) -> None:
        """Show why Python -I is needed before writing a snapshot."""
        result = self.invoke(["/usr/bin/python3", "-c", "import sqlite3"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.python_marker.exists())
        self.python_marker.unlink()
        result = self.invoke(
            ["/usr/bin/python3", "-I", "-c", "import sqlite3"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.python_marker.exists())

    def test_plain_manual_bash_remains_unsafe(self) -> None:
        """A forced unprotected shell still ignores the safe shebang."""
        result = self.invoke(["/bin/bash", str(BACKUP), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.bash_marker.exists())


if __name__ == "__main__":
    unittest.main()
