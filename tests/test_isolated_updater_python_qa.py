"""Require isolated Python startup throughout updater and check service."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts/apply-update.sh"
CHECKER = ROOT / "scripts/github_update.py"
UNIT = ROOT / "systemd/shelly-voltage-update-check.service"


class UpdaterIsolatedPythonTests(unittest.TestCase):
    """Use only inert --help and temporary forged import modules."""

    def setUp(self) -> None:
        """Create a fake stdlib module in an untrusted import directory."""
        temp = tempfile.TemporaryDirectory(prefix="qa-updater-python-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        module = self.root / "argparse.py"
        module.write_text(
            "import os\nfrom pathlib import Path\n"
            'Path(os.environ["QA_PYTHON_POISON"]).write_text("bad")\n'
        )
        self.marker = self.root / "imported"
        self.env = {
            **os.environ,
            "PYTHONPATH": str(self.root),
            "QA_PYTHON_POISON": str(self.marker),
        }

    def invoke(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a scratch-only Python or inert updater help command."""
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=10,
        )

    def test_updater_github_cli_calls_are_isolated(self) -> None:
        """Preflight, storage, guard and digest use Python -I."""
        code = UPDATER.read_text()
        entries = re.findall(
            r'/usr/bin/python3 -I "\$ROOT/scripts/github_update.py"',
            code,
        )
        self.assertEqual(len(entries), 4)
        self.assertNotIn('python3 "$ROOT/scripts/github_update.py"', code)

    def test_inline_state_helpers_use_isolated_python(self) -> None:
        """Every embedded Python state helper excludes PYTHONPATH."""
        code = UPDATER.read_text()
        self.assertEqual(code.count("<<'PY'"), 3)
        lines = [line for line in code.splitlines() if "<<'PY'" in line]
        for line in lines:
            self.assertIn("/usr/bin/python3 -I - ", line)

    def test_dynamic_user_update_check_is_isolated(self) -> None:
        """Even the read-only timer must not import a forged stdlib."""
        expected = (
            "ExecStart=/usr/bin/python3 -I "
            "/opt/shelly-voltage/scripts/github_update.py check"
        )
        self.assertIn(expected, UNIT.read_text())

    def test_checker_help_ignores_forged_argparse(self) -> None:
        """Import isolation succeeds with hostile PYTHONPATH."""
        command = ["/usr/bin/python3", "-I", str(CHECKER), "--help"]
        result = self.invoke(command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage:", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_inline_python_ignores_forged_argparse(self) -> None:
        """The actual interpreter flags also protect stdin execution."""
        command = ["/usr/bin/python3", "-I", "-", "fixture"]
        result = subprocess.run(
            command,
            input="import argparse\nprint('SYNTHETIC_OK')\n",
            check=False,
            capture_output=True,
            text=True,
            env=self.env,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNTHETIC_OK", result.stdout)
        self.assertFalse(self.marker.exists())

    def test_plain_python_negative_control_imports_poison(self) -> None:
        """Without -I, a supplied argparse module executes first."""
        result = self.invoke(["/usr/bin/python3", str(CHECKER), "--help"])
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
