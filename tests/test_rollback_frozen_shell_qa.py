"""Shell regression for a fail-closed legacy rollback freeze preflight."""

from __future__ import annotations

import fcntl
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROLLBACK = SCRIPTS / "rollback.sh"


class RollbackFrozenShellTests(unittest.TestCase):
    """All integration tests use fake Docker/systemd and a missing app."""

    def setUp(self) -> None:
        """Own one private synthetic G directory without production data."""
        tmp = tempfile.TemporaryDirectory(prefix="qa-rollback-shell-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.gate = self.root / "gate"
        self.gate.mkdir(mode=0o700)
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.trace = self.root / "mock.log"
        for name in ("G", "B", "W"):
            lock = self.gate / name
            lock.touch()
            lock.chmod(0o640)
        self.write_mocks()
        self.env = dict(os.environ)
        self.env.update(
            {
                "PATH": str(self.bin) + ":" + os.environ["PATH"],
                "MOCK_TRACE": str(self.trace),
                "SVT_ROOT": str(self.root / "missing-app"),
                "DATABASE_PATH": str(self.root / "unused.sqlite3"),
                "SVT_OWNER_GATE_DIR": str(self.gate),
            }
        )

    def write_mocks(
        self,
        docker: str = "false|no|0",
        systemd: str = "ActiveState=inactive\nUnitFileState=masked-runtime",
    ) -> None:
        """Replace only private binaries; real Docker is never invoked."""
        for name, output in (("docker", docker), ("systemctl", systemd)):
            executable = self.bin / name
            body = (
                "#!/bin/sh\n"
                f'printf \'{name.upper()} %s\\n\' "$*" >> "$MOCK_TRACE"\n'
                "printf '%b\\n' " + repr(output) + "\n"
            )
            executable.write_text(body)
            executable.chmod(0o700)

    def execute(
        self, *options: str, inherited_fd: int | None = None
    ) -> subprocess.CompletedProcess:
        """Execute only the candidate shell on a nonexistent QA target."""
        env = dict(self.env)
        descriptors: tuple[int, ...] = ()
        if inherited_fd is not None:
            env["SVT_MAINTENANCE_G_FD"] = str(inherited_fd)
            descriptors = (inherited_fd,)
        return subprocess.run(
            ["bash", str(ROLLBACK), *options],
            env=env,
            pass_fds=descriptors,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def calls(self) -> list[str]:
        """Read only the private fake-binary audit log."""
        return (
            self.trace.read_text().splitlines() if self.trace.exists() else []
        )

    def test_source_checks_freeze_before_target(self) -> None:
        """No source or DB validation may precede owner preflight."""
        text = ROLLBACK.read_text()
        main = text[text.index("main() {") :]
        self.assertLess(
            main.index("    require_frozen_rollback_owner"),
            main.index("    preflight_source_target"),
        )

    def test_start_only_possible_in_explicit_dry_run(self) -> None:
        """Candidate must reject actual restore unless --no-start."""
        contents = ROLLBACK.read_text()
        self.assertIn('[[ "$NO_START" != true ]]', contents)
        self.assertIn('[[ "$DRY_RUN" == true ]] && return 0', contents)

    def test_shell_syntax_passes(self) -> None:
        """The candidate must remain parsable POSIX Bash script."""
        run = subprocess.run(
            ["bash", "-n", str(ROLLBACK)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

    @unittest.skipUnless(os.geteuid() == 0, "root-only shell integration")
    def test_missing_no_start_refused_before_docker(self) -> None:
        """An actual rollback must never start the old OCI by default."""
        result = self.execute("--snapshot", str(self.root / "missing"))
        self.assertEqual(result.returncode, 5)
        self.assertIn("--no-start", result.stderr)
        self.assertEqual(self.calls(), [])

    @unittest.skipUnless(os.geteuid() == 0, "root-only shell integration")
    def test_missing_g_fd_refused_before_docker(self) -> None:
        """Even with no start, inherited G EX is mandatory."""
        result = self.execute("--no-start")
        self.assertEqual(result.returncode, 5)
        self.assertIn("maintenance G EX FD", result.stderr)
        self.assertEqual(self.calls(), [])

    @unittest.skipUnless(os.geteuid() == 0, "root-only shell integration")
    def test_real_inherited_g_passes_then_missing_target(self) -> None:
        """Narrow frozen admission leads only to safe missing-app refusal."""
        with (self.gate / "G").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute("--no-start", inherited_fd=lock.fileno())
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("unsafe application target", result.stderr)
        self.assertEqual(len(self.calls()), 4)
        self.assertFalse((self.root / "unused.sqlite3").exists())

    @unittest.skipUnless(os.geteuid() == 0, "root-only shell integration")
    def test_docker_autorestart_fails_before_target(self) -> None:
        """A stopped old owner with unless-stopped still blocks restore."""
        self.write_mocks(docker="false|unless-stopped|0")
        with (self.gate / "G").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute("--no-start", inherited_fd=lock.fileno())
        self.assertEqual(result.returncode, 5)
        self.assertIn("AUTORESTART_NOT_FROZEN", result.stdout)
        self.assertFalse((self.root / "unused.sqlite3").exists())

    @unittest.skipUnless(os.geteuid() == 0, "root-only shell integration")
    def test_active_systemd_fails_before_docker(self) -> None:
        """An active systemd entrypoint cannot pass frozen-owner check."""
        self.write_mocks(systemd="ActiveState=active\nUnitFileState=masked")
        with (self.gate / "G").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.execute("--no-start", inherited_fd=lock.fileno())
        self.assertEqual(result.returncode, 5)
        self.assertIn("SYSTEMD_OWNER_STILL_ACTIVE", result.stdout)
        self.assertTrue(all(x.startswith("SYSTEMCTL ") for x in self.calls()))


if __name__ == "__main__":
    unittest.main()
