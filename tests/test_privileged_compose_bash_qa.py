"""Ensure Compose start/reload Bash ignores injected startup files."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "systemd/shelly-voltage-compose.service"


class ComposePrivilegedBashTests(unittest.TestCase):
    """Use disposable Compose entrypoints, never a live Docker daemon."""

    def setUp(self) -> None:
        """Prepare an isolated script with poisoned environment."""
        temp = tempfile.TemporaryDirectory(prefix="qa-compose-bash-p-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.marker = self.root / "sourced-startup"
        self.fake_marker = self.root / "fake-command-executed"
        poison = self.root / "startup"
        poison.write_text(
            'printf injected > "$QA_COMPOSE_ENV_MARKER"\n'
        )
        fake = self.bin / "bash"
        fake_line = 'printf intercepted > "$QA_COMPOSE_FAKE_MARKER"\n'
        fake.write_text("#!/bin/sh\n" + fake_line + "exit 71\n")
        fake.chmod(0o700)
        docker = self.bin / "docker"
        docker.write_text("#!/bin/sh\nexit 79\n")
        docker.chmod(0o700)
        self.script = self.root / "start-compose.sh"
        shutil.copyfile(ROOT / "scripts/start-compose.sh", self.script)
        self.script.chmod(0o700)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "BASH_ENV": str(poison),
            "QA_COMPOSE_ENV_MARKER": str(self.marker),
            "QA_COMPOSE_FAKE_MARKER": str(self.fake_marker),
            "SVT_ROOT": str(self.root / "nonexistent-app"),
        }

    def execute(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run only help or fail-closed build in synthetic scratch."""
        return subprocess.run(
            args,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )

    def assert_clean(self) -> None:
        """Reject both Bash startup sourcing and PATH Bash replacement."""
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.fake_marker.exists())

    def test_compose_direct_help_ignores_poison(self) -> None:
        """Direct protected entrypoint may not import BASH_ENV."""
        result = self.execute([str(self.script), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: start-compose.sh", result.stdout)
        self.assert_clean()

    def test_compose_unsigned_build_stays_blocked(self) -> None:
        """A forbidden build returns before Docker or Python gate calls."""
        result = self.execute([str(self.script), "--build"])
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("unsigned Docker build is forbidden", result.stderr)
        self.assert_clean()

    def test_systemd_start_and_reload_use_privileged_bash(self) -> None:
        """Never call the protected shebang with unsafe explicit Bash."""
        for action in ("ExecStart", "ExecReload"):
            expected = (
                f"{action}=/bin/bash -p "
                "/opt/shelly-voltage/scripts/start-compose.sh"
            )
            self.assertIn(expected, UNIT.read_text())
        shell = (ROOT / "scripts/start-compose.sh").read_text()
        self.assertTrue(shell.startswith("#!/bin/bash -p\n"))

    def test_systemd_style_help_ignores_poison(self) -> None:
        """An explicit systemd interpreter keeps the same safe mode."""
        args = ["/bin/bash", "-p", str(self.script), "--help"]
        result = self.execute(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_clean()

    def test_unprotected_manual_bash_still_injects(self) -> None:
        """An untrusted caller bypassing the shebang remains unsafe."""
        args = ["/bin/bash", str(self.script), "--help"]
        result = self.execute(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.fake_marker.exists())


if __name__ == "__main__":
    unittest.main()
