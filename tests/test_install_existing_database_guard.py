"""Reject first-install roots that would reuse unprotected SQLite history."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_SQLITE = "/var/lib/shelly-voltage/voltage.sqlite3"


def invoke_guard(kind: str, installed: bool = False) -> tuple[int, str]:
    """Execute only the real preflight against disposable storage paths."""

    with tempfile.TemporaryDirectory(prefix="em50-fresh-db-") as temporary:
        root = Path(temporary)
        db = root / "voltage.sqlite3"
        target = root / "target"
        target.mkdir()
        if installed:
            (target / "VERSION").write_text("0.5.13-hw\n")
        if kind == "db":
            db.write_bytes(b"existing sqlite content")
        elif kind in ("wal", "shm"):
            (root / ("voltage.sqlite3-" + kind)).write_text("journal")
        elif kind == "linked":
            db.symlink_to(root / "external.sqlite3")
        installer = (ROOT / "scripts/install.sh").read_text()
        assert installer.rstrip().endswith('main "$@"')
        functions = root / "functions.sh"
        functions.write_text(
            installer.rsplit('main "$@"', 1)[0].replace(LIVE_SQLITE, str(db))
        )
        script = 'source "$1"; TARGET="$2"; require_safe_fresh_database_state'
        result = subprocess.run(
            ["bash", "-c", script, "_", str(functions), str(target)],
            env=os.environ,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode, result.stderr.decode(errors="replace")


class ExistingDatabaseInstallGuardTests(unittest.TestCase):
    """Preserve common SQLite history during alternate-root installs."""

    def test_existing_database_or_journals_block_new_root(self) -> None:
        """Never call a reused SQLite state a first installation."""

        for kind in ("db", "wal", "shm", "linked"):
            with self.subTest(kind=kind):
                code, message = invoke_guard(kind)
                self.assertEqual(code, 5)
                self.assertIn("existing Shelly SQLite state", message)

    def test_genuinely_fresh_root_is_allowed(self) -> None:
        """No application marker and no SQLite state is genuinely new."""

        code, _ = invoke_guard("absent")
        self.assertEqual(code, 0)

    def test_versioned_upgrade_still_allowed(self) -> None:
        """A versioned deployment uses separate quiesced backup checks."""

        code, _ = invoke_guard("db", installed=True)
        self.assertEqual(code, 0)

    def test_main_guard_precedes_docker_and_snapshot(self) -> None:
        """Check shared SQLite before any package or backup mutation."""

        script = (ROOT / "scripts/install.sh").read_text()
        main = script.split("main() {", 1)[1].split("\n}", 1)[0]
        self.assertIn(
            "require_valid_install_marker\n"
            "    require_safe_fresh_database_state\n"
            "    require_quiesced_upgrade",
            main,
        )


if __name__ == "__main__":
    unittest.main()
