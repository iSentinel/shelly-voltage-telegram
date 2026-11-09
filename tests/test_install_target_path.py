"""Reject install destinations redirected by links or path traversal."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dry_run(target: str) -> subprocess.CompletedProcess[bytes]:
    """Run complete installer dry-run with isolated privilege stub."""

    script = (ROOT / "scripts/install.sh").read_text()
    assert script.rstrip().endswith('main "$@"')
    with tempfile.TemporaryDirectory() as name:
        functions = Path(name) / "install-functions.sh"
        functions.write_text(script.rsplit('main "$@"', 1)[0])
        command = (
            'source "$1"; require_root() { :; }; '
            "main --dry-run --non-interactive"
        )
        return subprocess.run(
            ["bash", "-c", command, "_", str(functions)],
            env=os.environ | {"SVT_ROOT": target},
            capture_output=True,
            check=False,
            timeout=12,
        )


class InstallerTargetPathTests(unittest.TestCase):
    """Do not copy source into unexpected installation destinations."""

    def test_refuse_unsafe_targets_before_any_install(self) -> None:
        """Reject aliases, root, relative paths and traversal."""

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            actual = home / "real"
            actual.mkdir()
            (actual / "VERSION").write_text("0.5.13-hw\n")
            alias = home / "alias"
            alias.symlink_to(actual)
            parent_alias = home / "parent-alias"
            parent_alias.symlink_to(home, target_is_directory=True)
            bad_file = home / "file-target"
            bad_file.write_text("not a directory")
            paths = (
                str(alias),
                str(parent_alias / "real"),
                str(actual / ".." / "real"),
                str(bad_file),
                str(home / "real") + "/",
                "/",
                ".",
            )
            for path in paths:
                with self.subTest(path=path):
                    result = dry_run(path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(b"unsafe application target", result.stderr)
            self.assertEqual((actual / "VERSION").read_text(), "0.5.13-hw\n")

    def test_valid_new_and_existing_targets_pass_dry_run(self) -> None:
        """Do not break normal install and upgrade planning."""

        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            old = home / "installed"
            old.mkdir()
            (old / "VERSION").write_text("0.5.13-hw\n")
            for path in (old, home / "new-install"):
                with self.subTest(path=path):
                    result = dry_run(str(path))
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_guard_runs_before_installer_actions(self) -> None:
        """Ensure validation precedes package setup and source copying."""

        script = (ROOT / "scripts/install.sh").read_text()
        main = script.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(
            "require_safe_install_target\n    require_quiesced_upgrade",
            main,
        )


if __name__ == "__main__":
    unittest.main()
