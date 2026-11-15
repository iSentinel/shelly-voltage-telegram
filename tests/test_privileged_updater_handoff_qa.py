"""Protect systemd updater invocation and its internal Compose handoff."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/apply-update.sh"
UNIT = ROOT / "systemd/shelly-voltage-updater-apply@.service"


class UpdaterPrivilegedHandoffTests(unittest.TestCase):
    """Run a synthetic child script, never a Docker or SQLite writer."""

    def setUp(self) -> None:
        """Prepare private poisoned environment and harmless fake Compose."""
        temp = tempfile.TemporaryDirectory(prefix="qa-updater-bash-handoff-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(mode=0o700)
        self.updater = self.scripts / "apply-update.sh"
        shutil.copyfile(UPDATER, self.updater)
        self.updater.chmod(0o700)
        child = self.scripts / "start-compose.sh"
        child.write_text("#!/bin/bash -p\nprintf 'SYNTHETIC_CHILD_OK\\n'\n")
        child.chmod(0o700)
        self.marker = self.root / "unsafe-shell-sourced"
        poison = self.root / "injected-startup"
        poison.write_text('printf "injected" > "$QA_UPDATER_POISON_MARKER"\n')
        self.env = {
            **os.environ,
            "SVT_ROOT": str(self.root),
            "BASH_ENV": str(poison),
            "QA_UPDATER_POISON_MARKER": str(self.marker),
        }

    def invoke(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run only inert help or private synthetic handoff."""
        return subprocess.run(
            args,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )

    def test_systemd_updater_uses_privileged_shell(self) -> None:
        """The versioned systemd updater must not bypass the shebang."""
        expected = (
            "ExecStart=/bin/bash -p "
            "/opt/shelly-voltage/scripts/apply-update.sh --version %i"
        )
        self.assertIn(expected, UNIT.read_text())

    def test_updater_help_ignores_inherited_bash_env(self) -> None:
        """Safe explicit unit interpreter allows inert help."""
        args = ["/bin/bash", "-p", str(self.updater), "--help"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_inner_compose_start_is_privileged(self) -> None:
        """The internal handoff never reintroduces Bash startup imports."""
        script = UPDATER.read_text()
        expected = '/bin/bash -p "$ROOT/scripts/start-compose.sh"'
        self.assertIn(expected, script)
        command = 'source "$1"; start_stack'
        args = ["/bin/bash", "-p", "-c", command, "qa", str(self.updater)]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNTHETIC_CHILD_OK", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_forced_unprotected_shell_is_still_out_of_scope(self) -> None:
        """A caller selecting plain Bash remains a separate risk."""
        args = ["/bin/bash", str(self.updater), "--help"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
