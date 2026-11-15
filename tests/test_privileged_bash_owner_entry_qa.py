"""Synthetic Bash startup injection QA for owner entrypoints."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "systemd/shelly-voltage-compose.service"
SCRIPT_NAMES = ("apply-update.sh", "rollback.sh")


class BashEntrypointSafetyTests(unittest.TestCase):
    """Exercise only private script copies; never use production state."""

    def setUp(self) -> None:
        """Prepare a hostile startup environment in disposable scratch."""
        temp = tempfile.TemporaryDirectory(prefix="qa-bash-privilege-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.poison = self.root / "startup"
        self.marker = self.root / "startup-was-sourced"
        self.fake_marker = self.root / "fake-bash-used"
        self.poison.write_text(
            'printf "injected" > "$QA_BASH_ENV_MARKER"\n'
        )
        fake = self.bin / "bash"
        fake.write_text(
            '#!/bin/sh\nprintf "fake" > "$QA_BASH_PATH_MARKER"\n'
            "exit 49\n"
        )
        fake.chmod(0o700)
        self.env = {
            **os.environ,
            "BASH_ENV": str(self.poison),
            "ENV": str(self.poison),
            "QA_BASH_ENV_MARKER": str(self.marker),
            "QA_BASH_PATH_MARKER": str(self.fake_marker),
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "SVT_ROOT": str(self.root / "missing-app"),
            "SVT_UPDATE_STATE": str(self.root / "updater-state"),
            "DATABASE_PATH": str(self.root / "missing.sqlite3"),
        }

    def copy_entrypoint(self, name: str) -> Path:
        """Only local copied shell scripts are made executable."""
        source = ROOT / "scripts" / name
        destination = self.root / name
        shutil.copyfile(source, destination)
        destination.chmod(0o700)
        return destination

    def invoke(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run inert commands with injected Bash startup variables."""
        return subprocess.run(
            args,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=8,
        )

    def assert_no_poison(self) -> None:
        """Neither environment startup nor PATH alias may execute."""
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.fake_marker.exists())
        self.assertFalse((self.root / "missing.sqlite3").exists())

    def test_direct_updater_help_ignores_injection(self) -> None:
        """Kernel shebang must ignore both BASH_ENV and fake PATH bash."""
        result = self.invoke(
            [str(self.copy_entrypoint("apply-update.sh")), "--help"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assert_no_poison()

    def test_direct_rollback_help_ignores_injection(self) -> None:
        """Even the legacy rollback help remains inert with poisoned env."""
        result = self.invoke(
            [str(self.copy_entrypoint("rollback.sh")), "--help"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage:", result.stdout)
        self.assert_no_poison()

    @unittest.skipUnless(os.geteuid() == 0, "root-only recovery QA")
    def test_direct_empty_boot_recovery_remains_inert(self) -> None:
        """No pending marker must not trigger import or filesystem work."""
        result = self.invoke(
            [str(self.copy_entrypoint("apply-update.sh")), "--recover"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "updater-state").exists())
        self.assert_no_poison()

    @unittest.skipUnless(os.geteuid() == 0, "root-only recovery QA")
    def test_privileged_systemd_prestart_is_inert(self) -> None:
        """Unit's explicit interpreter must suppress startup sourcing."""
        expected = (
            "ExecStartPre=/bin/bash -p "
            "/opt/shelly-voltage/scripts/apply-update.sh --recover"
        )
        self.assertIn(expected, SYSTEMD.read_text())
        script = self.copy_entrypoint("apply-update.sh")
        result = self.invoke(["/bin/bash", "-p", str(script), "--recover"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_poison()

    @unittest.skipIf(os.geteuid() == 0, "nonroot rejection QA")
    def test_nonroot_empty_recovery_refused_before_state(self) -> None:
        """An unprivileged caller receives a fail-closed exit code."""
        script = self.copy_entrypoint("apply-update.sh")
        result = self.invoke([str(script), "--recover"])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assert_no_poison()

    def test_protected_shebangs_and_unit_match(self) -> None:
        """Source and fixed systemd command must retain protected mode."""
        for name in SCRIPT_NAMES:
            source = (ROOT / "scripts" / name).read_text()
            self.assertTrue(source.startswith("#!/bin/bash -p\n"))
        self.assertIn(
            "ExecStartPre=/bin/bash -p ",
            SYSTEMD.read_text(),
        )

    def test_forced_unsafe_bash_is_not_protected(self) -> None:
        """Document residual risk of a caller ignoring the shebang."""
        script = self.copy_entrypoint("rollback.sh")
        result = self.invoke(["/bin/bash", str(script), "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.marker.exists())
        self.assertFalse(self.fake_marker.exists())


if __name__ == "__main__":
    unittest.main()
