"""Preserve independent upgrade snapshots made in the same second."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import sqlite3
import stat
import subprocess
import tarfile
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
        "sqlite_db=/var/lib/shelly-voltage/voltage.sqlite3",
        f'sqlite_db="{database}"',
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


def invoke_snapshot(
    installer: Path,
    target: Path,
    clock: Path,
    source: Path = ROOT,
    extra_env: dict[str, str] | None = None,
) -> None:
    """Run only the sandboxed snapshot helper, never install or deploy."""

    cmd = (
        'source "$1"; SOURCE="$2"; TARGET="$3"; '
        "DRY_RUN=false; snapshot_upgrade"
    )
    env = (
        os.environ
        | {"PATH": f"{clock}:{os.environ['PATH']}"}
        | (extra_env or {})
    )
    subprocess.run(
        ["bash", "-c", cmd, "_", str(installer), str(source), str(target)],
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
    for filename in (
        "app.py",
        "Dockerfile",
        "compose.yaml",
        "requirements.txt",
        "scripts/backup.sh",
        "systemd/shelly-voltage-compose.service",
        "systemd/shelly-voltage-backup.service",
    ):
        item = installed / filename
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("synthetic rollback fixture\n")
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


def stale_backup_source(root: Path) -> Path:
    """Create fake-success backup producer with valid but stale content."""

    release = root / "test-release"
    scripts = release / "scripts"
    scripts.mkdir(parents=True)
    for file in (
        "source-tar.sh",
        "validate-sqlite-archive.py",
        "em50-predeploy-backup-guard.py",
    ):
        (scripts / file).write_bytes((ROOT / "scripts" / file).read_bytes())
    fake = scripts / "backup.sh"
    fake.write_text(
        "#!/bin/bash\nset -euo pipefail\n"
        'archive="$BACKUP_DIR/voltage-stale.sqlite3.gz"\n'
        'gzip -c "$STALE_DB" > "$archive"\n'
        'sha256sum "$archive" > "$archive.sha256"\n'
    )
    fake.chmod(0o700)
    (release / "VERSION").write_text("0.5.14-hw\n")
    return release


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

    def test_valid_but_stale_sqlite_snapshot_is_rejected(self) -> None:
        """Do not accept a valid but stale SQLite backup."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            upgrades, db, installed, clock, installer = create_fixture(root)
            old = root / "stale.sqlite3"
            set_history(old, 111)
            set_history(db, 222)
            source = stale_backup_source(root)
            with self.assertRaises(subprocess.CalledProcessError) as result:
                invoke_snapshot(
                    installer, installed, clock, source, {"STALE_DB": str(old)}
                )
            self.assertIn(b"invalid SQLite archive", result.exception.stderr)
            archives = list(upgrades.glob("*/voltage-*.sqlite3.gz"))
            self.assertEqual(len(archives), 1)
            validator = source / "scripts/validate-sqlite-archive.py"
            basic = subprocess.run(
                ["python3", str(validator), "--archive", str(archives[0])],
                capture_output=True,
                check=True,
            )
            self.assertIn(b"SQLITE ARCHIVE PASS", basic.stdout)
            strict = subprocess.run(
                [
                    "python3",
                    str(validator),
                    "--archive",
                    str(archives[0]),
                    "--compare-db",
                    str(db),
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(strict.returncode, 2)

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

    def test_rotation_preserves_unknown_recovery_directories(self) -> None:
        """Never delete unrelated or incomplete snapshot directories."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            upgrades, db, installed, clock, installer = create_fixture(root)
            upgrades.mkdir(mode=0o700)
            unrelated = upgrades / "manual-recovery-do-not-delete"
            unrelated.mkdir()
            (unrelated / "marker.txt").write_text("keep")
            incomplete = upgrades / "20261109-073000-0.5.13-hw-ABCDEFGH"
            incomplete.mkdir()
            (incomplete / "source.tar.gz").write_text("incomplete")
            for value in range(1, 7):
                set_history(db, value)
                invoke_snapshot(installer, installed, clock)
            self.assertTrue((unrelated / "marker.txt").is_file())
            self.assertTrue((incomplete / "source.tar.gz").is_file())
            archive_count = len(list(upgrades.glob("*/voltage-*.sqlite3.gz")))
            self.assertEqual(archive_count, 5)

    def test_corrupt_snapshot_preserves_valid_backups(self) -> None:
        """A corrupt-looking recovery folder must not displace good copies."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            upgrades, db, installed, clock, installer = create_fixture(root)
            upgrades.mkdir(mode=0o700)
            corrupt = upgrades / "20261109-073000-0.5.13-hw-ABCDEF12"
            corrupt.mkdir()
            set_history(db, 0)
            source_tar = corrupt / "source.tar.gz"
            source_tar.write_bytes(b"not a gzip TAR")
            sqlite_tar = corrupt / "voltage-pretend.sqlite3.gz"
            sqlite_tar.write_bytes(gzip.compress(db.read_bytes()))
            for archive in (source_tar, sqlite_tar):
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                Path(str(archive) + ".sha256").write_text(
                    f"{digest}  {archive}\n"
                )
            (corrupt / "manifest.env").write_text("from_version=0.5.13-hw\n")
            os.utime(corrupt, (2145916800, 2145916800))
            for value in range(1, 7):
                set_history(db, value)
                invoke_snapshot(installer, installed, clock)
            valid = [
                path
                for path in upgrades.glob("*/source.tar.gz")
                if path.parent != corrupt
            ]
            self.assertTrue((corrupt / "source.tar.gz").exists())
            self.assertEqual(len(valid), 5)

    def test_valid_gzip_incomplete_source_cannot_displace_backup(self):
        """A VERSION-only archive is not a restorable backup."""

        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            upgrades, db, installed, clock, installer = create_fixture(root)
            upgrades.mkdir(mode=0o700)
            unsafe = upgrades / "20261109-073000-0.5.13-hw-BADDCAFE"
            unsafe.mkdir()
            set_history(db, 0)
            source = unsafe / "source.tar.gz"
            with tarfile.open(source, "w:gz") as archive:
                contents = b"0.5.13-hw\n"
                entry = tarfile.TarInfo("VERSION")
                entry.size = len(contents)
                archive.addfile(entry, io.BytesIO(contents))
            backup = unsafe / "voltage-pretend.sqlite3.gz"
            backup.write_bytes(gzip.compress(db.read_bytes()))
            for archive in (source, backup):
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                Path(str(archive) + ".sha256").write_text(
                    f"{digest}  {archive}\n"
                )
            (unsafe / "manifest.env").write_text("from_version=0.5.13-hw\n")
            os.utime(unsafe, (2145916800, 2145916800))
            for value in range(1, 7):
                set_history(db, value)
                invoke_snapshot(installer, installed, clock)
            healthy = [
                item
                for item in upgrades.glob("*/source.tar.gz")
                if item.parent != unsafe
            ]
            self.assertTrue(source.exists())
            self.assertEqual(len(healthy), 5)

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
