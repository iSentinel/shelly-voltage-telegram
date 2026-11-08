"""Test guarded predeployment snapshots without changing live Shelly data."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_em50_predeploy_backup_guard import make_db

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_snapshot", ROOT / "scripts/em50-predeploy-snapshot.py"
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class SnapshotWindowTests(unittest.TestCase):
    """Require explicit approval and full state parity before publication."""

    def setUp(self) -> None:
        """Create a private synthetic database and destination."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.database = self.root / "live.sqlite3"
        self.output = self.root / "snapshots"
        self.output.mkdir(mode=0o700)
        make_db(self.database)

    def archives(self) -> list[Path]:
        """Return completed archives, excluding manifests."""

        return list(self.output.glob("voltage-*.sqlite3.gz"))

    def test_complete_snapshot_passes_full_parity_and_sha256(self):
        """Publish one private, versioned, nonoverwriting online backup."""

        original = self.database.read_bytes()
        result = QA.create_snapshot(self.database, self.output)
        self.assertIn("release_authorized=false", result)
        self.assertEqual(self.database.read_bytes(), original)
        archives = self.archives()
        self.assertEqual(len(archives), 1)
        archive = archives[0]
        manifest = Path(str(archive) + ".sha256")
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
        guard = QA.load_parity_guard()
        report = guard.inspect(
            SimpleNamespace(
                db=str(self.database),
                backup_dir=str(self.output),
                max_age_minutes=30,
            ),
            time.time(),
        )
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["release_authorized"])
        self.assertNotIn("synthetic", json.dumps(report))

    def test_cancelling_without_confirmation_writes_nothing(self):
        """Do not create backups merely because a user ran the tool."""

        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=SimpleNamespace(
                    dry_run=False,
                    capture=True,
                    db=str(self.database),
                    output_dir=str(self.output),
                    confirm="",
                ),
            ),
            contextlib.redirect_stderr(io.StringIO()) as error,
        ):
            self.assertEqual(QA.main(), 1)
        self.assertIn("FAIL predeploy_snapshot", error.getvalue())
        self.assertEqual(list(self.output.iterdir()), [])

    def test_dry_run_does_not_touch_database_or_directory(self):
        """Inspect path protection only; do not create a SQLite snapshot."""

        before = self.database.read_bytes()
        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=SimpleNamespace(
                    dry_run=True,
                    capture=False,
                    db=str(self.database),
                    output_dir=str(self.output),
                    confirm="",
                ),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
            patch.object(QA, "online_copy") as backup,
        ):
            self.assertEqual(QA.main(), 0)
        self.assertIn("no_writes=true", output.getvalue())
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(list(self.output.iterdir()), [])
        backup.assert_not_called()

    def test_source_advance_during_capture_rejects_and_cleans(self):
        """Do not leave an archive if the current database gained a row."""

        guard = QA.load_parity_guard()
        normal = guard.sqlite_state
        calls = 0

        def advancing(path: Path) -> dict:
            """Simulate a new physical measurement before comparing."""

            nonlocal calls
            calls += 1
            if calls == 2:
                with contextlib.closing(sqlite3.connect(self.database)) as db:
                    db.execute(
                        "INSERT INTO measurements VALUES (?,?,?,?,?)",
                        (4, 4.0, 215.0, 50.0, "L1"),
                    )
                    db.commit()
            return normal(path)

        guard.sqlite_state = advancing
        with (
            patch.object(QA, "load_parity_guard", return_value=guard),
            self.assertRaisesRegex(ValueError, "advanced"),
        ):
            QA.create_snapshot(self.database, self.output)
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(guard.sqlite_state(self.database)["rows"], 4)

    def test_source_advance_during_compression_rejects(self):
        """Check complete history again after gzip creation."""

        guard = QA.load_parity_guard()
        real = QA.gzip_copy

        def advancing(source: Path, destination: Path) -> str:
            """Write one synthetic sample while gzip is executing."""

            digest = real(source, destination)
            with contextlib.closing(sqlite3.connect(self.database)) as db:
                db.execute(
                    "INSERT INTO measurements VALUES (?,?,?,?,?)",
                    (4, 4.0, 217.0, 50.0, "L1"),
                )
                db.commit()
            return digest

        with (
            patch.object(QA, "load_parity_guard", return_value=guard),
            patch.object(QA, "gzip_copy", side_effect=advancing),
            self.assertRaisesRegex(ValueError, "compression"),
        ):
            QA.create_snapshot(self.database, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_source_symlink_and_hardlink_rejected(self):
        """Do not trust a symbolic or multiply linked source file."""

        link = self.root / "linked.sqlite3"
        link.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, "symlink"):
            QA.require_source(link)
        link.unlink()
        os.link(self.database, link)
        with self.assertRaisesRegex(ValueError, "ordinary"):
            QA.require_source(self.database)

    def test_public_or_symlink_destination_rejected(self):
        """Only an owner-only direct directory is permitted."""

        self.output.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "owner-only"):
            QA.require_directory(self.output)
        self.output.chmod(0o700)
        alias = self.root / "linked"
        alias.symlink_to(self.output, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            QA.require_directory(alias)

    def test_existing_snapshot_name_is_never_overwritten(self):
        """Explicit publish cannot clobber a previous versioned backup."""

        src = self.root / "compressed.gz"
        src.write_bytes(b"new")
        dst = self.output / "voltage-20261108-120000-000000.sqlite3.gz"
        dst.write_bytes(b"original")
        with self.assertRaisesRegex(ValueError, "already exists"):
            QA.publish(src, dst, "a" * 64)
        self.assertEqual(dst.read_bytes(), b"original")

    def test_invalid_or_missing_status_makes_capture_fail(self):
        """Telegram status metadata must be part of snapshot parity."""

        with contextlib.closing(sqlite3.connect(self.database)) as db:
            db.execute("DELETE FROM meta")
            db.commit()
        with self.assertRaisesRegex(ValueError, "advanced"):
            QA.create_snapshot(self.database, self.output)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_two_captures_use_unique_filenames(self):
        """Keep distinct snapshots; never overwrite or prune an old one."""

        QA.create_snapshot(self.database, self.output)
        QA.create_snapshot(self.database, self.output)
        archives = self.archives()
        self.assertEqual(len(archives), 2)
        self.assertNotEqual(archives[0].name, archives[1].name)
        self.assertEqual(len(list(self.output.glob("*.sha256"))), 2)


if __name__ == "__main__":
    unittest.main()
