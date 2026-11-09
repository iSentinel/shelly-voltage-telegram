"""Fail closed for a partial existing deployment without VERSION."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from test_install_target_path import dry_run


class InstallVersionMarkerTests(unittest.TestCase):
    """Protect old SQLite and source history during an upgrade."""

    def test_unversioned_project_content_is_rejected(self) -> None:
        """An unversioned existing install is not a fresh installation."""

        for name in ("compose.yaml", "app.py", "Dockerfile", "README.md"):
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                target = Path(directory) / "install"
                target.mkdir()
                (target / name).write_text("existing content")
                result = dry_run(str(target))
                self.assertEqual(result.returncode, 5)
                self.assertIn(b"missing VERSION", result.stderr)

    def test_unsafe_version_marker_is_rejected(self) -> None:
        """No symlink, hardlink or directory is an installed marker."""

        for kind in ("symlink", "hardlink", "directory"):
            with (
                self.subTest(kind=kind),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                target = root / "install"
                target.mkdir()
                marker = target / "VERSION"
                outside = root / "version"
                outside.write_text("0.5.13-hw")
                if kind == "symlink":
                    marker.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, marker)
                else:
                    marker.mkdir()
                result = dry_run(str(target))
                self.assertEqual(result.returncode, 5)

    def test_empty_and_preconfigured_new_installs_pass(self) -> None:
        """Allow fresh installs and existing standalone private config."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            new = root / "fresh"
            old = root / "old"
            config = root / "config"
            old.mkdir()
            config.mkdir()
            (old / "VERSION").write_text("0.5.13-hw")
            (config / ".env").write_text("configured")
            (config / ".env.example").write_text("example")
            (config / "secrets").mkdir()
            for target in (new, old, config):
                with self.subTest(target=target):
                    result = dry_run(str(target))
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_marker_guard_runs_before_writer_and_setup(self) -> None:
        """Fail before any Docker setup or snapshot can be skipped."""

        source = Path(__file__).resolve().parents[1]
        installer = (source / "scripts/install.sh").read_text()
        main = installer.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(
            "require_valid_install_marker\n    require_quiesced_upgrade",
            main,
        )


if __name__ == "__main__":
    unittest.main()
