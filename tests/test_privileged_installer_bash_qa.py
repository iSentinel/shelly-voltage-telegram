"""Validate privileged installer Bash entry and nested handoffs in QA."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"
ARCHIVE = ROOT / "scripts/source-tar.sh"


class InstallerPrivilegedBashTests(unittest.TestCase):
    """Never start real Docker or modify deployed files."""

    def setUp(self) -> None:
        """Create a private hostile startup file and synthetic services."""
        temp = tempfile.TemporaryDirectory(prefix="qa-installer-bash-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.marker = self.root / "injected"
        poison = self.root / "startup"
        poison.write_text('printf "injected" > "$QA_INSTALLER_MARKER"\n')
        self.env = {
            **os.environ,
            "BASH_ENV": str(poison),
            "QA_INSTALLER_MARKER": str(self.marker),
            "PATH": f"{self.bin_dir}:{os.environ.get('PATH', '')}",
        }

    def invoke(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Launch only a help or temporary synthetic Bash process."""
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=8,
        )

    def test_direct_installer_help_ignores_bash_env(self) -> None:
        """Kernel shebang uses privileged Bash before argument parsing."""
        self.assertEqual(
            INSTALLER.read_text().splitlines()[0],
            "#!/bin/bash -p",
        )
        result = self.invoke([str(INSTALLER), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: install.sh", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_archive_calls_use_protected_bash(self) -> None:
        """Both snapshot and source copy must fence Bash startup imports."""
        code = INSTALLER.read_text()
        for target in ("TARGET", "SOURCE"):
            expected = (
                '/bin/bash -p "$SOURCE/scripts/source-tar.sh" '
                f'"${target}" |'
            )
            self.assertIn(expected, code)

    def test_compose_calls_use_protected_bash(self) -> None:
        """Signed and unsigned call paths cannot bypass child shebang."""
        code = INSTALLER.read_text()
        self.assertIn('/bin/bash -p "$TARGET/scripts/start-compose.sh"', code)
        self.assertIn(
            '/bin/bash -p "$TARGET/scripts/start-compose.sh" --build',
            code,
        )
        self.assertNotRegex(
            code,
            r'(?m)^\s+bash "\$TARGET/scripts/start-compose.sh"',
        )

    def test_source_archive_helper_is_safe_when_called(self) -> None:
        """The existing helper is safe with the installer's interpreter."""
        result = self.invoke(["/bin/bash", "-p", str(ARCHIVE), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source-tar.sh", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_actual_deploy_function_uses_synthetic_compose(self) -> None:
        """Extract the real deploy body but use only inert fake commands."""
        code = INSTALLER.read_text()
        match = re.search(r"(?ms)^deploy\(\) \{\n.*?^\}", code)
        self.assertIsNotNone(match)
        scripts = self.root / "scripts"
        scripts.mkdir()
        compose = scripts / "start-compose.sh"
        compose.write_text("#!/bin/bash -p\nprintf 'FAKE_COMPOSE_OK\\n'\n")
        for name in ("systemctl", "curl"):
            command = self.bin_dir / name
            command.write_text("#!/bin/sh\nexit 0\n")
            command.chmod(0o755)
        shell = (
            "set -euo pipefail\n"
            + match.group(0)
            + '\nDRY_RUN=false\nSIGNED_DEPLOYMENT=false\nTARGET="$1"\n'
            + "deploy\n"
        )
        command = ["/bin/bash", "-p", "-c", shell, "qa", str(self.root)]
        result = self.invoke(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("FAKE_COMPOSE_OK", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_plain_bash_override_remains_unsafe(self) -> None:
        """Deliberately unsafe manual invocation remains a known risk."""
        result = self.invoke(["/bin/bash", str(INSTALLER), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
