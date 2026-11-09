"""Preserve optional YASNO Compose overlays across boot and upgrade."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/start-compose.sh"


class StartComposeYasnoTests(unittest.TestCase):
    """Verify the protected overlay is opt-in and stable across modes."""

    def setUp(self):
        """Create a disposable application with no real Docker access."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.app.mkdir(mode=0o700)
        (self.app / "compose.yaml").write_text("services: {}\n")
        self.mockbin = self.root / "bin"
        self.mockbin.mkdir(mode=0o700)
        self.calls = self.root / "calls.txt"
        docker = self.mockbin / "docker"
        docker.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMPOSE_CALLS"\n'
        )
        docker.chmod(0o700)
        self.overlay = self.root / "yasno.yaml"
        self.updater = self.root / "updater.yaml"

    def run_script(self, *args):
        """Invoke the real script with an offline fake Docker command."""
        env = dict(os.environ)
        env.update(
            SVT_ROOT=str(self.app),
            SVT_YASNO_READER_OVERLAY=str(self.overlay),
            SVT_UPDATE_OVERRIDE=str(self.updater),
            COMPOSE_CALLS=str(self.calls),
            PATH=str(self.mockbin) + os.pathsep + env["PATH"],
        )
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        calls = (
            self.calls.read_text().splitlines() if self.calls.exists() else []
        )
        return result, [shlex.split(row) for row in calls]

    def enable(self):
        """Create a private owner-controlled overlay fixture."""
        self.overlay.write_text("services: {}\n")
        self.overlay.chmod(0o600)

    def test_no_overlay_keeps_original_base_service(self):
        """Do not enable forecast by merely installing this version."""
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        args = ["compose", "-f", str(self.app / "compose.yaml")]
        self.assertEqual(calls, [args + ["config", "-q"], args + ["up", "-d"]])

    def test_owner_overlay_appends_last_for_regular_start(self):
        """The approved YASNO layer is opt-in and overrides the base."""
        self.enable()
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls[0][-4:], ["-f", str(self.overlay), "config", "-q"]
        )
        self.assertEqual(calls[1][-3:], [str(self.overlay), "up", "-d"])

    def test_updater_image_does_not_shadow_opted_in_yasno(self):
        """Keep the approved overlay AFTER the update image settings."""
        self.enable()
        self.updater.write_text("services: {}\n")
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(
            calls[0].index(str(self.updater)),
            calls[0].index(str(self.overlay)),
        )
        self.assertEqual(calls[1][-3:], ["up", "-d", "--no-build"])

    def test_install_build_uses_yasno_and_ignores_old_updater(self):
        """Upgrading source must not silently drop the protected overlay."""
        self.enable()
        self.updater.write_text("services: {}\n")
        result, calls = self.run_script("--build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(str(self.updater) not in call for call in calls))
        self.assertTrue(all(str(self.overlay) in call for call in calls))
        self.assertEqual(calls[1][-3:], ["up", "-d", "--build"])

    def test_symlink_refused_before_docker(self):
        """Never follow untrusted symlink configuration files."""
        real = self.root / "real.yml"
        real.write_text("services: {}\n")
        self.overlay.symlink_to(real)
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_world_writable_overlay_refused(self):
        """No group/world write access to the privileged Docker config."""
        self.enable()
        self.overlay.chmod(0o666)
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_hardlinked_overlay_refused(self):
        """Avoid shared overlays that can be swapped through another name."""
        self.enable()
        os.link(self.overlay, self.root / "other-link")
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_writable_parent_refused(self):
        """Reject unsafe directories even when the file itself is private."""
        self.enable()
        self.root.chmod(0o777)
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_symlinked_grandparent_refused(self):
        """Do not resolve an attacker-controlled ancestral directory."""
        real = self.root / "real"
        real.mkdir(mode=0o700)
        nested = real / "safe"
        nested.mkdir(mode=0o700)
        file = nested / "yasno.yaml"
        file.write_text("services: {}\\n")
        file.chmod(0o600)
        (self.root / "alias").symlink_to(real, target_is_directory=True)
        self.overlay = self.root / "alias" / "safe" / "yasno.yaml"
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_world_writable_grandparent_refused(self):
        """Permissions of grandparents count, not only direct parent."""
        parent = self.root / "unsafe"
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        safe = parent / "safe"
        safe.mkdir(mode=0o700)
        self.overlay = safe / "yasno.yaml"
        self.enable()
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_noncanonical_parent_traversal_refused(self):
        """Never permit a path that traverses a parent component."""
        safe = self.root / "safe"
        safe.mkdir(mode=0o700)
        self.enable()
        self.overlay = safe / ".." / "yasno.yaml"
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_nested_private_overlay_allowed(self):
        """Protected directories remain supported for production layouts."""
        safe = self.root / "parent" / "child"
        safe.mkdir(parents=True, mode=0o700)
        self.overlay = safe / "yasno.yaml"
        self.enable()
        result, calls = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(str(self.overlay) in call for call in calls))

    def test_help_is_noninteractive(self):
        """Expose usage without any access to Docker."""
        result, calls = self.run_script("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--build", result.stdout)
        self.assertEqual(calls, [])

    def test_bad_argument_is_rejected(self):
        """Never accept unknown mutation modes."""
        result, calls = self.run_script("--unexpected")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
