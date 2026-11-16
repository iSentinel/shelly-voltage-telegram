"""Prove rollback validators use isolated Python before touching state."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLLBACK = ROOT / "scripts/rollback.sh"
GUARDS = (
    "validate-sqlite-archive.py",
    "validate-source-archive.py",
    "rollback-signed-image-guard.py",
    "graphic-rollback-media-guard.py",
    "deployment-state-audit.py",
)


class IsolatedRollbackPythonTests(unittest.TestCase):
    """Use only inert help and forged imports, never production rollback."""

    def setUp(self) -> None:
        """Create a disposable module injection fixture."""
        temporary = tempfile.TemporaryDirectory(prefix="qa-rollback-python-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.marker = self.root / "imported"
        (self.root / "argparse.py").write_text(
            "import os\nfrom pathlib import Path\n"
            'Path(os.environ["QA_ROLLBACK_POISON"]).write_text("bad")\n'
        )
        self.env = {
            **os.environ,
            "PYTHONPATH": str(self.root),
            "QA_ROLLBACK_POISON": str(self.marker),
        }

    def invoke(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """Capture a non-mutating test process."""
        return subprocess.run(
            args,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_all_rollback_python_calls_are_isolated(self) -> None:
        """All nine Python call sites must carry -I."""
        source = ROLLBACK.read_text()
        self.assertEqual(source.count("/usr/bin/python3 -I "), 9)
        for line in source.splitlines():
            if "python3" not in line:
                continue
            self.assertIn("/usr/bin/python3 -I ", line)
        self.assertNotRegex(source, r"(?m)(?<!/usr/bin/)\bpython3\s+['\"]")

    def test_isolated_cli_help_blocks_import_spoof(self) -> None:
        """Five real guard scripts must parse help without fake argparse."""
        for name in GUARDS:
            with self.subTest(guard=name):
                candidate = ROOT / "scripts" / name
                argv = ["/usr/bin/python3", "-I", str(candidate), "--help"]
                result = self.invoke(argv)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())
                self.assertFalse(self.marker.exists())

    def test_restore_inline_python_import_is_isolated(self) -> None:
        """The restore helper also reads stdin with Python -I."""
        source = ROLLBACK.read_text()
        self.assertIn('/usr/bin/python3 -I - "$RESTORE_TMP"', source)
        argv = ["/usr/bin/python3", "-I", "-c", "import argparse"]
        result = self.invoke(argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_plain_python_negative_control_imports_poison(self) -> None:
        """Unsafe manual interpreter imports forged argparse first."""
        argv = ["/usr/bin/python3", "-c", "import argparse"]
        result = self.invoke(argv)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
