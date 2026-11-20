"""Never rewind live production source or SQLite with legacy rollback."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/rollback.sh"
LIVE_DB = "/var/lib/shelly-voltage/voltage.sqlite3"
LIVE_ROOT = "/opt/shelly-voltage"


class LegacyProductionRollbackGuardTests(unittest.TestCase):
    """Exercise only guarded shell functions using isolated QA paths."""

    def setUp(self) -> None:
        """Prepare a private directory and no production fixture files."""
        temporary = tempfile.TemporaryDirectory(prefix="svt-qa-old-rollback-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.log = self.root / "actions"
        self.env = dict(os.environ)
        self.env.pop("SVT_SUPERVISOR_OVERLAY", None)
        self.env.pop("SVT_WRITER_FENCE_DIR", None)
        source = SCRIPT.read_text(encoding="utf-8")
        assert source.rstrip().endswith('main "$@"')
        self.library = self.root / "rollback-functions.sh"
        self.library.write_text(
            source.rsplit('main "$@"', 1)[0], encoding="utf-8"
        )

    def sourced(self, action: str, db: str, target: str):
        """Never run an unmocked Docker operation or touch live paths."""
        shell = (
            'source "$1"; DB="$2"; TARGET="$3"; '
            'docker() { echo docker >> "$ACTION_LOG"; }; ' + action
        )
        return subprocess.run(
            ["bash", "-c", shell, "bash", str(self.library), db, target],
            env=dict(self.env, ACTION_LOG=str(self.log)),
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )

    def test_actual_production_rollback_blocked_before_snapshot_reads(self):
        """An explicit --confirm-db-replacement cannot override safety."""
        result = subprocess.run(
            [
                "bash",
                str(SCRIPT),
                "--confirm-db-replacement",
                "--no-start",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn(
            "production source/SQLite rollback blocked", result.stderr
        )

    def test_direct_destructive_operations_refused(self) -> None:
        """No sourced legacy helper may stop or overwrite the live writer."""
        for function in (
            "stop_service",
            "restore_source",
            "restore_database",
            "rescue_current_db",
            "start_service",
        ):
            with self.subTest(function=function):
                result = self.sourced(function, LIVE_DB, LIVE_ROOT)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("production source/SQLite", result.stderr)
                self.assertFalse(self.log.exists())

    def test_production_source_with_custom_database_still_blocked(self):
        """Switching the DB parameter cannot allow wiping live sources."""
        result = self.sourced(
            "restore_source", str(self.root / "custom.sqlite3"), LIVE_ROOT
        )
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.log.exists())

    def test_production_database_with_custom_source_still_blocked(self):
        """Switching the source parameter cannot allow a live WAL rewind."""
        result = self.sourced(
            "restore_database", LIVE_DB, str(self.root / "source")
        )
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.log.exists())

    def test_parent_hop_alias_is_not_an_escape(self) -> None:
        """Canonical source/database aliases are still production paths."""
        source = "/opt/../opt/shelly-voltage"
        database = "/var/lib/shelly-voltage/../shelly-voltage/voltage.sqlite3"
        result = self.sourced("restore_database", database, source)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(self.log.exists())

    def test_disposable_source_and_db_stay_eligible(self) -> None:
        """QA tests may still restore private synthetic fixtures."""
        source = str(self.root / "source")
        database = str(self.root / "data.sqlite3")
        result = self.sourced(
            "legacy_production_rollback_guard", database, source
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_still_available(self) -> None:
        """No runtime state or elevated resources are needed for --help."""
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
