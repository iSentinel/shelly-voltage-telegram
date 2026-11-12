"""Test rollback Compose plans with zero real host or Docker actions."""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RollbackYasnoComposeTests(unittest.TestCase):
    """Keep an approved YASNO overlay when restoring an older release."""

    def setUp(self):
        """Build isolated fixtures with fake Docker and systemctl commands."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        (self.app / "systemd").mkdir(parents=True, mode=0o700)
        (self.app / "systemd/service.service").write_text("Unit fixture")
        (self.app / "compose.yaml").write_text("services: {}\n")
        self.bin = self.root / "bin"
        self.bin.mkdir(mode=0o700)
        self.log = self.root / "docker-commands.txt"
        mock = self.bin / "docker"
        mock.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_FIXTURE_LOG"\n'
        )
        mock.chmod(0o700)
        self.overlay = self.root / "yasno.yaml"
        self.updater = self.root / "updater.yaml"

    def render(self, enable: bool, unsafe: bool = False):
        """Source rollback functions but intercept every system action."""
        rollback = ROOT / "scripts/rollback.sh"
        source = rollback.read_text()
        self.assertTrue(source.rstrip().endswith('main "$@"'))
        fixture = self.root / "rollback-functions.sh"
        fixture.write_text(source.rsplit('main "$@"', 1)[0])
        helper = self.root / "compose-helper.sh"
        helper.write_text((ROOT / "scripts/start-compose.sh").read_text())
        if enable:
            self.overlay.write_text("services: {}\n")
            self.overlay.chmod(0o666 if unsafe else 0o600)
        env = dict(os.environ)
        env.update(
            SVT_ROOT=str(self.app),
            SVT_YASNO_READER_OVERLAY=str(self.overlay),
            SVT_UPDATE_OVERRIDE=str(self.updater),
            DOCKER_FIXTURE_LOG=str(self.log),
            PATH=str(self.bin) + os.pathsep + env["PATH"],
        )
        return fixture, helper, env

    def run_rollback_start(self, enable: bool, unsafe: bool = False):
        """Start restored Compose with helper already loaded in memory."""
        fixture, helper, env = self.render(enable, unsafe)
        shell = (
            'source "$1"; source "$2"; compose_prepare true; '
            'rm -f -- "$2"; '
            "cp() { :; }; systemctl() { :; }; curl() { :; }; "
            "start_service"
        )
        result = subprocess.run(
            ["bash", "-c", shell, "bash", str(fixture), str(helper)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        calls = self.log.read_text().splitlines() if self.log.exists() else []
        return result, [shlex.split(row) for row in calls]

    def test_rollback_preserves_readonly_yasno_after_source_replaced(self):
        """The opt-in layer survives replacement of the helper source."""
        result, calls = self.run_rollback_start(True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertIn(str(self.overlay), calls[0])
        self.assertIn(str(self.overlay), calls[1])
        self.assertEqual(calls[0][-2:], ["config", "-q"])
        self.assertEqual(calls[1][-3:], ["up", "-d", "--build"])

    def test_rollback_default_does_not_enable_yasno(self):
        """Missing owner opt-in remains a base-only Compose start."""
        result, calls = self.run_rollback_start(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertNotIn(str(self.overlay), calls[0])
        self.assertEqual(calls[1][-3:], ["up", "-d", "--build"])

    def test_rollback_rejects_unsafe_overlay_before_docker(self):
        """A writable opt-in cannot be used to start restored sources."""
        result, calls = self.run_rollback_start(True, unsafe=True)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(calls, [])

    def test_media_checked_again_after_writer_stop_before_restore(self):
        """Reject an in-flight photo conversion before replacing SQLite."""

        source = (ROOT / "scripts/rollback.sh").read_text()
        main = source.split("main() {", 1)[1].split('\nmain "$@"', 1)[0]
        guard = "graphic-rollback-media-guard.py"
        first = main.index(guard)
        stop = main.index("stop_service\n")
        second = main.index(guard, first + len(guard))
        rescue = main.index("rescue_current_db\n")
        self.assertLess(first, stop)
        self.assertLess(stop, second)
        self.assertLess(second, rescue)
        on_race = main[second:rescue]
        self.assertIn("start_service", on_race)
        self.assertIn("writer remains stopped by operator request", on_race)
        self.assertIn("exit 6", on_race)

    def test_rollback_uses_secure_plan_after_snapshot_validation(self):
        """Inspect the control path, not only the helper itself."""
        script = (ROOT / "scripts/rollback.sh").read_text()
        main = script.split("main() {", 1)[1].split('\nmain "$@"', 1)[0]
        self.assertLess(
            main.index("compose_prepare true"),
            main.index("stop_service\n"),
        )
        self.assertIn("compose_cmd config -q", script)
        self.assertIn("compose_cmd up -d --build", script)


if __name__ == "__main__":
    unittest.main()
