"""Safely exercise config Bash/Python startup with scratch-only state."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "scripts/configure.sh"


class IsolatedConfigureTests(unittest.TestCase):
    """Do not use any actual Telegram token or Shelly endpoint."""

    def setUp(self) -> None:
        """Create disposable configuration and poisoned import modules."""
        temp = tempfile.TemporaryDirectory(prefix="qa-configure-env-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = self.root / "app"
        self.app.mkdir()
        (self.app / ".env.example").write_text("SHELLY_HOST=\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        chown = self.bin / "chown"
        chown.write_text("#!/bin/sh\nexit 0\n")
        chown.chmod(0o755)
        self.bash_marker = self.root / "bash-poisoned"
        self.python_marker = self.root / "python-poisoned"
        poison = self.root / "startup"
        poison.write_text('printf "bad" > "$QA_BASH_MARKER"\n')
        imports = self.root / "imports"
        imports.mkdir()
        (imports / "pathlib.py").write_text(
            "import os\n"
            'open(os.environ["QA_PYTHON_MARKER"], "w").close()\n'
        )
        self.env = {
            **os.environ,
            "SVT_ROOT": str(self.app),
            "SVT_SHELLY_HOST": "192.0.2.42",
            "SVT_SHELLY_METER_TYPE": "pro_em50",
            "SVT_TELEGRAM_BOT_TOKEN": "",
            "SVT_TELEGRAM_CHAT_ID": "",
            "BASH_ENV": str(poison),
            "PYTHONPATH": str(imports),
            "QA_BASH_MARKER": str(self.bash_marker),
            "QA_PYTHON_MARKER": str(self.python_marker),
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
        }

    def invoke(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """Execute a synthetic configuration command in private paths."""
        return subprocess.run(
            command,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_direct_help_ignores_bash_env(self) -> None:
        """The direct executable opts into privileged Bash startup."""
        self.assertEqual(CONFIGURE.read_text().splitlines()[0], "#!/bin/bash -p")
        result = self.invoke([str(CONFIGURE), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.bash_marker.exists())

    def test_inline_python_is_isolated(self) -> None:
        """Settings serialization cannot import hostile pathlib."""
        self.assertIn(
            '/usr/bin/python3 -I - "$ENV_FILE"',
            CONFIGURE.read_text(),
        )

    def test_scratch_noninteractive_configuration(self) -> None:
        """Write fake settings without real secrets, services or network."""
        result = self.invoke([str(CONFIGURE), "--non-interactive"])
        self.assertEqual(result.returncode, 0, result.stderr)
        content = (self.app / ".env").read_text()
        self.assertIn("SHELLY_HOST=192.0.2.42", content)
        self.assertIn("SHELLY_METER_TYPE=pro_em50", content)
        self.assertTrue((self.app / "secrets").is_dir())
        self.assertFalse(self.bash_marker.exists())
        self.assertFalse(self.python_marker.exists())

    def test_plain_bash_negative_control(self) -> None:
        """Forced unprotected Bash is still unsafe and unsupported."""
        result = self.invoke(["/bin/bash", str(CONFIGURE), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.bash_marker.exists())


if __name__ == "__main__":
    unittest.main()
