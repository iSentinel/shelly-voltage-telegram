"""Prove frozen owner checks ignore hostile Python import variables."""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/legacy-rollback-frozen-owner-preflight.py"
UPDATER = ROOT / "scripts/apply-update.sh"
ROLLBACK = ROOT / "scripts/rollback.sh"


class IsolatedPythonGuardTests(unittest.TestCase):
    """Only disposable fake Docker/systemd and G inodes are used."""

    def setUp(self) -> None:
        """Create private PATH mocks and a visible import-poison marker."""
        temp = tempfile.TemporaryDirectory(prefix="qa-guard-python-i-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.poison = self.root / "poison"
        self.poison.mkdir(mode=0o700)
        self.marker = self.root / "poison-imported"
        (self.poison / "shutil.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['QA_PYTHON_POISON_MARKER']).write_text('BAD')\n"
        )
        self.gate = self.root / "gate"
        self.gate.mkdir(mode=0o700)
        self.g = self.gate / "G"
        self.g.touch()
        self.g.chmod(0o640)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.create_owner_mocks()
        self.env = {
            **os.environ,
            "SVT_ROOT": str(self.root / "missing-app"),
            "DATABASE_PATH": str(self.root / "missing.sqlite3"),
            "SVT_OWNER_GATE_DIR": str(self.gate),
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "PYTHONPATH": str(self.poison),
            "PYTHONHOME": str(self.root / "bogus-home"),
            "QA_PYTHON_POISON_MARKER": str(self.marker),
        }

    def create_owner_mocks(self) -> None:
        """Trusted fake programs report only fixed, stopped owner state."""
        for name, output in (
            ("docker", "false|no|0"),
            ("systemctl", "ActiveState=inactive\nUnitFileState=masked"),
        ):
            candidate = self.bin / name
            candidate.write_text(
                "#!/bin/sh\n"
                f"printf '%b\\n' {output!r}\n"
            )
            candidate.chmod(0o700)

    def invoke(
        self, command: list[str], inherited_fd: int | None = None
    ) -> subprocess.CompletedProcess:
        """Pass a real G EX FD through only when requested."""
        env = dict(self.env)
        descriptors: tuple[int, ...] = ()
        if inherited_fd is not None:
            env["SVT_MAINTENANCE_G_FD"] = str(inherited_fd)
            descriptors = (inherited_fd,)
        return subprocess.run(
            command,
            env=env,
            pass_fds=descriptors,
            capture_output=True,
            text=True,
            check=False,
            timeout=12,
        )

    def test_help_ignores_pythonpath_and_pythonhome(self) -> None:
        """The exact isolated interpreter accepts inert help safely."""
        result = self.invoke(["/usr/bin/python3", "-I", str(GUARD), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.marker.exists())

    def test_both_entrypoints_call_isolated_python(self) -> None:
        """No older unisolated owner preflight path may remain."""
        command = '/usr/bin/python3 -I "$guard" --check'
        for source in (UPDATER, ROLLBACK):
            body = source.read_text()
            self.assertIn(command, body)
            self.assertNotIn('python3 "$guard" --check', body.replace(
                command, ""
            ))

    @unittest.skipUnless(os.geteuid() == 0, "root-only inherited G QA")
    def test_updater_guard_blocks_poisoned_python(self) -> None:
        """No real update or imported hostile module is possible."""
        with self.g.open("rb") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.invoke(
                ["bash", str(UPDATER), "--version", "0.5.21-hw"],
                inherited_fd=fd.fileno(),
            )
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn("complete owner gate", result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "missing.sqlite3").exists())

    @unittest.skipUnless(os.geteuid() == 0, "root-only inherited G QA")
    def test_rollback_guard_blocks_poisoned_python(self) -> None:
        """A simulated restore still refuses before source or DB access."""
        with self.g.open("rb") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.invoke(
                ["bash", str(ROLLBACK), "--no-start"],
                inherited_fd=fd.fileno(),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "missing.sqlite3").exists())

    @unittest.skipIf(os.geteuid() == 0, "nonroot boot QA")
    def test_nonroot_rollback_still_fails(self) -> None:
        """No unprivileged caller can use the hardened rollback owner."""
        result = self.invoke(["bash", str(ROLLBACK), "--no-start"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
