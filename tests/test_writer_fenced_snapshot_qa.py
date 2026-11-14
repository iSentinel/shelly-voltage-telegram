"""Isolated exact A/B snapshot QA: never inspect the live Shelly writer."""

from __future__ import annotations

import contextlib
import fcntl
import gzip
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
    Path(__file__).resolve().parents[1]
    / "scripts/writer-fenced-snapshot-qa.py"
)
SPEC = importlib.util.spec_from_file_location("writer_fenced_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class WriterFencedSnapshotTests(unittest.TestCase):
    """Verify exact snapshots and refusal to capture active writer data."""

    def setUp(self) -> None:
        """Create a private, marker-protected scratch source and SQLite."""
        self.tmp = tempfile.TemporaryDirectory(
            prefix="shelly-fenced-snapshot-", dir="/tmp"
        )
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / QA.MARKER).write_text(QA.MAGIC)
        descriptor = os.open(
            self.root / ".writer.lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        os.close(descriptor)
        self.source = self.root / "source"
        self.source.mkdir()
        self.make_source_fixture()
        self.db = self.root / "history.sqlite3"
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA user_version=5")
            conn.execute("PRAGMA application_id=123")
            conn.execute(
                "CREATE TABLE measurements (id INTEGER, voltage REAL)"
            )
            conn.execute("INSERT INTO measurements VALUES (1, 221.5)")
            conn.execute("CREATE TABLE outages (id INTEGER, ended_at REAL)")
            conn.execute("INSERT INTO outages VALUES (30, NULL)")
            conn.commit()
        self.digest = QA.db_digest(self.db)

    def make_source_fixture(self) -> None:
        """Populate a private source tree with release essentials."""
        entries = {
            "VERSION": "0.5.21-hw\n",
            "RELEASE_VERSION": "0.5.21-hw\n",
            "app.py": "print('safe qa')\n",
            "Dockerfile": "FROM scratch\n",
            "compose.yaml": "services: {}\n",
            "requirements.txt": "loguru\n",
            "scripts/backup.sh": "#!/bin/bash\n",
            "systemd/shelly-voltage-compose.service": "[Unit]\n",
            "systemd/shelly-voltage-backup.service": "[Unit]\n",
        }
        for name, value in entries.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        (self.source / ".env").write_text("SENSITIVE=TEST_ONLY\n")
        (self.source / "secrets").mkdir()
        (self.source / "secrets/key").write_text("QA_TEST_SECRET\n")

    def invoke(self, capture: bool = False) -> int:
        """Run the exact public QA CLI with only the private scratch root."""
        args = [str(SCRIPT), "--root", str(self.root)]
        args.append("--capture-disposable" if capture else "--dry-run")
        with (
            patch.object(sys, "argv", args),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_dry_run_never_creates_snapshot(self) -> None:
        """Verify the dry run is a no-write planning operation."""
        self.assertEqual(self.invoke(), 0)
        self.assertFalse((self.root / "snapshots").exists())
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_exact_ab_pair(self) -> None:
        """Both archives must represent the same full source and SQLite."""
        self.assertEqual(self.invoke(capture=True), 0)
        snapshots = self.root / "snapshots"
        manifest = json.loads((snapshots / "manifest.json").read_text())
        self.assertEqual(manifest["source_version"], "0.5.21-hw")
        self.assertEqual(manifest["whole_sqlite_digest"], self.digest)
        self.assertEqual(manifest["fence_proof"], "COOPERATIVE_QA_LOCK_ONLY")
        self.assertFalse(manifest["production_release_authorized"])
        for label in ("A", "B"):
            with gzip.open(
                snapshots / label / "history.sqlite3.gz", "rb"
            ) as zipped:
                restored = self.root / f"restored-{label}.sqlite3"
                restored.write_bytes(zipped.read())
            self.assertEqual(QA.db_digest(restored), self.digest)
            with tarfile.open(
                snapshots / label / "source.tar.gz", "r:gz"
            ) as tar:
                names = set(tar.getnames())
                self.assertIn("./VERSION", names)
                self.assertNotIn("./.env", names)
                self.assertNotIn("./secrets/key", names)
        self.assertEqual(
            QA.sha256(snapshots / "A/source.tar.gz"),
            QA.sha256(snapshots / "B/source.tar.gz"),
        )
        for label in ("A", "B"):
            path = snapshots / label / "history.sqlite3.gz"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                manifest["archive_sha256"][label], QA.sha256(path)
            )
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_writer_lock_blocks_before_archive(self) -> None:
        """A cooperative active writer must make the snapshot fail closed."""
        descriptor = os.open(self.root / ".writer.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.invoke(capture=True), 3)
        finally:
            os.close(descriptor)
        self.assertFalse((self.root / "snapshots").exists())
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_repeated_snapshot_fails_closed(self) -> None:
        """Never replace a previously validated snapshot pair."""
        self.assertEqual(self.invoke(capture=True), 0)
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_source_version_mismatch_refused(self) -> None:
        """The source and release marker must match before capture."""
        (self.source / "RELEASE_VERSION").write_text("0.5.15-hw\n")
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_root_outside_private_scratch_refused(self) -> None:
        """Production source root can never pass QA path validation."""
        with self.assertRaises(QA.Blocked):
            QA.safe_root(Path("/opt/shelly-voltage"))

    def test_linked_source_refused(self) -> None:
        """Refuse symlink substitution before snapshot creation."""
        moved = self.root / "source-real"
        self.source.rename(moved)
        self.source.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_dirty_source_fails_parity(self) -> None:
        """A source changed between snapshots must invalidate the pair."""
        original = QA.archive_source
        calls = [0]

        def changing(source: Path, destination: Path) -> None:
            original(source, destination)
            calls[0] += 1
            if calls[0] == 1:
                (self.source / "app.py").write_text("mutated\n")

        with patch.object(QA, "archive_source", side_effect=changing):
            self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_changed_database_fails_parity(self) -> None:
        """A SQLite writer ignoring the lock cannot create false PASS."""
        original = QA.copy_sqlite
        calls = [0]

        def changing(source: Path, destination: Path) -> None:
            original(source, destination)
            calls[0] += 1
            if calls[0] == 1:
                with contextlib.closing(sqlite3.connect(self.db)) as conn:
                    conn.execute("INSERT INTO measurements VALUES (2, 222.0)")
                    conn.commit()

        with patch.object(QA, "copy_sqlite", side_effect=changing):
            self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_missing_fence_refused(self) -> None:
        """Missing writer state cannot be treated as a stopped writer."""
        (self.root / ".writer.lock").unlink()
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_linked_fence_refused(self) -> None:
        """A forged lock symlink may never satisfy the writer fence."""
        lock = self.root / ".writer.lock"
        lock.unlink()
        lock.symlink_to(self.db)
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_exposed_fence_refused(self) -> None:
        """A world-readable fence does not prove the private QA state."""
        (self.root / ".writer.lock").chmod(0o644)
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_no_marker_refused(self) -> None:
        """Never infer sandbox authorization from its name alone."""
        (self.root / QA.MARKER).unlink()
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())

    def test_linked_destination_refused(self) -> None:
        """Never replace a linked output path with an archive pair."""
        (self.root / "snapshots").symlink_to(
            self.source, target_is_directory=True
        )
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_other_process_holding_fence_blocks_capture(self) -> None:
        """Exercise cooperative lock contention across real processes."""
        child_code = (
            "import fcntl,os,sys;"
            "fd=os.open(sys.argv[1],os.O_RDWR);"
            "fcntl.flock(fd,fcntl.LOCK_EX);"
            "print('LOCKED',flush=True);"
            "sys.stdin.read(1);os.close(fd)"
        )
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_code,
                str(self.root / ".writer.lock"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "LOCKED")
            self.assertEqual(self.invoke(capture=True), 3)
            self.assertFalse((self.root / "snapshots").exists())
        finally:
            child.communicate(input="x", timeout=6)
        self.assertEqual(child.returncode, 0)
        self.assertEqual(self.invoke(capture=True), 0)
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_interrupted_second_snapshot_never_publishes(self) -> None:
        """A failed B copy cannot publish an incomplete A/B pair."""
        original = QA.copy_sqlite
        calls = [0]

        def interrupt(source: Path, destination: Path) -> None:
            calls[0] += 1
            if calls[0] == 2:
                raise OSError("injected second-copy failure")
            original(source, destination)

        with patch.object(QA, "copy_sqlite", side_effect=interrupt):
            self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_incomplete_source_archive_cannot_publish(self) -> None:
        """Never snapshot source lacking an essential runtime component."""
        (self.source / "app.py").unlink()
        self.assertEqual(self.invoke(capture=True), 3)
        self.assertFalse((self.root / "snapshots").exists())
        self.assertEqual(QA.db_digest(self.db), self.digest)

    def test_sqlite_restore_expansion_is_bounded(self) -> None:
        """Refuse oversized compressed SQLite fixtures before parity claim."""
        archive = self.root / "oversize.sqlite3.gz"
        with gzip.open(archive, "wb") as stream:
            stream.write(b"x" * 4096)
        restored = self.root / "oversize-restored.sqlite3"
        with (
            patch.object(QA, "MAX_SQLITE_EXPANDED", 1024),
            self.assertRaises(QA.Blocked),
        ):
            QA.restore_bounded(archive, restored)
        self.assertFalse((self.root / "snapshots").exists())

    def test_db_wal_contents_included(self) -> None:
        """WAL-backed measurements must appear in both restored copies."""
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("INSERT INTO measurements VALUES (2, 230.0)")
            conn.commit()
        expected = QA.db_digest(self.db)
        self.assertEqual(self.invoke(capture=True), 0)
        with gzip.open(
            self.root / "snapshots/B/history.sqlite3.gz", "rb"
        ) as backup:
            restored = self.root / "restored.sqlite3"
            restored.write_bytes(backup.read())
        self.assertEqual(QA.db_digest(restored), expected)


if __name__ == "__main__":
    unittest.main()
