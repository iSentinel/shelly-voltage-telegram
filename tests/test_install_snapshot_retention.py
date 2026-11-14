"""Keep historical source+SQLite snapshots across every source upgrade."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"


def isolated_dry_run(root: Path, target: Path, backups: Path):
    """Exercise all dry-run stages without requiring production root."""
    functions = root / "install-functions.sh"
    source = INSTALLER.read_text()
    assert source.rstrip().endswith('main "$@"')
    functions.write_text(source.rsplit('main "$@"', 1)[0])
    harness = (
        'source "$1"; require_root() { :; }; main --dry-run --non-interactive'
    )
    env = os.environ | {
        "SVT_ROOT": str(target),
        "UPGRADE_SNAPSHOT_ROOT": str(backups),
    }
    return subprocess.run(
        ["bash", "-c", harness, "bash", str(functions)],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
        check=False,
    )


class UpgradeSnapshotRetentionTests(unittest.TestCase):
    """Ensure installer does not silently remove old verified snapshots."""

    def test_snapshot_stage_does_not_prune_any_snapshot(self) -> None:
        """Snapshot creation may add but never delete recovery history."""
        script = INSTALLER.read_text()
        stage = script.split("snapshot_upgrade() {", 1)[1].split(
            "\ncopy_source() {", 1
        )[0]
        self.assertIn('bash "$SOURCE/scripts/source-tar.sh"', stage)
        self.assertIn("validate-sqlite-archive.py", stage)
        self.assertIn('echo "Upgrade snapshot:', stage)
        for operation in (
            "prune_upgrade_snapshots",
            "rm -rf",
            "rm -r ",
            "find -delete",
        ):
            with self.subTest(operation=operation):
                self.assertNotIn(operation, stage)

    def test_no_implicit_destructive_prune_function(self) -> None:
        """Nothing in installer should delete whole snapshot directories."""
        script = INSTALLER.read_text()
        self.assertNotIn("prune_upgrade_snapshots()", script)
        self.assertNotIn("rm -rf --", script)

    def test_dry_run_preserves_many_existing_snapshots(self) -> None:
        """Plan source upgrade with 12 protected historical snapshot files."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            backups = root / "snapshots"
            target.mkdir()
            backups.mkdir(mode=0o700)
            (target / "VERSION").write_text("0.5.15-hw\n")
            old_paths = []
            for n in range(12):
                archive = backups / f"20261113-2000{n:02d}-old-{n:08d}"
                archive.mkdir(mode=0o700)
                marker = archive / "retained.txt"
                marker.write_text(f"evidence-{n}\n")
                old_paths.append(marker)
            result = isolated_dry_run(root, target, backups)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DRY-RUN PASS", result.stdout)
            for n, marker in enumerate(old_paths):
                self.assertEqual(marker.read_text(), f"evidence-{n}\n")


if __name__ == "__main__":
    unittest.main()
