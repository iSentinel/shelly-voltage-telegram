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
root, owner, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
def pending_present():
    pending = root / 'pending-update'
    return pending.is_symlink() or pending.exists()
if pending_present():
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
    if mode == 'pause_after_fence':
        print('FENCE_ACQUIRED', flush=True)
        sys.stdin.readline()
    if pending_present():
        print('BLOCKED_PENDING', flush=True)
        raise SystemExit(4)
    with closing(sqlite3.connect(root / 'synthetic.sqlite3')) as db:
        db.execute('INSERT INTO measurements(voltage) VALUES (223.1)')
        db.commit()
    print('OWNER_ACQUIRED:' + owner, flush=True)
    if mode == 'hold':
        sys.stdin.readline()
finally:
    os.close(fd)
"""


UPDATER = """
from pathlib import Path
import fcntl, os, sys
root = Path(sys.argv[1])
path = root / 'writer.lock'
if path.is_symlink():
    raise SystemExit(6)
fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('BLOCKED_OWNER', flush=True)
        raise SystemExit(5)
    pending = root / 'pending-update'
    if pending.exists() or pending.is_symlink():
        print('BLOCKED_PENDING', flush=True)
        raise SystemExit(4)
    marker_fd = os.open(
        pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(marker_fd, 'w') as stream:
        stream.write('synthetic-update-marker')
        stream.flush()
        os.fsync(stream.fileno())
    print('PENDING_CREATED', flush=True)
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

    def start(
        self, owner: str, hold: bool = False, pause: bool = False
    ) -> subprocess.Popen:
        """Launch one mock writer with an optional post-lock race barrier."""

        mode = "pause_after_fence" if pause else "hold" if hold else "once"
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                CHILD,
                str(self.root),
                owner,
                mode,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.close_child, process)
        return process

    def start_updater(self) -> subprocess.Popen:
        """Start one disposable marker writer sharing the same lock."""

        process = subprocess.Popen(
            [sys.executable, "-B", "-c", UPDATER, str(self.root)],
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

    def enable_pro3em_wal(self) -> None:
        """Convert only the synthetic fixture to three-phase WAL mode."""

        with closing(sqlite3.connect(self.root / "synthetic.sqlite3")) as db:
            self.assertEqual(
                db.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal"
            )
            db.executescript(
                "ALTER TABLE measurements ADD COLUMN phase TEXT "
                "NOT NULL DEFAULT 'L1';"
                "ALTER TABLE measurements ADD COLUMN frequency REAL "
                "NOT NULL DEFAULT 50.0;"
            )
            db.executemany(
                "INSERT INTO measurements(voltage,phase,frequency) "
                "VALUES (?,?,?)",
                [(211.0, "L2", 50.0), (205.0, "L3", 50.0)],
            )
            db.commit()

    def assert_three_phase_photo(self, l1: int) -> None:
        """Require all real committed fixture WAL records and PHOTO ID."""

        with closing(sqlite3.connect(self.root / "synthetic.sqlite3")) as db:
            self.assertEqual(
                db.execute("PRAGMA quick_check").fetchone()[0], "ok"
            )
            phases = db.execute(
                "SELECT phase,count(*) FROM measurements "
                "GROUP BY phase ORDER BY phase"
            ).fetchall()
            photo = db.execute(
                "SELECT media_kind,message_id FROM telegram_status"
            ).fetchone()
        self.assertEqual(phases, [("L1", l1), ("L2", 1), ("L3", 1)])
        self.assertEqual(photo, ("photo", 1000))

    def test_three_phase_wal_pending_race_preserves_every_phase(self):
        """A marker created after writer lease still blocks WAL writes."""

        self.enable_pro3em_wal()
        owner = self.start("systemd", pause=True)
        self.assertEqual(owner.stdout.readline().strip(), "FENCE_ACQUIRED")
        (self.root / "pending-update").write_text("synthetic-marker")
        self.assertIsNotNone(owner.stdin)
        owner.stdin.write("resume\n")
        owner.stdin.flush()
        output, _ = owner.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), owner.returncode), ("BLOCKED_PENDING", 4)
        )
        self.assert_three_phase_photo(10)

    def test_three_phase_wal_sigkill_then_pending_blocks_reboot(self):
        """After a killed WAL writer, updater owns the recovery gate."""

        self.enable_pro3em_wal()
        writer = self.start("docker", hold=True)
        self.assertEqual(
            writer.stdout.readline().strip(), "OWNER_ACQUIRED:docker"
        )
        writer.send_signal(signal.SIGKILL)
        self.assertEqual(writer.wait(timeout=5), -signal.SIGKILL)
        update = self.start_updater()
        output, _ = update.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), update.returncode), ("PENDING_CREATED", 0)
        )
        boot = self.start("systemd")
        output, _ = boot.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), boot.returncode), ("BLOCKED_PENDING", 4)
        )
        self.assert_three_phase_photo(11)

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

    def test_pending_created_after_lock_blocks_any_sqlite_write(self):
        """A pending marker racing with startup fails after the lock."""

        owner = self.start("systemd", pause=True)
        self.assertEqual(owner.stdout.readline().strip(), "FENCE_ACQUIRED")
        (self.root / "pending-update").write_text("synthetic-marker")
        self.assertIsNotNone(owner.stdin)
        owner.stdin.write("resume\n")
        owner.stdin.flush()
        stdout, _ = owner.communicate(timeout=5)
        self.assertEqual(
            (stdout.strip(), owner.returncode), ("BLOCKED_PENDING", 4)
        )
        self.assertEqual(self.count_and_photo(), (10, ("photo", 1000)))

    def test_pending_symlink_is_refused_even_when_target_absent(self):
        """A dangling linked marker cannot evade the fixture boot gate."""

        marker = self.root / "pending-update"
        marker.symlink_to(self.root / "absent-file")
        for name in ("docker", "systemd"):
            with self.subTest(owner=name):
                process = self.start(name)
                output, _ = process.communicate(timeout=5)
                self.assertEqual(
                    (output.strip(), process.returncode),
                    ("BLOCKED_PENDING", 4),
                )
        self.assertEqual(self.count_and_photo(), (10, ("photo", 1000)))

    def test_updater_cannot_publish_pending_while_boot_writer_holds_lock(self):
        """A real competing updater process cannot cross the writer fence."""

        boot = self.start("docker", hold=True)
        self.assertEqual(
            boot.stdout.readline().strip(), "OWNER_ACQUIRED:docker"
        )
        updater = self.start_updater()
        output, _ = updater.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), updater.returncode), ("BLOCKED_OWNER", 5)
        )
        self.assertFalse((self.root / "pending-update").exists())
        self.assertEqual(self.count_and_photo(), (11, ("photo", 1000)))

    def test_updater_pending_after_sigkill_blocks_new_boot(self):
        """Update marker written under lock prevents independent reboot."""

        boot = self.start("systemd", hold=True)
        self.assertEqual(
            boot.stdout.readline().strip(), "OWNER_ACQUIRED:systemd"
        )
        boot.kill()
        boot.wait(timeout=5)
        updater = self.start_updater()
        output, _ = updater.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), updater.returncode), ("PENDING_CREATED", 0)
        )
        restarted = self.start("docker")
        output, _ = restarted.communicate(timeout=5)
        self.assertEqual(
            (output.strip(), restarted.returncode), ("BLOCKED_PENDING", 4)
        )
        self.assertEqual(self.count_and_photo(), (11, ("photo", 1000)))

    def test_symlinked_writer_token_rejected(self):
        """File substitution cannot redirect a new writer lock."""

        (self.root / "writer.lock").symlink_to(self.root / "synthetic.sqlite3")
        owner = self.start("systemd")
        owner.communicate(timeout=5)
        self.assertEqual(owner.returncode, 6)
        self.assertEqual(self.count_and_photo(), (10, ("photo", 1000)))


if __name__ == "__main__":
    unittest.main()
