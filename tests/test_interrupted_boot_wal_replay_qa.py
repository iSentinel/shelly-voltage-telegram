"""Repeated interrupted-boot QA using only a synthetic SQLite WAL."""

from __future__ import annotations

import fcntl
import gzip
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

UPDATER = Path(__file__).resolve().parents[1] / "scripts/apply-update.sh"
PENDING = "20261115T220000Z-0.5.21-hw"


class InterruptedBootReplayTests(unittest.TestCase):
    """No production files, sockets, containers or Shelly APIs are used."""

    def setUp(self) -> None:
        """Create private WAL history and a fake legacy updater state."""
        tmp = tempfile.TemporaryDirectory(prefix="qa-boot-replay-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db_path = self.root / "current.sqlite3"
        self.database = sqlite3.connect(self.db_path)
        self.addCleanup(self.database.close)
        self.database.execute("PRAGMA journal_mode=WAL")
        self.database.execute(
            "CREATE TABLE measurements (id INTEGER PRIMARY KEY)"
        )
        self.database.executemany(
            "INSERT INTO measurements VALUES (?)",
            [(number,) for number in range(1, 31)],
        )
        self.database.commit()
        self.state = self.root / "state"
        self.gate = self.root / "gate"
        self.gate.mkdir(mode=0o700)
        self.g = self.gate / "G"
        self.g.touch(mode=0o640)
        self.g.chmod(0o640)
        self.bin = self.root / "mock-bin"
        self.bin.mkdir(mode=0o700)
        self.trace = self.root / "mock-trace"
        self.create_mock_commands()
        self.env = {
            **os.environ,
            "SVT_ROOT": str(self.root / "missing-source"),
            "SVT_UPDATE_STATE": str(self.state),
            "SVT_OWNER_GATE_DIR": str(self.gate),
            "DATABASE_PATH": str(self.db_path),
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "QA_TRACE": str(self.trace),
        }

    def create_mock_commands(self) -> None:
        """Restrict accidental Docker and systemd calls to fake binaries."""
        for name, response in (
            ("docker", "false|no|0"),
            ("systemctl", "ActiveState=inactive\nUnitFileState=masked"),
        ):
            path = self.bin / name
            path.write_text(
                "#!/bin/sh\n"
                f'printf "{name} %s\n" "$*" >> "$QA_TRACE"\n'
                f"printf '%s\\n' {response!r}\n"
            )
            path.chmod(0o700)

    def prepare_pending(self, marker: str = PENDING) -> None:
        """Place an old or corrupt archive outside the current database."""
        self.state.mkdir(exist_ok=True)
        (self.state / "pending-update").write_text(marker)
        archive = self.state / "snapshots" / marker
        archive.mkdir(parents=True, exist_ok=True)
        (archive / "voltage-stale.sqlite3.gz").write_bytes(
            gzip.compress(b"not-a-current-valid-sqlite-database")
        )

    def run_recover(self, fd: int | None = None) -> subprocess.CompletedProcess:
        """Run the candidate only against synthetic state and fake tools."""
        env = dict(self.env)
        descriptors: tuple[int, ...] = ()
        if fd is not None:
            env["SVT_MAINTENANCE_G_FD"] = str(fd)
            descriptors = (fd,)
        return subprocess.run(
            ["bash", str(UPDATER), "--recover"],
            env=env,
            pass_fds=descriptors,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def verify_current(self, wal_before: bytes) -> None:
        """Assert that pending recovery never changed committed WAL history."""
        wal = Path(str(self.db_path) + "-wal")
        self.assertEqual(wal.read_bytes(), wal_before)
        self.assertEqual(
            self.database.execute("SELECT COUNT(*) FROM measurements")
            .fetchone()[0],
            30,
        )
        self.assertEqual(
            self.database.execute("PRAGMA quick_check").fetchone()[0],
            "ok",
        )
        self.assertFalse(self.trace.exists())

    @unittest.skipUnless(os.geteuid() == 0, "root-only boot prestart")
    def test_no_pending_repeated_boot_is_inert(self) -> None:
        """Two normal boots must not create updater state or touch WAL."""
        wal_before = Path(str(self.db_path) + "-wal").read_bytes()
        for _ in range(2):
            self.assertEqual(self.run_recover().returncode, 0)
        self.verify_current(wal_before)
        self.assertFalse(self.state.exists())

    @unittest.skipUnless(os.geteuid() == 0, "root-only boot prestart")
    def test_pending_stale_archive_never_replays(self) -> None:
        """Two interrupted boots preserve every newer committed sample."""
        self.prepare_pending()
        wal_before = Path(str(self.db_path) + "-wal").read_bytes()
        for _ in range(2):
            result = self.run_recover()
            self.assertEqual(result.returncode, 5, result.stderr)
            self.assertIn("inherited maintenance G EX", result.stderr)
        self.verify_current(wal_before)
        self.assertEqual((self.state / "pending-update").read_text(), PENDING)

    @unittest.skipUnless(os.geteuid() == 0, "root-only boot prestart")
    def test_malformed_marker_remains_untouched(self) -> None:
        """Invalid pending tokens still cannot initiate any old restore."""
        self.prepare_pending("invalid-marker")
        wal_before = Path(str(self.db_path) + "-wal").read_bytes()
        self.assertEqual(self.run_recover().returncode, 5)
        self.verify_current(wal_before)
        self.assertEqual(
            (self.state / "pending-update").read_text(), "invalid-marker"
        )

    @unittest.skipUnless(os.geteuid() == 0, "root-only boot prestart")
    def test_linked_pending_refused_before_inspection(self) -> None:
        """A pending symlink cannot redirect the candidate recovery."""
        self.state.mkdir()
        (self.state / "pending-update").symlink_to(self.root / "other")
        wal_before = Path(str(self.db_path) + "-wal").read_bytes()
        result = self.run_recover()
        self.assertEqual(result.returncode, 5)
        self.assertIn("linked updater", result.stderr)
        self.verify_current(wal_before)

    @unittest.skipUnless(os.geteuid() == 0, "root-only G examination")
    def test_valid_g_still_blocks_corrupt_recovery(self) -> None:
        """A real inherited G EX does not approve archive restoration."""
        self.prepare_pending()
        wal_before = Path(str(self.db_path) + "-wal").read_bytes()
        with self.g.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_recover(fd=held.fileno())
        self.assertEqual(result.returncode, 5)
        self.assertIn("complete owner gate", result.stderr)
        self.verify_current(wal_before)
        self.assertTrue((self.state / "pending-update").exists())

    @unittest.skipIf(os.geteuid() == 0, "nonroot boot prestart")
    def test_nonroot_always_refuses_recover(self) -> None:
        """Unprivileged recovery returns before source or DB inspection."""
        self.prepare_pending()
        wal_before = Path(str(self.db_path) + "-wal").read_bytes()
        self.assertEqual(self.run_recover().returncode, 2)
        self.verify_current(wal_before)

    def test_shell_syntax(self) -> None:
        """Never exercise a candidate with an invalid Bash parser state."""
        result = subprocess.run(
            ["bash", "-n", str(UPDATER)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
