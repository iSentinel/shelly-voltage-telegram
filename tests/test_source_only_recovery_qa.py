"""Disposable source-only recovery exercises: never touch production."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
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


def abrupt_child_code() -> str:
    """Return the QA-only Python subprocess crash harness."""
    return """
import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

script, archive, target, database = map(Path, sys.argv[1:5])
spec = importlib.util.spec_from_file_location("crash_qa", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = Path.rename

def crash_once(source, destination):
    result = original(source, destination)
    if source == target:
        os._exit(77)
    return result

with patch.object(Path, "rename", crash_once):
    sys.argv = [
        str(script), "--archive", str(archive),
        "--target", str(target), "--database", str(database),
        "--apply-disposable",
    ]
    module.main()
"""


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
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE measurements (id INTEGER)")
            db.execute("INSERT INTO measurements VALUES (42)")
            db.commit()
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

    def invoke(
        self,
        apply: bool = False,
        database: Path | None = None,
        recover: bool = False,
    ) -> int:
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
        if recover:
            args.append("--recover-disposable")
        with (
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return RECOVERY.main()

    def assert_database_unchanged(self) -> None:
        """Verify data, not only a file checksum."""
        self.assertEqual(self.database_digest, self.digest(self.database))
        with contextlib.closing(sqlite3.connect(self.database)) as db:
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

    def test_concurrent_apply_is_rejected(self) -> None:
        """An already active QA lock blocks a competing recovery."""
        lock_path = self.root / ".source-only-qa.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.invoke(apply=True), 3)
        finally:
            os.close(descriptor)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assert_database_unchanged()

    def test_staging_failure_restores_old_source(self) -> None:
        """A failed stage rename leaves original source and SQLite intact."""
        original = Path.rename

        def interrupt(stage: Path, destination: Path) -> Path:
            if stage.name.startswith(".source-stage-"):
                raise OSError("simulated interrupted stage promotion")
            return original(stage, destination)

        with patch.object(
            Path, "rename", autospec=True, side_effect=interrupt
        ):
            self.assertEqual(self.invoke(apply=True), 3)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.invoke(recover=True), 0)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assertTrue((self.target / "partial.py").exists())
        self.assert_database_unchanged()

    def test_hard_crash_mid_swap_can_recover(self) -> None:
        """Survive KeyboardInterrupt between source directory renames."""
        original = Path.rename

        def kill_after_first(stage: Path, destination: Path) -> Path:
            if stage.name.startswith(".source-stage-"):
                raise KeyboardInterrupt("simulated process death")
            return original(stage, destination)

        with (
            patch.object(
                Path, "rename", autospec=True, side_effect=kill_after_first
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.invoke(apply=True)
        self.assertFalse(self.target.exists())
        self.assert_database_unchanged()
        self.assertEqual(self.invoke(recover=True), 0)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assertEqual(
            (self.target / "secrets/key").read_text(), "unchanged\n"
        )
        self.assert_database_unchanged()

    def test_crash_after_promote_requires_review(self) -> None:
        """Never silently delete old source after an ambiguous crash."""
        original = RECOVERY.cleanup_stage

        def crash_before_cleanup(path: Path) -> None:
            if path.name.startswith(".source-old-"):
                raise KeyboardInterrupt("simulated post-promote crash")
            original(path)

        with (
            patch.object(
                RECOVERY, "cleanup_stage", side_effect=crash_before_cleanup
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.invoke(apply=True)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.21-hw\n")
        self.assertEqual(self.invoke(recover=True), 3)
        self.assert_database_unchanged()

    def test_malicious_journal_path_is_rejected(self) -> None:
        """Refuse path traversal in untrusted transaction metadata."""
        path = RECOVERY.journal_path(self.target)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "stage": "../../etc",
                    "old": ".source-old-fake1234",
                }
            )
        )
        path.chmod(0o600)
        self.assertEqual(self.invoke(recover=True), 3)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assert_database_unchanged()

    def test_child_process_hard_exit_recovery(self) -> None:
        """A real subprocess exit after the first rename retains source."""
        code = abrupt_child_code()
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(SCRIPT),
                str(self.archive),
                str(self.target),
                str(self.database),
            ],
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertFalse(self.target.exists())
        self.assertTrue(RECOVERY.journal_path(self.target).exists())
        self.assertEqual(self.invoke(recover=True), 0)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assertEqual(
            (self.target / "secrets/key").read_text(), "unchanged\n"
        )
        self.assert_database_unchanged()

    def test_corrupt_journal_json_fails_closed(self) -> None:
        """Invalid crash metadata cannot influence restored paths."""
        journal = RECOVERY.journal_path(self.target)
        journal.write_text("{not json")
        journal.chmod(0o600)
        self.assertEqual(self.invoke(recover=True), 3)
        self.assertEqual((self.target / "VERSION").read_text(), "0.5.15-hw\n")
        self.assert_database_unchanged()

    def test_wrong_mode_journal_fails_closed(self) -> None:
        """An exposed journal must not be trusted as a recovery record."""
        journal = RECOVERY.journal_path(self.target)
        journal.write_text("{}")
        journal.chmod(0o644)
        self.assertEqual(self.invoke(recover=True), 3)
        self.assert_database_unchanged()

    def test_missing_old_backup_fails_closed(self) -> None:
        """An incomplete crash journal must never wipe a valid target."""
        journal = RECOVERY.journal_path(self.target)
        journal.write_text(
            json.dumps(
                {
                    "version": 1,
                    "stage": ".source-stage-abc12345",
                    "old": ".source-old-abc12345",
                }
            )
        )
        journal.chmod(0o600)
        self.target.rename(self.root / "target-held")
        self.assertEqual(self.invoke(recover=True), 3)
        self.assertEqual(
            (self.root / "target-held" / "VERSION").read_text(),
            "0.5.15-hw\n",
        )
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
