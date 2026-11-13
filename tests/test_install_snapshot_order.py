"""Ensure recovery snapshot precedes every installer system change."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    "parse_args require_root require_safe_install_target "
    "require_safe_install_files require_valid_install_marker "
    "require_safe_fresh_database_state require_quiesced_upgrade "
    "signed_image_preflight snapshot_upgrade install_docker "
    "copy_source prepare_state configure_project install_units deploy summary"
)


def run_isolated_main(fail_snapshot: bool) -> tuple[int, list[str]]:
    """Run installer main with all stages replaced by inert stubs."""

    with tempfile.TemporaryDirectory() as name:
        root = Path(name)
        script = (ROOT / "scripts/install.sh").read_text()
        assert script.rstrip().endswith('main "$@"')
        functions = root / "installer-functions.sh"
        functions.write_text(script.rsplit('main "$@"', 1)[0])
        output = root / "calls.txt"
        harness = """
source "$1"
for stage in $STAGES; do
  eval "$stage() { echo '$stage' >> '$LOG_FILE'; }"
done
snapshot_upgrade() {
  echo snapshot_upgrade >> "$LOG_FILE"
  if [[ "$FAIL_SNAPSHOT" == yes ]]; then return 9; fi
}
main --non-interactive
"""
        result = subprocess.run(
            ["bash", "-c", harness, "_", str(functions), str(output)],
            env={
                "PATH": "/usr/bin:/bin",
                "STAGES": STAGES,
                "LOG_FILE": str(output),
                "FAIL_SNAPSHOT": "yes" if fail_snapshot else "no",
            },
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode, output.read_text().splitlines()


class InstallSnapshotOrderTests(unittest.TestCase):
    """Prevent Docker package changes without a verified rescue point."""

    def test_snapshot_precedes_docker_on_success(self) -> None:
        """Successful installs must snapshot before installing Docker."""

        code, calls = run_isolated_main(False)
        self.assertEqual(code, 0)
        self.assertLess(
            calls.index("snapshot_upgrade"), calls.index("install_docker")
        )

    def test_snapshot_failure_prevents_any_docker_setup(self) -> None:
        """Failed snapshot must abort before Docker setup or source copy."""

        code, calls = run_isolated_main(True)
        self.assertEqual(code, 9)
        self.assertIn("snapshot_upgrade", calls)
        self.assertNotIn("install_docker", calls)
        self.assertNotIn("copy_source", calls)


if __name__ == "__main__":
    unittest.main()
