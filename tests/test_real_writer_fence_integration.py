"""Check the real Storage and monitor-owner locking contract."""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from test_app import APP

from writer_fence import WriterFenceError, transaction_gate, writer_lease

KILL_CHILD = """
import sys
import time
from pathlib import Path
from app import Storage
from writer_fence import writer_lease
path = Path(sys.argv[1])
with writer_lease(path):
    Storage(path, "Europe/Kyiv").add_phases(1050, {
        "L1": (222, 50, 1),
        "L2": (223, 50, 1),
        "L3": (224, 50, 1),
    })
    print("COMMITTED", flush=True)
    time.sleep(60)
"""


class RealWriterFenceTests(unittest.TestCase):
    """Exercise app.Storage with independent filesystem lock owners."""

    def setUp(self) -> None:
        """Create one private SQLite fixture for every test."""
        temp = tempfile.TemporaryDirectory(prefix="svt-real-writer-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "history.sqlite3"
        self.storage = APP.Storage(self.path, "Europe/Kyiv")
        self.storage.initialize()
        self.storage.set_meta("telegram_status_media_identity", "photo:123")
        self.storage.set_meta("telegram_setting_meter_type", "pro3em")

    def count(self) -> int:
        """Read the synthetic history without mutating it."""
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT COUNT(*) FROM measurements").fetchone()[
                0
            ]

    def test_pending_prevents_phase_batch_and_meta(self) -> None:
        """No SQLite write can begin after a durable pending marker."""
        marker = self.path.parent / ".svt-update-pending"
        with transaction_gate(self.path):
            marker.write_text("qa-only")
        with self.assertRaisesRegex(WriterFenceError, "SVT_UPDATE_PENDING"):
            self.storage.add_phases(1000, {"L1": (223, 50, 1)})
        with self.assertRaisesRegex(WriterFenceError, "SVT_UPDATE_PENDING"):
            self.storage.set_meta("telegram_status_media_identity", "changed")
        self.assertEqual(self.count(), 0)
        marker.unlink()
        self.storage.add_phases(1000, {"L1": (223, 50, 1)})
        self.assertEqual(self.count(), 1)

    def test_unsafe_symlink_marker_fails_closed(self) -> None:
        """A linked marker cannot masquerade as no update."""
        marker = self.path.parent / ".svt-update-pending"
        marker.symlink_to(self.path)
        with self.assertRaisesRegex(
            WriterFenceError, "SVT_PENDING_MARKER_UNSAFE"
        ):
            self.storage.add(1000, 223, 50)
        self.assertEqual(self.count(), 0)

    def test_lifetime_owner_refuses_competing_process(self) -> None:
        """The competing Python process cannot acquire the same lease."""
        child = """
import sys
from pathlib import Path
from writer_fence import writer_lease, WriterFenceError
path = Path(sys.argv[1])
try:
    with writer_lease(path):
        sys.exit(9)
except WriterFenceError:
    sys.exit(3)
"""
        with writer_lease(self.path):
            result = subprocess.run(
                [sys.executable, "-c", child, str(self.path)],
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=6,
            )
            self.assertEqual(result.returncode, 3)
        with writer_lease(self.path):
            self.storage.add_phases(1010, {"L1": (226, 50, 1)})
        self.assertEqual(self.count(), 1)

    def test_pending_waits_for_existing_transaction(self) -> None:
        """The updater cannot publish while SQLite transaction gate is held."""
        marker = self.path.parent / ".svt-update-pending"
        started = threading.Event()
        published = threading.Event()

        def publish() -> None:
            """Take the same lock as real Storage, then publish the marker."""
            started.set()
            with transaction_gate(self.path):
                marker.write_text("qa-only")
            published.set()

        with closing(self.storage.connect()) as db:
            thread = threading.Thread(target=publish, daemon=True)
            thread.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.1)
            self.assertFalse(published.is_set())
            db.execute("SELECT 1")
        thread.join(timeout=3)
        self.assertTrue(published.is_set())
        with self.assertRaisesRegex(WriterFenceError, "SVT_UPDATE_PENDING"):
            self.storage.add(1010, 226, 50)
        self.assertEqual(self.count(), 0)

    def test_sigkill_releases_owner_without_losing_committed_phases(
        self,
    ) -> None:
        """A killed writer cannot leave a live lease or half a poll cycle."""
        child = KILL_CHILD
        process = subprocess.Popen(
            [sys.executable, "-c", child, str(self.path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(
            lambda: process.kill() if process.poll() is None else None
        )
        self.assertEqual(process.stdout.readline().strip(), "COMMITTED")
        os.kill(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        with writer_lease(self.path):
            self.storage.add_phases(
                1050,
                {
                    "L1": (222, 50, 1),
                    "L2": (223, 50, 1),
                    "L3": (224, 50, 1),
                },
            )
        self.assertEqual(self.count(), 3)
        self.assertEqual(
            self.storage.get_meta("telegram_status_media_identity"),
            "photo:123",
        )


if __name__ == "__main__":
    unittest.main()
