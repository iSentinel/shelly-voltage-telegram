"""Do not let old updater rewind current production SQLite/WAL state."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/apply-update.sh"
PRODUCTION_DB = "/var/lib/shelly-voltage/voltage.sqlite3"


class LegacyProductionRewindGuardTests(unittest.TestCase):
    """Call sourced functions with fake Docker and private pending fixtures."""

    def setUp(self) -> None:
        """Use only temporary marker and source paths."""
        temp = tempfile.TemporaryDirectory(prefix="svt-qa-legacy-rewind-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.pending = self.state / "pending-update"
        self.pending.write_text("20261120T010000Z-0.5.26-hw\n")
        self.actions = self.root / "docker-calls"
        self.env = dict(os.environ)
        self.env.pop("SVT_SUPERVISOR_OVERLAY", None)
        self.env.pop("SVT_WRITER_FENCE_DIR", None)
        # Explicitly bind the subprocess to the production-path guard.
        # Other unittest modules may set DATABASE_PATH for QA fixtures.
        self.env["DATABASE_PATH"] = PRODUCTION_DB

    def sourced(
        self, action: str, database: str
    ) -> subprocess.CompletedProcess:
        """Use a fake Docker shell, never a real production service."""
        script = (
            'source "$1"; DB="$2"; STATE="$3"; '
            'PENDING_FILE="$STATE/pending-update"; '
            'docker() { printf "docker\n" >> "$ACTION_LOG"; }; ' + action
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "bash",
                str(SCRIPT),
                database,
                str(self.state),
            ],
            env=dict(self.env, ACTION_LOG=str(self.actions)),
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )

    def test_normal_boot_without_pending_is_unaffected(self) -> None:
        """An ordinary no-pending startup must not be blocked."""
        self.pending.unlink()
        result = self.sourced("recover_pending", PRODUCTION_DB)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.actions.exists())

    def test_production_pending_recovery_refused_before_docker(self) -> None:
        """Do not stop the writer or replace newest WAL after interruption."""
        result = self.sourced("recover_pending", PRODUCTION_DB)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("production SQLite rewind", result.stderr)
        self.assertFalse(self.actions.exists())
        self.assertTrue(self.pending.is_file())

    def test_direct_old_database_restore_removed(self) -> None:
        """Sourcing the updater exposes no old SQLite restore function."""
        result = self.sourced(
            "declare -F restore_snapshot_database", PRODUCTION_DB
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.actions.exists())

    def test_direct_legacy_activation_refused(self) -> None:
        """Do not create a pending production upgrade with old rollback."""
        result = self.sourced("activate sha256:fixture", PRODUCTION_DB)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.actions.exists())
        self.assertTrue(self.pending.is_file())

    def test_old_version_install_refuses_before_dependencies(self) -> None:
        """Do not initiate a production update with unsafe recovery hooks."""
        result = subprocess.run(
            ["bash", str(SCRIPT), "--version", "0.5.26-hw"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("production SQLite rewind", result.stderr)

    def test_production_relative_segments_are_normalized(self) -> None:
        """Aliases with a parent hop cannot bypass the default DB guard."""
        path = "/var/lib/shelly-voltage/../shelly-voltage/voltage.sqlite3"
        result = self.sourced("recover_pending", path)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.actions.exists())

    def test_private_disposable_database_remains_testable(self) -> None:
        """QA databases still use the isolated legacy test harness."""
        local = str(self.root / "database.sqlite3")
        result = self.sourced("legacy_production_db_guard", local)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.actions.exists())


if __name__ == "__main__":
    unittest.main()
