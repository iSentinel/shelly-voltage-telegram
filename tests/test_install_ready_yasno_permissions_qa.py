"""Regression tests for signed-start readiness and YASNO source isolation."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "scripts/install.sh"


CURL_FAKE = r"""
count=0
test ! -f "$READY_COUNT" || count=$(cat "$READY_COUNT")
printf '%s' "$((count+1))" > "$READY_COUNT"
code=503
position=0
oldIFS=$IFS
IFS=,
for item in $READY_CODES; do
    code=$item
    test "$position" -ne "$count" || break
    position=$((position+1))
done
IFS=$oldIFS
printf '%s' "$code"
"""

PARITY_FAKE = r"""
case "$1" in
    */deployment-state-audit.py) ;;
    *) exit 80 ;;
esac
count=0
test ! -f "$PARITY_COUNT" || count=$(cat "$PARITY_COUNT")
printf '%s' "$((count+1))" > "$PARITY_COUNT"
code=2
position=0
oldIFS=$IFS
IFS=,
for item in $PARITY_CODES; do
    code=$item
    test "$position" -ne "$count" || break
    position=$((position+1))
done
IFS=$oldIFS
exit "$code"
"""


class InstallerFunctionsTest(unittest.TestCase):
    """Source only installer functions in a private, disconnected fixture."""

    def setUp(self) -> None:
        """Build a fixture without running the installer entry point."""

        temp = tempfile.TemporaryDirectory(prefix="svt-installer-qa-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = INSTALLER.read_text()
        assert source.rstrip().endswith('main "$@"')
        self.functions = self.root / "functions.sh"
        self.functions.write_text(source.rsplit('main "$@"', 1)[0])
        self.target = self.root / "target"
        (self.target / "scripts").mkdir(parents=True)
        self.bindir = self.root / "bin"
        self.bindir.mkdir()

    def fake(self, name: str, source: str) -> None:
        """Install a private command spy, not a privileged system command."""

        path = self.bindir / name
        path.write_text("#!/bin/sh\n" + source)
        path.chmod(0o700)

    def invoke(
        self, method: str, extra: dict | None = None
    ) -> subprocess.CompletedProcess:
        """Run the selected function, with no production network or files."""

        env = (
            os.environ
            | {
                "PATH": str(self.bindir) + os.pathsep + os.environ["PATH"],
                "TARGET": str(self.target),
                "DRY_RUN": "false",
            }
            | (extra or {})
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; TARGET="$2"; DRY_RUN="$3"; SOURCE="$5"; "$4"',
                "bash",
                str(self.functions),
                str(self.target),
                env["DRY_RUN"],
                method,
                env.get("QA_SOURCE", str(INSTALLER.parent.parent)),
            ],
            env=env,
            text=True,
            capture_output=True,
            timeout=12,
            check=False,
        )


class SignedStartupReadinessTests(InstallerFunctionsTest):
    """Require both fresh Shelly readings and real Docker parity."""

    def setUp(self) -> None:
        """Fake only readiness and deployment parity responses."""

        super().setUp()
        self.fake("curl", CURL_FAKE)
        self.fake("python3", PARITY_FAKE)
        self.fake("sleep", "exit 0\n")
        self.ready_count = self.root / "ready.count"
        self.parity_count = self.root / "parity.count"

    def check(self, ready: str, parity: str) -> tuple[int, int, int]:
        """Return exit status and number of readiness / parity probes."""

        result = self.invoke(
            "await_signed_ready",
            {
                "READY_CODES": ready,
                "PARITY_CODES": parity,
                "READY_COUNT": str(self.ready_count),
                "PARITY_COUNT": str(self.parity_count),
            },
        )
        ready_calls = int(self.ready_count.read_text())
        parity_calls = int(self.parity_count.read_text())
        return result.returncode, ready_calls, parity_calls

    def test_docker_health_lags_behind_ready(self) -> None:
        """HTTP success alone must not authorize deployment parity."""

        self.assertEqual(
            self.check("503,503,200,200,200,200,200", "2,2,0"),
            (0, 7, 3),
        )

    def test_permanent_docker_unhealthy_fails_closed(self) -> None:
        """Reject even 60 fresh polls if Docker remains unhealthy."""

        code, ready, parity = self.check("200", "2")
        self.assertEqual(code, 1)
        self.assertEqual(ready, 60)
        self.assertGreaterEqual(parity, 50)

    def test_permanent_shelly_offline_fails_closed(self) -> None:
        """Reject missing voltage, regardless of diagnostic parity."""

        code, ready, parity = self.check("503", "0")
        self.assertEqual(code, 1)
        self.assertEqual(ready, 60)
        self.assertEqual(parity, 1)


class YasnoSourcePermissionsTests(InstallerFunctionsTest):
    """Permit named forecast user traversal without exposing credentials."""

    def setUp(self) -> None:
        """Make a source tree with the private files seen on LXC110."""

        super().setUp()
        self.target.chmod(0o700)
        (self.target / "scripts").chmod(0o700)
        (self.target / ".env").write_text("SECRET=private\n")
        (self.target / ".env").chmod(0o600)
        (self.target / "secrets").mkdir(mode=0o700)
        for file in (
            "scripts/yasno-refresh.py",
            "yasno_cache.py",
            "yasno_schedule.py",
        ):
            path = self.target / file
            path.write_text("# safe public YASNO fixture\n")
            path.chmod(0o600)
        self.calls = self.root / "calls.txt"
        self.fake("getent", 'test "$YASNO_PRESENT" = 1\n')
        self.fake("id", 'test "$YASNO_PRESENT" = 1\n')
        self.fake("chgrp", 'printf "group %s\\n" "$*" >> "$YASNO_CALLS"\n')
        self.fake(
            "chmod",
            r"""
printf 'mode %s\n' "$*" >> "$YASNO_CALLS"
/bin/chmod "$@"
""",
        )

    def run_yasno(
        self, *, present: bool, dry_run: bool = False
    ) -> subprocess.CompletedProcess:
        """Run only the YASNO permission helper in a disposable directory."""

        return self.invoke(
            "prepare_yasno_reader",
            {
                "YASNO_PRESENT": "1" if present else "0",
                "YASNO_CALLS": str(self.calls),
                "DRY_RUN": "true" if dry_run else "false",
            },
        )

    def test_minimal_traversal_preserves_private_modes(self) -> None:
        """Grant only group traversal, not .env or secret-file access."""

        result = self.run_yasno(present=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o750)
        self.assertEqual(
            stat.S_IMODE((self.target / "scripts").stat().st_mode), 0o710
        )
        self.assertEqual(
            stat.S_IMODE((self.target / "secrets").stat().st_mode), 0o700
        )
        self.assertEqual(
            stat.S_IMODE((self.target / ".env").stat().st_mode), 0o600
        )
        self.assertIn("group shelly-yasno", self.calls.read_text())
        for file in (
            "scripts/yasno-refresh.py",
            "yasno_cache.py",
            "yasno_schedule.py",
        ):
            self.assertEqual(
                stat.S_IMODE((self.target / file).stat().st_mode), 0o640
            )

    def test_absent_yasno_account_does_not_change_modes(self) -> None:
        """Do not require the optional YASNO sidecar on other installs."""

        self.assertEqual(self.run_yasno(present=False).returncode, 0)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertFalse(self.calls.exists())

    def test_dry_run_is_non_mutating(self) -> None:
        """Dry runs must not grant rights or modify directories."""

        result = self.run_yasno(present=True, dry_run=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("DRY-RUN", result.stdout)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertFalse(self.calls.exists())

    def make_source_tar_fixture(self) -> Path:
        """Prepare an isolated source archive with hostile private files."""

        source = self.root / "source"
        source.mkdir(mode=0o700)
        scripts = source / "scripts"
        scripts.mkdir(mode=0o700)
        shutil.copy2(INSTALLER.parent / "source-tar.sh", scripts)
        (scripts / "yasno-refresh.py").write_text("# test fixture\n")
        (source / "yasno_cache.py").write_text("# cache module fixture\n")
        (source / "yasno_schedule.py").write_text(
            "# schedule module fixture\n"
        )
        (source / "VERSION").write_text("0.5.25-hw\n")
        (source / ".env").write_text("PRIVATE=overwritten\n")
        private = source / "secrets"
        private.mkdir(mode=0o700)
        (private / "telegram_token").write_text("wrong\n")

        return source

    def test_source_tar_overlay_preserves_secrets_and_yasno(self) -> None:
        """Exercise source copy and least-privilege YASNO repair."""

        source = self.make_source_tar_fixture()

        expected_env = (self.target / ".env").read_bytes()
        (self.target / "secrets" / "telegram_token").write_text("keep\n")
        expected_token = (
            self.target / "secrets" / "telegram_token"
        ).read_bytes()
        result = self.invoke("copy_source", {"QA_SOURCE": str(source)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.target / ".env").read_bytes(), expected_env)
        self.assertEqual(
            (self.target / "secrets" / "telegram_token").read_bytes(),
            expected_token,
        )
        self.assertTrue(
            (self.target / "scripts" / "yasno-refresh.py").is_file()
        )
        result = self.run_yasno(present=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o750)
        self.assertEqual(
            stat.S_IMODE((self.target / "scripts").stat().st_mode), 0o710
        )
        self.assertEqual(
            stat.S_IMODE((self.target / ".env").stat().st_mode), 0o600
        )
        self.assertEqual(
            stat.S_IMODE((self.target / "secrets").stat().st_mode), 0o700
        )

    def test_wrong_owner_fails_before_directory_mode_change(self) -> None:
        """Reject a private config owned by someone else, even at 0600."""

        self.fake(
            "stat",
            """
case "$*" in
    *'%u'*'/.env') printf '99999\\n' ;;
    *) exec /usr/bin/stat "$@" ;;
esac
""",
        )
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credentials lost protected mode", result.stderr)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.target / "scripts").stat().st_mode),
            0o700,
        )
        self.assertFalse(self.calls.exists())

    def test_symlinked_scripts_fail_before_group_change(self) -> None:
        """Never grant traversal to arbitrary paths through a linked dir."""

        scripts = self.target / "scripts"
        (scripts / "yasno-refresh.py").unlink()
        scripts.rmdir()
        scripts.symlink_to(self.root, target_is_directory=True)
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertFalse(self.calls.exists())

    def test_public_yasno_symlink_fails_before_changes(self) -> None:
        """Do not follow links to .env or unrelated private state."""

        code = self.target / "yasno_cache.py"
        code.unlink()
        code.symlink_to(self.target / ".env")
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe YASNO public source", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)

    def test_hardlinked_yasno_source_fails_closed(self) -> None:
        """Never group-read a linked private file during permission repair."""

        code = self.target / "yasno_schedule.py"
        code.unlink()
        code.hardlink_to(self.target / ".env")
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe YASNO public source", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_missing_yasno_module_blocks_without_partial_chmod(self) -> None:
        """A partial source extraction must not grant directory access."""

        (self.target / "yasno_schedule.py").unlink()
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe YASNO public source", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)

    def test_owner_mismatch_yasno_module_fails_closed(self) -> None:
        """Reject foreign-owned public code before changing file groups."""

        self.fake(
            "stat",
            r"""
case "$*" in
    *'%u'*'yasno_schedule.py') printf '99999\n' ;;
    *) exec /usr/bin/stat "$@" ;;
esac
""",
        )
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe YASNO public source", result.stderr)
        self.assertFalse(self.calls.exists())

    def test_insecure_env_blocks_source_install(self) -> None:
        """Never accept a readable private .env after the access change."""

        (self.target / ".env").chmod(0o644)
        result = self.run_yasno(present=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("credentials lost protected mode", result.stderr)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.target / "scripts").stat().st_mode),
            0o700,
        )
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
