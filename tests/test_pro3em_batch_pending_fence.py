"""Test the patched Storage batch inside a shared mock updater fence."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from test_app import APP

CHILD = """
import fcntl, os, sys
from pathlib import Path
from app import Storage

root = Path(sys.argv[1])
mode = sys.argv[2]
pending = root / 'pending-update'

def hold(name, nonblock=False):
    fd = os.open(root / name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblock else 0)
    try:
        fcntl.flock(fd, flags)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd

if mode == 'publish':
    fd = hold('txn.lock')
    if pending.exists():
        raise SystemExit(5)
    pending.write_text('qa-only')
    marker_fd = os.open(pending, os.O_RDONLY)
    os.fsync(marker_fd)
    os.close(marker_fd)
    os.close(fd)
    print('PENDING_PUBLISHED', flush=True)
    raise SystemExit(0)

owner = hold('owner.lock', True)
if owner is None:
    print('BLOCKED_OWNER', flush=True)
    raise SystemExit(2)

db = Storage(root / 'history.sqlite3', 'Europe/Kyiv')
gate = hold('txn.lock')
if pending.exists():
    print('BLOCKED_PENDING', flush=True)
    raise SystemExit(4)
if mode == 'pause':
    print('GATE_HELD', flush=True)
    sys.stdin.readline()
batch = {
    'L1': (176.0, 50.0, 1.0),
    'L2': (184.0, 50.0, 1.0),
    'L3': (193.0, 50.0, 1.0),
}
db.add_phases(1800000000.0, batch)
os.close(gate)
print('COMMITTED', flush=True)
sys.stdin.readline()
gate = hold('txn.lock')
if pending.exists():
    print('BLOCKED_PENDING', flush=True)
    raise SystemExit(4)
db.add_phases(1800000010.0, batch)
os.close(gate)
print('SECOND_COMMITTED', flush=True)
"""


class BatchPendingFenceTests(unittest.TestCase):
    """Exercise actual app.Storage.add_phases with independent processes."""

    def setUp(self) -> None:
        """Create a private WAL-mode fixture and saved PHOTO identity."""

        tmp = tempfile.TemporaryDirectory(prefix="svt-batch-fence-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "history.sqlite3"
        storage = APP.Storage(self.db, "Europe/Kyiv")
        storage.initialize()
        storage.set_meta("telegram_setting_meter_type", "pro3em")
        storage.set_meta("telegram_status_media_identity", "photo:1000")

    def child_command(self, mode: str) -> list[str]:
        """Use the real Python process, never Docker or a live service."""

        return [sys.executable, "-c", CHILD, str(self.root), mode]

    def run_child(self, mode: str) -> subprocess.CompletedProcess:
        """Execute an independent short-lived updater or writer."""

        return subprocess.run(
            self.child_command(mode),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def start_child(self, mode: str) -> subprocess.Popen:
        """Start a paused writer and register an unconditional cleanup."""

        child = subprocess.Popen(
            self.child_command(mode),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self.stop_child, child)
        return child

    @staticmethod
    def stop_child(child: subprocess.Popen) -> None:
        """Reap any remaining test process and close inherited pipes."""

        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)

    @staticmethod
    def resume(child: subprocess.Popen) -> None:
        """Release exactly one test-mode phase synchronization barrier."""

        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.flush()

    def audit(self, expected: int) -> None:
        """Confirm complete triplets, no fabricated phases, and PHOTO."""

        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(
                db.execute("PRAGMA quick_check").fetchone()[0], "ok"
            )
            rows = db.execute(
                "SELECT ts,phase,voltage FROM measurements ORDER BY ts,phase"
            ).fetchall()
            self.assertEqual(len(rows), expected)
            self.assertEqual(
                db.execute(
                    "SELECT value FROM meta WHERE key=?",
                    ("telegram_setting_meter_type",),
                ).fetchone(),
                ("pro3em",),
            )
            self.assertEqual(
                db.execute(
                    "SELECT value FROM meta "
                    "WHERE key='telegram_status_media_identity'"
                ).fetchone(),
                ("photo:1000",),
            )
        by_time: dict[float, set[str]] = {}
        for ts, phase, volts in rows:
            self.assertGreater(volts, 0)
            by_time.setdefault(ts, set()).add(phase)
        self.assertTrue(all(x == {"L1", "L2", "L3"} for x in by_time.values()))

    def test_preexisting_pending_rejects_actual_storage_batch(self):
        """The updater marker blocks all three inserts before opening WAL."""

        published = self.run_child("publish")
        self.assertEqual(published.returncode, 0, published.stderr)
        writer = self.run_child("writer")
        self.assertEqual(writer.returncode, 4, writer.stderr)
        self.assertIn("BLOCKED_PENDING", writer.stdout)
        self.audit(0)

    def test_second_process_rejected_and_live_writer_stops_on_pending(self):
        """Lifetime owner and shared gate protect actual atomic writes."""

        writer = self.start_child("writer")
        assert writer.stdout is not None
        self.assertEqual(writer.stdout.readline().strip(), "COMMITTED")
        second = self.run_child("writer")
        self.assertEqual(second.returncode, 2, second.stderr)
        self.assertIn("BLOCKED_OWNER", second.stdout)
        self.assertEqual(self.run_child("publish").returncode, 0)
        self.resume(writer)
        self.assertEqual(writer.wait(timeout=5), 4)
        self.audit(3)

    def test_publisher_waits_until_atomic_batch_finishes(self):
        """A pending marker cannot overtake the transaction lock."""

        writer = self.start_child("pause")
        assert writer.stdout is not None
        self.assertEqual(writer.stdout.readline().strip(), "GATE_HELD")
        publisher = self.start_child("publish")
        time.sleep(0.25)
        self.assertFalse((self.root / "pending-update").exists())
        self.resume(writer)
        self.assertEqual(writer.stdout.readline().strip(), "COMMITTED")
        self.assertEqual(publisher.wait(timeout=5), 0)
        self.assertTrue((self.root / "pending-update").exists())
        self.resume(writer)
        self.assertEqual(writer.wait(timeout=5), 4)
        self.audit(3)


if __name__ == "__main__":
    unittest.main()
