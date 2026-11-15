"""Verify Bash bootstrap and pinned-release entrypoints are protected."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = (
    ROOT / "scripts/bootstrap.sh",
    ROOT / "scripts/install-release.sh",
)


class ProtectedBootstrapReleaseTests(unittest.TestCase):
    """Never download an archive or run a real installer."""

    def setUp(self) -> None:
        """Create disposable startup poison and marker."""
        temp = tempfile.TemporaryDirectory(prefix="qa-entry-bash-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.marker = self.root / "startup-injected"
        poison = self.root / "bash-env"
        poison.write_text('printf "bad" > "$QA_ENTRY_MARKER"\n')
        self.env = {
            **os.environ,
            "BASH_ENV": str(poison),
            "QA_ENTRY_MARKER": str(self.marker),
        }

    def invoke(
        self, args: list[str], stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Execute only --help with captured standard input."""
        return subprocess.run(
            args,
            input=stdin,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_direct_shebang_help_is_protected(self) -> None:
        """Executable files must use privileged Bash before parsing."""
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                self.assertEqual(
                    script.read_text().splitlines()[0], "#!/bin/bash -p"
                )
                result = self.invoke([str(script), "--help"])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)
                self.assertFalse(self.marker.exists())

    def test_piped_help_requires_privileged_interpreter(self) -> None:
        """Explicit -p is required because a pipe ignores shebangs."""
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                command = ["/bin/bash", "-p", "-s", "--", "--help"]
                result = self.invoke(command, script.read_text())
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage:", result.stdout)
                self.assertFalse(self.marker.exists())

    def test_documented_pipes_use_privileged_bash(self) -> None:
        """Future installation examples must not recommend plain Bash."""
        for script in SCRIPTS:
            with self.subTest(script=script.name):
                content = script.read_text()
                self.assertIn("/bin/bash -p -s -- --non-interactive", content)
                self.assertNotIn("| sudo bash", content)

    def test_plain_piped_bash_negative_control(self) -> None:
        """A forced unprotected shell remains a known unsafe path."""
        script = SCRIPTS[0]
        result = self.invoke(
            ["/bin/bash", "-s", "--", "--help"],
            script.read_text(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
