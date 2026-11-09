"""Protect previous rescue snapshots when rollback clocks collide."""

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

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "scripts/rollback.sh"
BACKUP = ROOT / "scripts/backup.sh"


def put_measurement(path: Path, value: int) -> None:
    """Write a safe test-only source SQLite value."""

    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS measurements (value INTEGER)")
        db.execute("DELETE FROM measurements")
        db.execute("INSERT INTO measurements VALUES (?)", (value,))


def rescue_once(path: Path, database: Path, root: Path, bin_dir: Path) -> None:
    """Run only the isolated rescue function, never Docker or main."""

    env = os.environ | {
        "SVT_ROOT": str(root),
        "DATABASE_PATH": str(database),
        "RESCUE_ROOT": str(path),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    command = "source <(sed '$d' \"$1\"); rescue_current_db"
    subprocess.run(
        ["bash", "-c", command, "_", str(ROLLBACK)],
        env=env,
        check=True,
        capture_output=True,
    )


def archived_values(path: Path) -> set[int]:
    """Read all independent backups from disposable rescue folders."""

    found = set()
    for archive in path.glob("rollback-rescue-*/*.sqlite3.gz"):
        assert stat.S_IMODE(archive.parent.stat().st_mode) == 0o700
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sidecar = Path(f"{archive}.sha256")
        assert sidecar.read_text().strip() == f"{digest}  {archive}"
        payload = gzip.decompress(archive.read_bytes())
        with tempfile.TemporaryDirectory() as folder:
            restored = Path(folder) / "restored.sqlite3"
            restored.write_bytes(payload)
            with closing(sqlite3.connect(restored)) as db:
                found.add(
                    db.execute("SELECT value FROM measurements").fetchone()[0]
                )
    return found


class RescueBackupUniquenessTests(unittest.TestCase):
    """A rescue must never silently replace its predecessor."""

    def test_two_rescues_same_second_keep_both_histories(self) -> None:
        """Distinct historic records survive consecutive rescue attempts."""

        with tempfile.TemporaryDirectory() as name:
            workspace = Path(name)
            data = workspace / "live.sqlite3"
            rescue_root = workspace / "rescues"
            rescue_root.mkdir(mode=0o700)
            target = workspace / "source"
            (target / "scripts").mkdir(parents=True)
            backup_script = target / "scripts/backup.sh"
            backup_script.write_bytes(BACKUP.read_bytes())
            backup_script.chmod(0o700)
            clock_dir = workspace / "bin"
            clock_dir.mkdir()
            date = clock_dir / "date"
            date.write_text("#!/bin/sh\necho 20261109-070500\n")
            date.chmod(0o700)
            put_measurement(data, 111)
            rescue_once(rescue_root, data, target, clock_dir)
            put_measurement(data, 222)
            rescue_once(rescue_root, data, target, clock_dir)
            self.assertEqual(archived_values(rescue_root), {111, 222})
            self.assertEqual(
                len(list(rescue_root.glob("rollback-rescue-*"))), 2
            )


if __name__ == "__main__":
    unittest.main()
