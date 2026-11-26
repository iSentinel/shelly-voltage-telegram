"""Exercise the legacy systemd boot gate without contacting Docker."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

UPDATER = Path(__file__).resolve().parents[1] / "scripts/apply-update.sh"
FIXTURE = """
source "$1"
UPDATE_LOCK="$2"
start_stack() {
    if flock -n "$UPDATE_LOCK" true 9>&-; then
        echo "startup ran without the updater lock" >&2
        return 4
    fi
    printf '%s\n' "start with lock held"
}
guarded_start
"""


class GuardedStartTests(unittest.TestCase):
    """Keep the state check and Compose start in one lock scope."""

    def setUp(self) -> None:
        """Create a temporary private state directory."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.journal = self.state / "pending-update"

    def launch(self) -> subprocess.CompletedProcess:
        """Run the real boot check with a fixture in place of Compose."""
        return subprocess.run(
            [
                "bash",
                "-c",
                FIXTURE,
                "bash",
                str(UPDATER),
                str(self.root / "updater.lock"),
            ],
            env={**os.environ, "SVT_UPDATE_STATE": str(self.state)},
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )

    def test_start_holds_lock(self) -> None:
        """A clear state starts Compose while owning the update lock."""
        result = self.launch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "start with lock held")

    def test_pending_state_blocks_without_modifying_journal(self) -> None:
        """Files and broken symbolic links both deny autonomous startup."""
        self.journal.write_text("unfinished", encoding="utf-8")
        result = self.launch()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            self.journal.read_text(encoding="utf-8"), "unfinished"
        )
        self.journal.unlink()
        self.journal.symlink_to(self.root / "missing")
        result = self.launch()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertTrue(self.journal.is_symlink())

    def test_linked_state_directory_blocks(self) -> None:
        """A linked state path cannot make an unsafe start look clear."""
        self.state.rmdir()
        self.state.symlink_to(self.root / "missing", target_is_directory=True)
        result = self.launch()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")
