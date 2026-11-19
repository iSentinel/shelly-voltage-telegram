"""Test updater marker CLI against the real SQLite storage gate."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from test_app import APP

from writer_fence import WriterFenceError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/writer-fence-marker.py"


class WriterFenceMarkerTests(unittest.TestCase):
    """Verify safe publication, idempotency, contention and cleanup."""

    def setUp(self) -> None:
        """Use only a temporary synthetic SQLite database."""
        temp = tempfile.TemporaryDirectory(prefix="svt-marker-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "history.sqlite3"
        self.storage = APP.Storage(self.path, "Europe/Kyiv")
        self.storage.initialize()
        self.storage.set_meta("telegram_status_media_identity", "photo:456")

    def command(self, action: str, token: str = "") -> list[str]:
        """Prepare a subprocess with only public local test data."""
        result = [
            sys.executable,
            str(SCRIPT),
            action,
            "--database",
            str(self.path),
        ]
        if token:
            result.extend(("--token", token))
        return result

    def invoke(
        self, action: str, token: str = ""
    ) -> subprocess.CompletedProcess:
        """Execute one real updater-marker operation."""
        return subprocess.run(
            self.command(action, token),
            check=False,
            capture_output=True,
            text=True,
            timeout=6,
        )

    def test_publish_blocks_storage_until_exact_clear(self) -> None:
        """Pending excludes all writes while retaining PHOTO state."""
        self.assertEqual(self.invoke("publish", "qa-11").returncode, 0)
        self.assertEqual(self.invoke("publish", "qa-11").returncode, 0)
        self.assertEqual(self.invoke("status").returncode, 3)
        with self.assertRaisesRegex(WriterFenceError, "SVT_UPDATE_PENDING"):
            self.storage.add_phases(1000, {"L1": (226, 50, 1)})
        self.assertEqual(self.invoke("clear", "wrong").returncode, 3)
        self.assertEqual(self.invoke("clear", "qa-11").returncode, 0)
        self.assertEqual(self.invoke("status").returncode, 0)
        self.storage.add_phases(1000, {"L1": (226, 50, 1)})
        self.assertEqual(
            self.storage.get_meta("telegram_status_media_identity"),
            "photo:456",
        )

    def test_conflicting_marker_refused(self) -> None:
        """No second token can overwrite an active update."""
        self.assertEqual(self.invoke("publish", "qa-1").returncode, 0)
        self.assertEqual(self.invoke("publish", "qa-2").returncode, 3)
        self.assertEqual(self.invoke("status").stdout.strip(), "PENDING")

    def test_unsafe_marker_refused(self) -> None:
        """A marker symlink cannot be read or overwritten."""
        (self.path.parent / ".svt-update-pending").symlink_to(self.path)
        self.assertEqual(self.invoke("publish", "qa-1").returncode, 3)
        self.assertEqual(self.invoke("status").returncode, 3)

    def test_publish_waits_for_real_storage_connection(self) -> None:
        """Updater marker cannot cross an outstanding SQLite gate."""
        with closing(self.storage.connect()):
            process = subprocess.Popen(
                self.command("publish", "qa-2"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(
                lambda: process.kill() if process.poll() is None else None
            )
            time.sleep(0.15)
            self.assertIsNone(process.poll())
        output, errors = process.communicate(timeout=6)
        self.assertEqual((process.returncode, errors), (0, ""), output)
        with self.assertRaisesRegex(WriterFenceError, "SVT_UPDATE_PENDING"):
            self.storage.set_meta("telegram_status_media_identity", "changed")


if __name__ == "__main__":
    unittest.main()
