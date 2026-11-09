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
            "require_safe_install_target\n    require_safe_install_files",
            main,
        )

    def test_reject_protected_environment_links(self) -> None:
        """Symlink or hard link to an external .env must fail preflight."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "installed"
            target.mkdir()
            other = root / "external-env"
            other.write_text("safe")
            linked = target / ".env"
            linked.symlink_to(other)
            self.assertNotEqual(dry_run(str(target)).returncode, 0)
            linked.unlink()
            os.link(other, linked)
            self.assertNotEqual(dry_run(str(target)).returncode, 0)
            self.assertEqual(other.read_text(), "safe")

    def test_reject_linked_secrets_directory(self) -> None:
        """Do not chmod or chown an unrelated secrets directory."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "installed"
            target.mkdir()
            outside = root / "elsewhere"
            outside.mkdir()
            (target / "secrets").symlink_to(outside, target_is_directory=True)
            result = dry_run(str(target))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b"unsafe installation secrets", result.stderr)

    def test_reject_linked_secret_files(self) -> None:
        """Do not touch, chown or chmod external secret file inodes."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "installed"
            secrets = target / "secrets"
            secrets.mkdir(parents=True)
            outside = root / "external-secret"
            outside.write_text("preserved")
            token = secrets / "telegram_token"
            token.symlink_to(outside)
            self.assertNotEqual(dry_run(str(target)).returncode, 0)
            token.unlink()
            os.link(outside, token)
            self.assertNotEqual(dry_run(str(target)).returncode, 0)
            self.assertEqual(outside.read_text(), "preserved")

    def test_regular_protected_files_are_accepted(self) -> None:
        """Existing standard private files do not block dry run."""

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "installed"
            secrets = target / "secrets"
            secrets.mkdir(parents=True)
            (target / ".env").write_text("present")
            for name in ("telegram_token", "shelly_password"):
                (secrets / name).write_text("present")
            result = dry_run(str(target))
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
