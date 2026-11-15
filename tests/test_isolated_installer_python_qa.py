"""Block untrusted Python imports before installer guard checks."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install.sh"
TOOLS = (
    "installer-signed-image-guard.py",
    "validate-source-archive.py",
    "validate-sqlite-archive.py",
    "deployment-state-audit.py",
)


class InstallerIsolatedPythonTests(unittest.TestCase):
    """Run inert help and forged imports only, never a real installer."""

    def setUp(self) -> None:
        """Prepare an isolated untrusted stdlib module directory."""
        temp = tempfile.TemporaryDirectory(prefix="qa-installer-python-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.marker = self.root / "argparse-poison"
        (self.root / "argparse.py").write_text(
            "import os\nfrom pathlib import Path\n"
            'Path(os.environ["QA_ARGPARSE_MARKER"]).write_text("bad")\n'
        )
        self.env = {
            **os.environ,
            "PYTHONPATH": str(self.root),
            "QA_ARGPARSE_MARKER": str(self.marker),
        }

    def invoke(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Execute only an inert Python CLI with captured output."""
        return subprocess.run(
            args,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_all_installer_guards_use_python_isolation(self) -> None:
        """Five image/source/SQLite/audit calls must include -I."""
        source = INSTALLER.read_text()
        matches = re.findall(
            r'/usr/bin/python3 -I "\$(?:SOURCE|TARGET)/scripts/[^"]+"',
            source,
        )
        self.assertEqual(len(matches), 5)
        self.assertNotRegex(
            source,
            r'(?m)^[ \t]+python3 "\$(?:SOURCE|TARGET)/scripts/',
        )

    def test_each_cli_help_rejects_argparse_spoof(self) -> None:
        """Check actual gate imports without accessing host state."""
        for name in TOOLS:
            with self.subTest(script=name):
                path = ROOT / "scripts" / name
                command = ["/usr/bin/python3", "-I", str(path), "--help"]
                result = self.invoke(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())
                self.assertFalse(self.marker.exists())

    def test_isolated_stdin_import_ignores_pythonpath(self) -> None:
        """The chosen interpreter mode excludes poisoned module paths."""
        command = ["/usr/bin/python3", "-I", "-c", "import argparse"]
        result = self.invoke(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_unisolated_import_negative_control(self) -> None:
        """Show that a plain interpreter imports forged argparse."""
        result = self.invoke(["/usr/bin/python3", "-c", "import argparse"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
