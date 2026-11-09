"""Preserve independent upgrade snapshots made in the same second."""

from __future__ import annotations

import gzip
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def synthetic_installer(
    path: Path, upgrade_root: Path, database: Path
) -> Path:
    """Redirect all installer snapshot operations into a disposable sandbox."""

    script = (ROOT / "scripts/install.sh").read_text()
    assert script.rstrip().endswith('main "$@"')
    script = script.rsplit('main "$@"', 1)[0]
    script = script.replace(
        "/var/backups/shelly-voltage/upgrades", str(upgrade_root)
    )
    script = script.replace(
        "DATABASE_PATH=/var/lib/shelly-voltage/voltage.sqlite3",
        f'DATABASE_PATH="{database}"',
    )
    target = path / "isolated-installer-functions.sh"
    target.write_text(script)
    return target


def set_history(path: Path, value: int) -> None:
    """Update the value of one independent synthetic SQLite measurement."""

    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS measurements (voltage INTEGER)")
        db.execute("DELETE FROM measurements")
        db.execute("INSERT INTO measurements VALUES (?)", (value,))


def invoke_snapshot(installer: Path, target: Path, clock: Path) -> None:
    """Run only the sandboxed snapshot helper, never install or deploy."""

    cmd = (
        'source "$1"; SOURCE="$2"; TARGET="$3"; '
        "DRY_RUN=false; snapshot_upgrade"
    )
    env = os.environ | {"PATH": f"{clock}:{os.environ['PATH']}"}
    subprocess.run(
        ["bash", "-c", cmd, "_", str(installer), str(ROOT), str(target)],
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    )


def recovered_values(upgrades: Path) -> list[int]:
    """Read each snapshot's SQLite archive in a disconnected temp copy."""

    values = []
    for archive in upgrades.glob("*/voltage-*.sqlite3.gz"):
        with tempfile.TemporaryDirectory() as directory:
            restored = Path(directory) / "restored.sqlite3"
            restored.write_bytes(gzip.decompress(archive.read_bytes()))
            with closing(sqlite3.connect(restored)) as db:
                values.append(
                    db.execute("SELECT voltage FROM measurements").fetchone()[
                        0
                    ]
                )
    return values


def create_fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    """Prepare a disposable source, clock and SQLite destination."""

    root.mkdir(parents=True, exist_ok=True)
    upgrades = root / "upgrades"
    database = root / "live.sqlite3"
    installed = root / "installed"
    installed.mkdir()
    (installed / "VERSION").write_text("0.5.13-hw\n")
    clock = root / "bin"
    clock.mkdir()
    fake_date = clock / "date"
    fake_date.write_text("#!/bin/sh\necho 20261109-073000\n")
    fake_date.chmod(0o700)
    return (
        upgrades,
        database,
        installed,
        clock,
        synthetic_installer(root, upgrades, database),
    )


class UpgradeSnapshotUniquenessTests(unittest.TestCase):
    """A fresh upgrade attempt cannot overwrite its predecessor."""

    def test_same_second_snapshots_retain_both_versions(self) -> None:
        """Two distinct states survive the same wall clock second."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            upgrades, db, installed, clock, installer = create_fixture(root)
            set_history(db, 111)
            invoke_snapshot(installer, installed, clock)
            set_history(db, 222)
            invoke_snapshot(installer, installed, clock)
            archives = list(upgrades.glob("*/source.tar.gz"))
            self.assertEqual(len(archives), 2)
            self.assertEqual(set(recovered_values(upgrades)), {111, 222})
            self.assertEqual(stat.S_IMODE(upgrades.stat().st_mode), 0o700)
            for archive in archives:
                self.assertEqual(
                    stat.S_IMODE(archive.parent.stat().st_mode), 0o700
                )

    def test_invalid_version_cannot_create_snapshot(self) -> None:
        """Do not use an untrusted previous VERSION as a directory name."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            upgrades, db, installed, clock, installer = create_fixture(root)
            (installed / "VERSION").write_text("../unsafe\n")
            set_history(db, 111)
            with self.assertRaises(subprocess.CalledProcessError):
                invoke_snapshot(installer, installed, clock)
            self.assertFalse(upgrades.exists())

    def test_rotation_handles_spaces_without_overwriting(self) -> None:
        """Prune to five snapshot pairs even when path contains spaces."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "upgrade workspace with spaces"
            upgrades, db, installed, clock, installer = create_fixture(root)
            for value in range(1, 7):
                set_history(db, value)
                invoke_snapshot(installer, installed, clock)
            self.assertEqual(len(list(upgrades.glob("*/source.tar.gz"))), 5)
            self.assertEqual(set(recovered_values(upgrades)), {2, 3, 4, 5, 6})


if __name__ == "__main__":
    unittest.main()
