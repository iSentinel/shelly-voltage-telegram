"""Test read-only validation before rollback may stop a running service."""

from __future__ import annotations

import gzip
import io
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
CHECKER = PROJECT / "scripts/validate-rollback-snapshot.py"
ROLLBACK = PROJECT / "scripts/rollback.sh"


def make_source(
    path: Path, private_name: str | None = None, link: bool = False
) -> None:
    """Write a tiny source tar with optional forbidden content."""

    members = {"./VERSION": b"0.5.12-hw\n", "./.env.example": b"# empty\n"}
    if private_name:
        members[private_name] = b"fake test credential"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
        if link:
            redirect = tarfile.TarInfo("./redirect")
            redirect.type = tarfile.SYMTYPE
            redirect.linkname = "/etc/passwd"
            archive.addfile(redirect)


def make_database(path: Path, extra_bytes: int = 0) -> bytes:
    """Create a disposable SQLite database with a single voltage reading."""

    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE measurements(voltage REAL NOT NULL)")
        db.execute("INSERT INTO measurements VALUES (220.0)")
        if extra_bytes:
            db.execute("CREATE TABLE padding(data BLOB)")
            db.execute("INSERT INTO padding VALUES (?)", (bytes(extra_bytes),))
    return path.read_bytes()


class RollbackSnapshotValidationTests(unittest.TestCase):
    """Protect the rollback path before a container is stopped."""

    def setUp(self):
        """Create a real but disposable source and SQLite snapshot."""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.snapshot = self.directory / "snapshot"
        self.snapshot.mkdir()
        make_source(self.snapshot / "source.tar.gz")
        database = make_database(self.directory / "source.sqlite3")
        self.archive = self.snapshot / "voltage-test.sqlite3.gz"
        self.archive.write_bytes(gzip.compress(database))

    def check(self, *args: str) -> subprocess.CompletedProcess:
        """Run the read-only checker without revealing any file contents."""

        return subprocess.run(
            [
                "python3",
                str(CHECKER),
                "--snapshot",
                str(self.snapshot),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_valid_snapshot_passes(self):
        """A readable SQLite backup is sufficient for a safe dry run."""

        result = self.check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SNAPSHOT CONTENT PASS", result.stdout)

    def test_corrupted_sqlite_fails(self):
        """A valid gzip wrapper must not hide malformed SQLite data."""

        self.archive.write_bytes(gzip.compress(b"broken SQLite payload"))
        self.assertEqual(self.check().returncode, 2)

    def test_bad_gzip_fails(self):
        """A truncated database archive must be rejected."""

        self.archive.write_bytes(b"not a gzip stream")
        self.assertEqual(self.check().returncode, 2)

    def test_legacy_env_backup_is_safe_to_ignore_during_rollback(self):
        """Historical env backups are excluded during extraction, not restored."""

        make_source(self.snapshot / "source.tar.gz", "./.env.bak")
        self.assertEqual(self.check().returncode, 0)

    def test_traversal_source_archive_fails(self):
        """Refuse a path outside the intended recovery directory."""

        make_source(self.snapshot / "source.tar.gz", "../escape")
        self.assertEqual(self.check().returncode, 2)

    def test_link_source_archive_fails(self):
        """A link to an external file is never a valid code snapshot."""

        make_source(self.snapshot / "source.tar.gz", link=True)
        self.assertEqual(self.check().returncode, 2)

    def test_bounded_restore_rejects_large_uncompressed_database(self):
        """The configured size limit protects the temporary filesystem."""

        large = make_database(
            self.directory / "large.sqlite3", 2 * 1024 * 1024
        )
        self.archive.write_bytes(gzip.compress(large))
        self.assertEqual(self.check("--max-size-mib", "1").returncode, 2)

    def test_missing_or_ambiguous_database_fails(self):
        """The rollback directory must identify one unambiguous archive."""

        self.assertEqual(self.check("--max-size-mib", "0").returncode, 2)
        (self.snapshot / "voltage-other.sqlite3.gz").write_bytes(b"bad")
        self.assertEqual(self.check().returncode, 2)

    def test_help(self):
        """The helper must provide standard command-line help."""

        result = subprocess.run(
            ["python3", str(CHECKER), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--snapshot", result.stdout)


if __name__ == "__main__":
    unittest.main()
