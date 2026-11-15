"""Fail-closed shell QA for legacy updater and boot recovery admission."""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

UPDATER = Path(__file__).resolve().parents[1] / "scripts/apply-update.sh"


class LegacyUpdaterOwnerFuseTests(unittest.TestCase):
    """Run no production command: all state lives in private QA scratch."""

    def setUp(self) -> None:
        """Prepare missing updater state and isolated fake owner commands."""
        tmp = tempfile.TemporaryDirectory(prefix="qa-updater-fuse-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "updater-state"
        self.gate = self.root / "gate"
        self.gate.mkdir(mode=0o700)
        self.lock = self.gate / "G"
        self.lock.touch()
        self.lock.chmod(0o640)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.trace = self.root / "mock.log"
        self.env = dict(os.environ)
        self.env.update({
            "SVT_ROOT": str(self.root / "missing-app"),
            "SVT_UPDATE_STATE": str(self.state),
            "SVT_OWNER_GATE_DIR": str(self.gate),
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "MOCK_TRACE": str(self.trace),
        })
        self.write_mocks()

    def write_mocks(self, systemd: str = "inactive") -> None:
        """Provide read-only synthetic Docker/systemd state only."""
        docker = self.bin / "docker"
        docker.write_text(
            '#!/bin/sh\nprintf "DOCKER %s\n" "$*" >> "$MOCK_TRACE"\n'
            'printf "false|no|0\n"\n'
        )
        docker.chmod(0o700)
        unit = self.bin / "systemctl"
        unit.write_text(
            '#!/bin/sh\nprintf "UNIT %s\n" "$*" >> "$MOCK_TRACE"\n'
            f'printf "ActiveState={systemd}\nUnitFileState=masked\n"\n'
        )
        unit.chmod(0o700)

    def execute(
        self, *args: str, fd: int | None = None, function: bool = False
    ) -> subprocess.CompletedProcess:
        """Run candidate Bash with inherited G only when supplied."""
        env = dict(self.env)
        fds: tuple[int, ...] = ()
        if fd is not None:
            env["SVT_MAINTENANCE_G_FD"] = str(fd)
            fds = (fd,)
        command = ["bash", str(UPDATER), *args]
        if function:
            command = [
                "bash", "-c", 'source "$1"; refuse_unapproved_update',
                "qa", str(UPDATER),
            ]
        return subprocess.run(
            command, env=env, pass_fds=fds, capture_output=True,
            text=True, check=False, timeout=12,
        )

    def trace_lines(self) -> list[str]:
        """Read mock invocation history without touching real Docker."""
        if self.trace.exists():
            return self.trace.read_text().splitlines()
        return []

    def test_shell_syntax(self) -> None:
        """Bash candidate must parse in CI before any runtime use."""
        result = subprocess.run(
            ["bash", "-n", str(UPDATER)],
            capture_output=True, text=True, check=False, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_is_inert(self) -> None:
        """Help never requires G or creates updater state."""
        result = self.execute("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.trace_lines(), [])

    def test_boot_without_pending_is_inert(self) -> None:
        """No pending marker must not launch an old recovery writer."""
        result = self.execute("--recover")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.trace_lines(), [])

    def test_pending_recovery_without_g_refused(self) -> None:
        """Old pending archives cannot replace current WAL on boot."""
        self.state.mkdir()
        (self.state / "pending-update").write_text("fake-token")
        result = self.execute("--recover")
        self.assertEqual(result.returncode, 5)
        self.assertIn("inherited maintenance G EX", result.stderr)
        self.assertEqual(self.trace_lines(), [])
        self.assertTrue((self.state / "pending-update").exists())

    def test_pending_marker_symlink_refused(self) -> None:
        """Never follow a symlinked pending marker into recovery."""
        self.state.mkdir()
        (self.state / "pending-update").symlink_to(self.root / "other")
        result = self.execute("--recover")
        self.assertEqual(result.returncode, 5)
        self.assertIn("linked updater", result.stderr)
        self.assertEqual(self.trace_lines(), [])

    def test_state_directory_symlink_refused(self) -> None:
        """A redirected updater state is not an approved path."""
        alternate = self.root / "alternate"
        alternate.mkdir()
        self.state.symlink_to(alternate, target_is_directory=True)
        result = self.execute("--recover")
        self.assertEqual(result.returncode, 5)
        self.assertEqual(self.trace_lines(), [])

    def test_real_version_without_g_refused_before_requirements(self) -> None:
        """No update may create state or invoke Docker without owner G."""
        result = self.execute("--version", "0.5.21-hw")
        self.assertEqual(result.returncode, 5)
        self.assertIn("inherited maintenance G EX", result.stderr)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.trace_lines(), [])

    def test_owner_guard_before_stateful_requirements(self) -> None:
        """Real update admission must precede requirements and preflight."""
        body = UPDATER.read_text().split("\nmain() {", 1)[1]
        self.assertLess(
            body.index("refuse_unapproved_update"),
            body.index("\n    requirements"),
        )
        self.assertIn('[[ "$DRY_RUN" != true ]]', body)
        self.assertIn(
            "complete owner gate and approval pending", UPDATER.read_text()
        )

    @unittest.skipUnless(os.geteuid() == 0, "root-only kernel G QA")
    def test_inherited_g_narrow_pass_is_still_no_go(self) -> None:
        """A real G EX and frozen mocks never authorize actual updates."""
        with self.lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute(fd=held.fileno(), function=True)
        self.assertEqual(result.returncode, 5, result.stderr)
        self.assertIn('"release_decision": "NO_GO"', result.stdout)
        self.assertIn("complete owner gate", result.stderr)
        self.assertEqual(len(self.trace_lines()), 4)
        self.assertFalse(self.state.exists())

    @unittest.skipUnless(os.geteuid() == 0, "root-only kernel G QA")
    def test_active_systemd_blocks_even_narrow_admission(self) -> None:
        """An active legacy restart owner refuses the inherited G path."""
        self.write_mocks(systemd="active")
        with self.lock.open("rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute(fd=held.fileno(), function=True)
        self.assertEqual(result.returncode, 5)
        self.assertIn("SYSTEMD_OWNER_STILL_ACTIVE", result.stdout)
        self.assertTrue(all(x.startswith("UNIT ") for x in self.trace_lines()))


if __name__ == "__main__":
    unittest.main()
