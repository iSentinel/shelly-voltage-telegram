"""Protect the exact immutable image during signed source installation."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SignedDeployPlanTests(unittest.TestCase):
    """Run only isolated installer functions with fake service commands."""

    def setUp(self) -> None:
        """Create disposable functions, entry points, and trace files."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.scratch = Path(temporary.name)
        source = (ROOT / "scripts/install.sh").read_text()
        assert source.rstrip().endswith('main "$@"')
        self.functions = self.scratch / "functions.sh"
        self.functions.write_text(source.rsplit('main "$@"', 1)[0])
        self.target = self.scratch / "project"
        (self.target / "scripts").mkdir(parents=True)
        starter = self.target / "scripts/start-compose.sh"
        starter.write_text('#!/bin/sh\nprintf "start %s\n" "$*" >> "$CALLS"\n')
        starter.chmod(0o700)
        bindir = self.scratch / "bin"
        bindir.mkdir()
        for command in ("docker", "systemctl", "curl"):
            path = bindir / command
            path.write_text(
                '#!/bin/sh\nprintf "%s %s\n" "$0" "$*" >> "$CALLS"\n'
                'case "$0" in */curl) printf 200;; esac\n'
            )
            path.chmod(0o700)
        self.bindir = bindir
        self.calls = self.scratch / "calls.log"

    def invoke(self, signed: bool, dry_run: bool = False) -> list[str]:
        """Execute the deploy function without writing host files."""
        self.calls.unlink(missing_ok=True)
        script = (
            'source "$1"; TARGET="$2"; SIGNED_DEPLOYMENT="$3"; '
            'DRY_RUN="$4"; await_signed_ready() { '
            'echo "ready-verified" >> "$CALLS"; }; deploy'
        )
        env = os.environ | {
            "PATH": f"{self.bindir}:{os.environ['PATH']}",
            "CALLS": str(self.calls),
        }
        result = subprocess.run(
            [
                "bash",
                "-c",
                script,
                "bash",
                str(self.functions),
                str(self.target),
                "true" if signed else "false",
                "true" if dry_run else "false",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return (
            self.calls.read_text().splitlines() if self.calls.exists() else []
        )

    def test_signed_source_never_triggers_local_build(self) -> None:
        """Signed updates use the pinned overlay and rearm Docker policy."""
        calls = self.invoke(signed=True)
        self.assertIn("start ", calls)
        self.assertNotIn("start --build", calls)
        self.assertTrue(
            any("update --restart=unless-stopped" in x for x in calls)
        )
        self.assertIn("ready-verified", calls)

    def test_fresh_bootstrap_retains_build_behavior(self) -> None:
        """Only installations without a signed receipt build locally."""
        calls = self.invoke(signed=False)
        self.assertIn("start --build", calls)
        self.assertNotIn("ready-verified", calls)

    def test_signed_dry_run_has_no_effect(self) -> None:
        """Planning a signed upgrade must not invoke startup tools."""
        self.assertEqual(self.invoke(signed=True, dry_run=True), [])


if __name__ == "__main__":
    unittest.main()
