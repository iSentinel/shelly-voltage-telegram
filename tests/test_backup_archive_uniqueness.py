"""Regression tests for safe independent daily SQLite backup snapshots."""

from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backup.sh"


def make_db(path: Path, label: str) -> None:
    """Create one disposable SQLite history with recognizable contents."""

    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE readings (label TEXT)")
        db.execute("INSERT INTO readings VALUES (?)", (label,))


def read_archive(path: Path, directory: Path) -> str:
    """Verify the checksum and restore exactly one archive in a sandbox."""

    checksum = Path(f"{path}.sha256").read_text().strip()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != f"{digest}  {path}":
        raise AssertionError("backup checksum did not match archive")
    target = directory / "restored.sqlite3"
    target.write_bytes(gzip.decompress(path.read_bytes()))
    with closing(sqlite3.connect(target)) as db:
        return str(db.execute("SELECT label FROM readings").fetchone()[0])


def fixed_clock(root: Path) -> Path:
    """Create a fake date binary pinned to one second."""

    folder = root / "bin"
    folder.mkdir()
    clock = folder / "date"
    clock.write_text("#!/bin/sh\necho 20261109-002700\n")
    clock.chmod(0o700)
    return folder


class BackupUniquenessTests(unittest.TestCase):
    """No two snapshots may replace each other within one clock second."""

    def test_same_second_backups_remain_independent(self) -> None:
        """Keep both verified archives when the clock is identical."""

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            destination = root / "backups"
            destination.mkdir(mode=0o700)
            database = root / "measurements.sqlite3"
            fixture_bin = fixed_clock(root)
            env = os.environ | {
                "DATABASE_PATH": str(database),
                "BACKUP_DIR": str(destination),
                "BACKUP_KEEP": "14",
                "PATH": f"{fixture_bin}:{os.environ['PATH']}",
            }
            make_db(database, "first")
            subprocess.run(
                ["bash", str(SCRIPT)], check=True, env=env, capture_output=True
            )
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("UPDATE readings SET label='second'")
            subprocess.run(
                ["bash", str(SCRIPT)], check=True, env=env, capture_output=True
            )
            archives = sorted(destination.glob("voltage-*.sqlite3.gz"))
            self.assertEqual(len(archives), 2)
            restored = {read_archive(p, root) for p in archives}
            self.assertEqual(restored, {"first", "second"})
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            for archive in archives:
                self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
                sidecar = Path(f"{archive}.sha256")
                self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
            self.assertEqual(list(destination.glob(".voltage-*")), [])

    def test_parallel_backups_never_share_public_names(self) -> None:
        """Concurrent snapshots with one timestamp keep distinct archives."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = root / "source.sqlite3"
            make_db(db, "shared")
            output = root / "backups"
            clock_dir = fixed_clock(root)
            env = os.environ | {
                "DATABASE_PATH": str(db),
                "BACKUP_DIR": str(output),
                "BACKUP_KEEP": "14",
                "PATH": f"{clock_dir}:{os.environ['PATH']}",
            }
            with (
                subprocess.Popen(
                    ["bash", str(SCRIPT)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ) as first,
                subprocess.Popen(
                    ["bash", str(SCRIPT)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ) as second,
            ):
                a, err_a = first.communicate(timeout=30)
                b, err_b = second.communicate(timeout=30)
                self.assertEqual(first.returncode, 0, (a, err_a))
                self.assertEqual(second.returncode, 0, (b, err_b))
            archives = list(output.glob("voltage-*.sqlite3.gz"))
            self.assertEqual(len(archives), 2)
            self.assertEqual(
                [read_archive(p, root) for p in archives], ["shared", "shared"]
            )
            self.assertEqual(list(output.glob(".voltage-*")), [])

    def test_rescue_retains_newest_with_keep_one(self) -> None:
        """Rollback's BACKUP_KEEP=1 retains only the latest valid history."""

        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            db = root / "db.sqlite3"
            dest = root / "backups"
            make_db(db, "before")
            env = os.environ | {
                "DATABASE_PATH": str(db),
                "BACKUP_DIR": str(dest),
                "BACKUP_KEEP": "1",
            }
            subprocess.run(
                ["bash", str(SCRIPT)], env=env, check=True, capture_output=True
            )
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("UPDATE readings SET label='after'")
            subprocess.run(
                ["bash", str(SCRIPT)], env=env, check=True, capture_output=True
            )
            files = list(dest.glob("voltage-*.sqlite3.gz"))
            self.assertEqual(len(files), 1)
            self.assertEqual(read_archive(files[0], root), "after")
            self.assertEqual(list(dest.glob(".voltage-*")), [])

    def test_failed_sidecar_publish_cleans_archive(self) -> None:
        """Never leave orphaned public gzip files when checksum fails."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = root / "db.sqlite3"
            make_db(db, "test")
            destination = root / "backup"
            directory = root / "bin"
            directory.mkdir()
            stub = directory / "ln"
            stub.write_text(
                '#!/bin/sh\ncase "$*" in *".sha256"*) exit 23;; esac\n'
                'exec /usr/bin/ln "$@"\n'
            )
            stub.chmod(0o700)
            env = os.environ | {
                "DATABASE_PATH": str(db),
                "BACKUP_DIR": str(destination),
                "PATH": f"{directory}:{os.environ['PATH']}",
            }
            done = subprocess.run(
                ["bash", str(SCRIPT)], env=env, capture_output=True
            )
            self.assertNotEqual(done.returncode, 0)
            self.assertEqual(list(destination.iterdir()), [])

    def test_unknown_options_never_start_backup(self) -> None:
        """A mistaken dry-run flag must not write or rotate backups."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = root / "source.sqlite3"
            make_db(db, "test")
            for args in (("--dry-run",), ("--unknown",), ("--help", "oops")):
                with self.subTest(args=args):
                    dest = root / "backups"
                    env = os.environ | {
                        "DATABASE_PATH": str(db),
                        "BACKUP_DIR": str(dest),
                    }
                    done = subprocess.run(
                        ["bash", str(SCRIPT), *args],
                        capture_output=True,
                        env=env,
                    )
                    self.assertNotEqual(done.returncode, 0)
                    self.assertFalse(dest.exists())

    def test_bad_retention_rejected_before_creating_archive(self) -> None:
        """Invalid rotation settings must never destroy backup history."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            db = root / "db.sqlite3"
            make_db(db, "test")
            for keep in ("0", "-1", "oops"):
                with self.subTest(keep=keep):
                    output = root / "backups"
                    env = os.environ | {
                        "DATABASE_PATH": str(db),
                        "BACKUP_DIR": str(output),
                        "BACKUP_KEEP": keep,
                    }
                    done = subprocess.run(
                        ["bash", str(SCRIPT)], env=env, capture_output=True
                    )
                    self.assertNotEqual(done.returncode, 0)
                    self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
