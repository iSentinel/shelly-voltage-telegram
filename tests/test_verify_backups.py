"""Test read-only SHA256 and SQLite restoration checks for backups."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts/verify-backups.py"
SPEC = importlib.util.spec_from_file_location("backup_verifier", PATH)
BACKUPS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKUPS)


def create_backup(directory: Path, name: str = "voltage-1") -> Path:
    """Build one small real SQLite backup and its SHA256 sidecar."""

    plain = directory / f"{name}.sqlite3"
    with closing(sqlite3.connect(plain)) as db, db:
        db.execute("CREATE TABLE measurements(voltage REAL)")
        db.execute("INSERT INTO measurements VALUES(226.0)")
    archive = directory / f"{name}.sqlite3.gz"
    archive.write_bytes(gzip.compress(plain.read_bytes()))
    plain.unlink()
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(f"{archive}.sha256").write_text(f"{checksum}  {archive.name}\n")
    return archive


class BackupVerificationTests(unittest.TestCase):
    """Check successful validation and expected failure cases."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def verify(self, count: int = 1, size: int = 16) -> int:
        """Run the CLI against only the isolated fixture directory."""

        return BACKUPS.main(
            [
                "--backup-dir",
                str(self.root),
                "--count",
                str(count),
                "--max-size-mib",
                str(size),
            ]
        )

    def test_valid_backup_passes_without_modification(self):
        """Checking an archive must leave it and the checksum unchanged."""

        archive = create_backup(self.root)
        sidecar = Path(f"{archive}.sha256")
        before = (archive.read_bytes(), sidecar.read_bytes())
        self.assertEqual(self.verify(), 0)
        self.assertEqual((archive.read_bytes(), sidecar.read_bytes()), before)
        self.assertEqual(len(list(self.root.glob("*.sqlite3"))), 0)

    def test_three_distinct_backups_pass(self):
        """A complete three-backup validation has exit code zero."""

        for index in range(3):
            path = create_backup(self.root, f"voltage-{index}")
            os.utime(path, (1000 + index, 1000 + index))
        self.assertEqual(self.verify(count=3), 0)

    def test_missing_checksum_is_failure(self):
        """No archive is accepted without its SHA256 sidecar."""

        path = create_backup(self.root)
        Path(f"{path}.sha256").unlink()
        self.assertEqual(self.verify(), 2)

    def test_checksum_mismatch_is_failure(self):
        """A modified archived file must never be considered a backup."""

        path = create_backup(self.root)
        path.write_bytes(path.read_bytes() + b"tampered")
        self.assertEqual(self.verify(), 2)

    def test_wrong_sidecar_filename_is_failure(self):
        """Reject a matching digest paired with another backup name."""

        archive = create_backup(self.root)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha256").write_text(
            f"{digest}  voltage-other.sqlite3.gz\n"
        )
        self.assertEqual(self.verify(), 2)

    def test_absolute_sidecar_filename_is_accepted(self):
        """Existing sha256sum files may contain their original path."""

        archive = create_backup(self.root)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha256").write_text(f"{digest}  {archive}\n")
        self.assertEqual(self.verify(), 0)

    def test_digest_only_sidecar_remains_accepted(self):
        """Legacy checksum-only files must remain compatible."""

        archive = create_backup(self.root)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha256").write_text(digest + "\n")
        self.assertEqual(self.verify(), 0)

    def test_symlink_sidecar_is_rejected(self):
        """Do not read checksum metadata via a symbolic link."""

        archive = create_backup(self.root)
        sidecar = Path(f"{archive}.sha256")
        real = self.root / "stored-checksum"
        sidecar.rename(real)
        sidecar.symlink_to(real)
        self.assertEqual(self.verify(), 2)

    def test_oversized_sidecar_is_rejected(self):
        """Ignore unexpectedly large checksum metadata files."""

        archive = create_backup(self.root)
        Path(f"{archive}.sha256").write_text("0" * 5000)
        self.assertEqual(self.verify(), 2)

    def test_corrupted_gzip_is_failure(self):
        """Reject malformed compression even with a matching checksum."""

        path = create_backup(self.root)
        path.write_bytes(b"invalid gzip")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        Path(f"{path}.sha256").write_text(checksum + "\n")
        self.assertEqual(self.verify(), 2)

    def test_non_database_is_failure(self):
        """A checksum-matching text archive must fail SQLite restoration."""

        path = create_backup(self.root)
        path.write_bytes(gzip.compress(b"not a SQLite database"))
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        Path(f"{path}.sha256").write_text(checksum + "\n")
        self.assertEqual(self.verify(), 2)

    def test_archive_size_limit_is_enforced(self):
        """Block decompression beyond the configured safety limit."""

        path = create_backup(self.root)
        path.write_bytes(gzip.compress(b"z" * (2 * 1024 * 1024)))
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        Path(f"{path}.sha256").write_text(checksum + "\n")
        self.assertEqual(self.verify(size=1), 2)

    def test_insufficient_backups_is_failure(self):
        """A request for three copies cannot pass when only two exist."""

        create_backup(self.root, "voltage-a")
        create_backup(self.root, "voltage-b")
        self.assertEqual(self.verify(count=3), 2)

    def test_missing_backup_directory_is_failure(self):
        """Unavailable backup storage returns an explicit nonzero status."""

        self.assertEqual(
            BACKUPS.main(
                ["--backup-dir", str(self.root / "missing"), "--count", "1"]
            ),
            2,
        )

    def test_unreadable_directory_reports_access_error(self):
        """A listing permission error must explain why no backups appear."""

        output = io.StringIO()
        with (
            patch.object(BACKUPS.os, "scandir", side_effect=PermissionError),
            patch("sys.stderr", output),
        ):
            result = self.verify()
        self.assertEqual(result, 2)
        self.assertIn("cannot read backup directory", output.getvalue())
        self.assertIn("PermissionError", output.getvalue())

    def test_symbolic_link_archive_is_not_used(self):
        """Do not follow links outside the selected backup directory."""

        real = create_backup(self.root, "voltage-real")
        linked = self.root / "voltage-linked.sqlite3.gz"
        linked.symlink_to(real)
        self.assertEqual(self.verify(count=2), 2)

    def test_invalid_count_is_failure(self):
        """Zero is never interpreted as permission to skip checks."""

        create_backup(self.root)
        self.assertEqual(self.verify(count=0), 2)

    def test_help_is_available(self):
        """All operator tools must support the standard help flag."""

        with self.assertRaises(SystemExit) as raised:
            BACKUPS.parse_args(["--help"])
        self.assertEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
