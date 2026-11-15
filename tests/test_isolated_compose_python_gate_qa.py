"""Synthetic import poisoning regression for gated Compose admission."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "scripts/start-compose.sh"
GUARD = ROOT / "scripts/legacy-compose-start-gate.py"


class ComposePythonIsolationTests(unittest.TestCase):
    """Only test a fake permit and private, nonexistent Docker target."""

    def setUp(self) -> None:
        """Prepare root-independent private Compose and import-poison files."""
        temp = tempfile.TemporaryDirectory(prefix="qa-compose-python-i-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir(mode=0o700)
        self.candidate = self.scripts / "start-compose.sh"
        shutil.copyfile(COMPOSE, self.candidate)
        self.candidate.chmod(0o700)
        shutil.copyfile(GUARD, self.scripts / GUARD.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.override = self.state / "override.yaml"
        self.override.write_text("services:\n  shelly-voltage: {}\n")
        self.poison = self.root / "python-poison"
        self.poison.mkdir(mode=0o700)
        self.marker = self.root / "fake-argparse-imported"
        code = (
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['QA_COMPOSE_IMPORT_MARKER']).write_text('BAD')\n"
        )
        (self.poison / "argparse.py").write_text(code)
        self.env = {
            **os.environ,
            "SVT_ROOT": str(self.root),
            "SVT_UPDATE_OVERRIDE": str(self.override),
            "SVT_YASNO_READER_OVERLAY": str(self.root / "absent-yasno"),
            "SVT_OWNER_GATE_DIR": str(self.root / "missing-G"),
            "DATABASE_PATH": str(self.root / "nonexistent.sqlite3"),
            "PYTHONPATH": str(self.poison),
            "PYTHONHOME": str(self.root / "invalid-python-home"),
            "QA_COMPOSE_IMPORT_MARKER": str(self.marker),
        }

    def call(self, env: dict | None = None) -> subprocess.CompletedProcess:
        """Run gated entrypoint with no live permit or Docker activation."""
        return subprocess.run(
            [str(self.candidate)],
            env=env or self.env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

    def test_candidate_uses_isolated_python(self) -> None:
        """Child gate interpreter is absolute and import-isolated."""
        source = COMPOSE.read_text()
        command = '/usr/bin/python3 -I "$ROOT/scripts/'
        self.assertIn(command + 'legacy-compose-start-gate.py"', source)
        self.assertLess(source.index(command), source.index("compose_cmd config"))

    def test_untrusted_pythonpath_cannot_impersonate_gate(self) -> None:
        """Invalid G must refuse without hostile argparse import."""
        result = self.call()
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("COMPOSE_ADMISSION_DENIED", result.stdout)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "nonexistent.sqlite3").exists())

    def test_missing_pythonhome_does_not_change_outcome(self) -> None:
        """Python -I also ignores a bogus inherited PYTHONHOME."""
        clean = dict(self.env)
        clean.pop("PYTHONHOME", None)
        result = self.call(clean)
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_legacy_unisolated_gate_imports_poison(self) -> None:
        """Reproduce original defect using a temporary unsafe copy."""
        before = '/usr/bin/python3 -I "$ROOT/scripts/'
        after = 'python3 "$ROOT/scripts/'
        unsafe = self.candidate.read_text().replace(before, after)
        self.candidate.write_text(unsafe)
        no_home = dict(self.env)
        no_home.pop("PYTHONHOME", None)
        result = self.call(no_home)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.marker.exists())

    def test_help_is_inert(self) -> None:
        """Help must not inspect G or load Python modules."""
        result = subprocess.run(
            [str(self.candidate), "--help"],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
