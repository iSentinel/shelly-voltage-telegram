"""Disposable boot-owner contention and pending-marker rejection tests."""

from __future__ import annotations

import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

CHILD = """
from contextlib import closing
from pathlib import Path
import fcntl, os, sqlite3, sys
root, owner, hold = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == 'hold'
if (root / 'pending-update').exists():
    print('BLOCKED_PENDING', flush=True)
    raise SystemExit(4)
path = root / 'writer.lock'
if path.is_symlink():
    raise SystemExit(6)
fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('BLOCKED_OWNER', flush=True)
        raise SystemExit(5)
    with closing(sqlite3.connect(root / 'synthetic.sqlite3')) as db:
        db.execute('INSERT INTO measurements(voltage) VALUES (223.1)')
        db.commit()
    print('OWNER_ACQUIRED:' + owner, flush=True)
    if hold:
        sys.stdin.readline()
finally:
    os.close(fd)
"""


class DualOwnerRehearsalTests(unittest.TestCase):
    """Test desired shared writer fencing without real bot processes."""

    def setUp(self) -> None:
        """Create private fixture data and an unchanged mock PHOTO marker."""

        temporary = tempfile.TemporaryDirectory(prefix="svt-boot-owner-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with closing(sqlite3.connect(self.root / "synthetic.sqlite3")) as db:
            db.executescript(
                "CREATE TABLE measurements (id INTEGER PRIMARY KEY,"
                " voltage REAL);"
                "CREATE TABLE telegram_status (media_kind TEXT,"
                " message_id INTEGER);"
                "INSERT INTO telegram_status VALUES ('photo',1000);"
            )
            db.executemany(
                "INSERT INTO measurements(voltage) VALUES (?)",
                ((220.0,) for _ in range(10)),
            )
            db.commit()

    def start(self, owner: str, hold: bool = False) -> subprocess.Popen:
        """Launch a mock systemd/Docker writer without network or secrets."""

        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                CHILD,
                str(self.root),
                owner,
                "hold" if hold else "once",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.close_child, process)
        return process

    @staticmethod
    def close_child(process: subprocess.Popen) -> None:
        """Clean up only this fixture child, never production processes."""

        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def count_and_photo(self) -> tuple[int, tuple]:
        """Read full synthetic data after mock boot attempts."""

        with closing(sqlite3.connect(self.root / "synthetic.sqlite3")) as db:
            return (
                db.execute("SELECT count(*) FROM measurements").fetchone()[0],
                db.execute("SELECT * FROM telegram_status").fetchone(),
            )

    def test_docker_owner_blocks_second_systemd_writer(self):
        """A competing owner cannot insert any records while lock held."""

        docker = self.start("docker", hold=True)
        self.assertEqual(
            docker.stdout.readline().strip(), "OWNER_ACQUIRED:docker"
        )
        systemd = self.start("systemd")
        self.assertEqual(
            systemd.communicate(timeout=5)[0].strip(), "BLOCKED_OWNER"
        )
        self.assertEqual(systemd.returncode, 5)
        self.assertEqual(self.count_and_photo(), (11, ("photo", 1000)))

    def test_pending_marker_refuses_both_start_owners(self):
        """Pending recovery blocks both startup identities before a write."""

        (self.root / "pending-update").write_text("synthetic-marker")
        for owner in ("systemd", "docker"):
            with self.subTest(owner=owner):
                child = self.start(owner)
                out, _ = child.communicate(timeout=5)
                self.assertEqual(
                    (out.strip(), child.returncode), ("BLOCKED_PENDING", 4)
                )
        self.assertEqual(self.count_and_photo(), (10, ("photo", 1000)))

    def test_sigkill_releases_lock_but_pending_still_blocks(self):
        """A restarted second owner must inspect pending before acquiring."""

        first = self.start("docker", hold=True)
        self.assertEqual(
            first.stdout.readline().strip(), "OWNER_ACQUIRED:docker"
        )
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=5)
        (self.root / "pending-update").write_text("synthetic-marker")
        second = self.start("systemd")
        out, _ = second.communicate(timeout=5)
        self.assertEqual(
            (out.strip(), second.returncode), ("BLOCKED_PENDING", 4)
        )
        self.assertEqual(self.count_and_photo(), (11, ("photo", 1000)))

    def test_clean_restart_only_after_pending_removed(self):
        """No writer is resumed until pending data is separately resolved."""

        first = self.start("docker", hold=True)
        self.assertEqual(
            first.stdout.readline().strip(), "OWNER_ACQUIRED:docker"
        )
        first.kill()
        first.wait(timeout=5)
        (self.root / "pending-update").write_text("synthetic-marker")
        blocked = self.start("systemd")
        blocked.communicate(timeout=5)
        self.assertEqual(blocked.returncode, 4)
        (self.root / "pending-update").unlink()
        resumed = self.start("systemd")
        output, _ = resumed.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), resumed.returncode), ("OWNER_ACQUIRED:systemd", 0)
        )
        self.assertEqual(self.count_and_photo(), (12, ("photo", 1000)))

    def test_symlinked_writer_token_rejected(self):
        """File substitution cannot redirect a new writer lock."""

        (self.root / "writer.lock").symlink_to(self.root / "synthetic.sqlite3")
        owner = self.start("systemd")
        owner.communicate(timeout=5)
        self.assertEqual(owner.returncode, 6)
        self.assertEqual(self.count_and_photo(), (10, ("photo", 1000)))


if __name__ == "__main__":
    unittest.main()
