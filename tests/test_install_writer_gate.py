"""Require an offline writer before an existing Shelly installation changes."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fake_docker_directory(root: Path) -> Path:
    """Create one isolated Docker inspection stub."""

    folder = root / "bin"
    folder.mkdir(exist_ok=True)
    executable = folder / "docker"
    executable.write_text(
        '#!/bin/sh\nif [ "$1" = inspect ]; then\n'
        '  case "$DOCKER_SIM_STATE" in\n'
        "    running) echo true;;\n"
        "    stopped) echo false;;\n"
        "    *) exit 17;;\n"
        "  esac\n"
        'elif [ "$1" = container ] && [ "$2" = ls ]; then\n'
        '  case "$DOCKER_SIM_STATE" in\n'
        "    absent) echo other-container;;\n"
        "    listed) echo shelly-voltage-bot;;\n"
        "    *) exit 17;;\n"
        "  esac\n"
        "else\n  exit 17\nfi\n"
    )
    executable.chmod(0o700)
    return folder


def invoke_gate(root: Path, state: str, installed: bool, dry: bool) -> int:
    """Run only the isolated installer gate with a stub Docker binary."""

    functions = root / "isolated-functions.sh"
    source = (ROOT / "scripts/install.sh").read_text()
    assert source.rstrip().endswith('main "$@"')
    functions.write_text(source.rsplit('main "$@"', 1)[0])
    target = root / "install"
    target.mkdir(exist_ok=True)
    if installed:
        (target / "VERSION").write_text("0.5.13-hw\n")
    fake_bin = fake_docker_directory(root)
    command = (
        'source "$1"; TARGET="$2"; DRY_RUN="$3"; require_quiesced_upgrade'
    )
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_SIM_STATE": state,
    }
    result = subprocess.run(
        [
            "bash",
            "-c",
            command,
            "_",
            str(functions),
            str(target),
            "true" if dry else "false",
        ],
        env=env,
        capture_output=True,
        timeout=10,
    )
    return result.returncode


class InstallWriterGateTests(unittest.TestCase):
    """Prevent accidental live updates before any source write."""

    def test_main_calls_guard_before_docker_setup(self) -> None:
        """The live-update gate must run before Docker setup."""

        source = (ROOT / "scripts/install.sh").read_text()
        main = source.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(
            "require_quiesced_upgrade\n    signed_image_preflight"
            "\n    snapshot_upgrade\n    install_docker",
            main,
        )

    def test_existing_running_or_unknown_writer_rejected(self) -> None:
        """Fail closed when the container runs or cannot be inspected."""

        for state in ("running", "unknown"):
            with (
                self.subTest(state=state),
                tempfile.TemporaryDirectory() as directory,
            ):
                result = invoke_gate(Path(directory), state, True, False)
                self.assertNotEqual(result, 0)

    def test_stopped_writer_and_new_install_allowed(self) -> None:
        """Preserve an intentional maintenance window or fresh install."""

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                invoke_gate(Path(directory), "stopped", True, False), 0
            )
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                invoke_gate(Path(directory), "absent", False, False), 0
            )

    def test_fresh_target_rejects_existing_live_writer(self) -> None:
        """An alternate installation root cannot replace a running bot."""

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                invoke_gate(Path(directory), "running", False, False), 5
            )

    def test_fresh_target_allows_stopped_or_absent_writer(self) -> None:
        """A fresh install remains possible with no live Docker writer."""

        for state in ("stopped", "absent"):
            with (
                self.subTest(state=state),
                tempfile.TemporaryDirectory() as directory,
            ):
                self.assertEqual(
                    invoke_gate(Path(directory), state, False, False), 0
                )

    def test_fresh_target_rejects_unknown_docker_inspection(self) -> None:
        """Never assume failed inspect means no existing writer."""

        for state in ("unknown", "listed"):
            with (
                self.subTest(state=state),
                tempfile.TemporaryDirectory() as directory,
            ):
                self.assertEqual(
                    invoke_gate(Path(directory), state, False, False), 5
                )

    def test_dry_run_fresh_does_not_require_docker(self) -> None:
        """Plan mode never inspects Docker, even when the bot is running."""

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                invoke_gate(Path(directory), "running", False, True), 0
            )

    def test_dry_run_existing_install_has_no_docker_dependency(self) -> None:
        """Print a plan without querying or changing Docker."""

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                invoke_gate(Path(directory), "unknown", True, True), 0
            )


if __name__ == "__main__":
    unittest.main()
