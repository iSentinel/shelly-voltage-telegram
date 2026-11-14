"""Disposable source-only recovery exercises: never touch production."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts/source-only-recovery-qa.py"
)
SPEC = importlib.util.spec_from_file_location("source_only_qa", SCRIPT)
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


class SourceOnlyRecoveryTests(unittest.TestCase):
    """Prove the restored source cannot alter SQLite or signed runtime."""

    def setUp(self) -> None:
        """Create an isolated private target with intentionally old source."""
        self.temporary = tempfile.TemporaryDirectory(
            prefix="shelly-source-recovery-qa-", dir="/tmp"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "target"
        self.target.mkdir(mode=0o700)
        (self.target / ".source-only-qa").write_text(RECOVERY.MARKER_VALUE)
        (self.target / "VERSION").write_text("0.5.15-hw\n")
        (self.target / "partial.py").write_text("broken install\n")
        (self.target / ".env").write_text("local settings preserved\n")
        (self.target / "ROADMAP.md").write_text("private checkpoint\n")
        (self.target / "secrets").mkdir(mode=0o700)
        (self.target / "secrets/key").write_text("unchanged\n")
        self.database = self.root / "voltage.sqlite3"
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE measurements (id INTEGER)")
            db.execute("INSERT INTO measurements VALUES (42)")
        self.archive = self.root / "source.tar.gz"
        self.write_archive()
        self.database_digest = self.digest(self.database)

    @staticmethod
    def digest(path: Path) -> str:
        """Return a fingerprint for the SQLite fixture."""
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_archive(self, extras: dict | None = None) -> None:
        """Create a tiny but structurally complete official-style archive."""
        files = {
            "VERSION": "0.5.21-hw\n",
            "RELEASE_VERSION": "0.5.21-hw\n",
            "app.py": "# signed candidate\n",
            "Dockerfile": "FROM scratch\n",
            "compose.yaml": "services: {}\n",
            "requirements.txt": "\n",
            "scripts/backup.sh": "#!/bin/sh\n",
            "systemd/shelly-voltage-compose.service": "[Unit]\n",
            "systemd/shelly-voltage-backup.service": "[Unit]\n",
            ".env.example": "safe example\n",
        }
        files.update(extras or {})
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content in files.items():
                payload = content.encode()
                record = tarfile.TarInfo("./" + name)
                record.size = len(payload)
                record.mode = 0o644
                archive.addfile(record, io.BytesIO(payload))

    def invoke(self, apply: bool = False, database: Path | None = None) -> int:
        """Call only the disposable QA CLI."""
        args = [
            str(SCRIPT),
            "--archive",
            str(self.archive),
            "--target",
            str(self.target),
            "--database",
            str(database or self.database),
        ]
        if apply:
            args.append("--apply-disposable")
        with (
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return RECOVERY.main()

    def assert_database_unchanged(self) -> None:
        """Verify data, not only a file checksum."""
        self.assertEqual(self.database_digest, self.digest(self.database))
        with sqlite3.connect(self.database) as db:
            count = db.execute(
                "SELECT count(*) FROM measurements WHERE id=42"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_dry_run_makes_no_changes(self) -> None:
        """A dry-run verifies the candidate but preserves old source."""
        self.assertEqual(self.invoke(), 0)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assertTrue((self.target / "partial.py").exists())
        self.assert_database_unchanged()

    def test_disposable_restore_replaces_partial_source(self) -> None:
        """Reapply complete source, retain local state and live-like DB."""
        self.assertEqual(self.invoke(apply=True), 0)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.21-hw\n")
        self.assertFalse((self.target / "partial.py").exists())
        self.assertEqual(
            (self.target / ".env").read_text(), "local settings preserved\n"
        )
        self.assertEqual(
            (self.target / "ROADMAP.md").read_text(), "private checkpoint\n"
        )
        self.assertEqual(
            (self.target / "secrets/key").read_text(), "unchanged\n"
        )
        self.assertTrue((self.target / ".env.example").exists())
        self.assert_database_unchanged()

    def test_reapply_is_idempotent(self) -> None:
        """Repeated QA recovery does not duplicate or lose state."""
        self.assertEqual(self.invoke(apply=True), 0)
        self.assertEqual(self.invoke(apply=True), 0)
        self.assert_database_unchanged()

    def test_archive_invalid_version_fails_closed(self) -> None:
        """Reject mismatching source and release versions before writes."""
        self.write_archive({"RELEASE_VERSION": "0.5.15-hw\n"})
        self.assertEqual(self.invoke(apply=True), 3)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assert_database_unchanged()

    def test_archive_with_private_file_fails_closed(self) -> None:
        """A release archive must never overwrite local credentials."""
        self.write_archive({".env": "attack\n"})
        self.assertEqual(self.invoke(apply=True), 3)
        self.assertEqual(
            (self.target / ".env").read_text(), "local settings preserved\n"
        )
        self.assert_database_unchanged()

    def test_missing_marker_fails_closed(self) -> None:
        """No silent operation outside explicitly marked disposable QA."""
        (self.target / ".source-only-qa").unlink()
        self.assertEqual(self.invoke(apply=True), 3)
        self.assert_database_unchanged()

    def test_linked_marker_fails_closed(self) -> None:
        """Do not follow marker aliases."""
        marker = self.target / ".source-only-qa"
        marker.unlink()
        marker.symlink_to(self.target / "VERSION")
        self.assertEqual(self.invoke(apply=True), 3)

    def test_database_inside_target_fails_closed(self) -> None:
        """Do not replace a source directory containing its own database."""
        embedded = self.target / "embedded.sqlite3"
        embedded.write_bytes(self.database.read_bytes())
        self.assertEqual(self.invoke(apply=True, database=embedded), 3)
        self.assert_database_unchanged()

    def test_traversal_target_fails_closed(self) -> None:
        """Block relative escape from the /tmp sandbox namespace."""
        target = Path("/tmp/fake/../../opt/shelly-voltage")
        with self.assertRaises(RECOVERY.Blocked):
            RECOVERY.validate_target(target)

    def test_production_target_fails_closed(self) -> None:
        """Refuse /opt/shelly-voltage regardless of command flags."""
        with self.assertRaises(RECOVERY.Blocked):
            RECOVERY.validate_target(Path("/opt/shelly-voltage"))

    def test_linked_parent_fails_closed(self) -> None:
        """Refuse a sandbox with an aliased parent path."""
        link = self.root / "link"
        link.symlink_to(self.target, target_is_directory=True)
        with self.assertRaises(RECOVERY.Blocked):
            RECOVERY.validate_target(link)

    def test_nested_secret_symlink_fails_closed(self) -> None:
        """Never follow links inside an otherwise private secrets folder."""
        (self.target / "secrets/link").symlink_to(self.database)
        self.assertEqual(self.invoke(apply=True), 3)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assert_database_unchanged()

    def test_archive_inside_target_fails_closed(self) -> None:
        """An archive cannot be destroyed by replacing its own directory."""
        internal = self.target / "source.tar.gz"
        internal.write_bytes(self.archive.read_bytes())
        self.archive = internal
        self.assertEqual(self.invoke(apply=True), 3)
        self.assert_database_unchanged()

    def test_corrupted_archive_fails_closed(self) -> None:
        """Truncated source cannot overwrite partially installed files."""
        data = self.archive.read_bytes()
        self.archive.write_bytes(data[:25])
        self.assertEqual(self.invoke(apply=True), 3)
        self.assertTrue((self.target / "partial.py").exists())
        self.assert_database_unchanged()


if __name__ == "__main__":
    unittest.main()
