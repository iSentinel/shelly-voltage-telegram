"""Regression tests for read-only and fail-closed owner source auditor."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/owner-coverage-source-audit.py"
SPEC = importlib.util.spec_from_file_location("qa_source_owner_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def invented_files() -> dict[str, str]:
    """Create only invented scripts with known relevant owner risk lines."""
    rollback_variable = chr(36) + "{DB}"
    return {
        "compose.yaml": "services:\n  writer:\n    restart: unless-stopped\n",
        "scripts/apply-update.sh": (
            "exec 9>/run/lock/shelly-voltage-updater.lock\n"
            "flock -n 9\nflock -w 120 9\n"
            'case "--recover" in *) ;; esac\n'
            "docker compose -f compose.yaml stop shelly-voltage\n"
            'rm -f "$DB-wal" "$DB-shm"\n'
        ),
        "scripts/start-compose.sh": "compose_cmd up -d --no-build\n",
        "scripts/install.sh": (
            "docker update --restart=unless-stopped shelly-voltage-bot\n"
        ),
        "scripts/rollback.sh": (
            "restore_source() { :; }\n"
            + 'rm -f "'
            + rollback_variable
            + '-wal"\n'
        ),
    }


class OwnerSourceAuditTests(unittest.TestCase):
    """Never execute any shell code from the local invented fixture."""

    def setUp(self) -> None:
        """Populate a private fake checkout for a source-only audit."""
        tmp = tempfile.TemporaryDirectory(prefix="owner-audit-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.contents = invented_files()
        for name, content in self.contents.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def codes(self) -> set[str]:
        """Read stable auditor finding codes from invented source only."""
        return {finding.code for finding in AUDIT.inspect(self.root)}

    def test_all_five_source_paths_required(self) -> None:
        """The audited paths must include all known release owners."""
        self.assertEqual(set(AUDIT.SOURCES), set(self.contents))

    def test_all_known_unsafe_paths_block(self) -> None:
        """Known startup, updater and rollback hazards remain NO_GO."""
        needed = {
            "AUTONOMOUS_DOCKER_RESTART_COVERAGE_UNVERIFIED",
            "UPDATER_A_LOCK_NOT_GLOBAL_G_B_PROOF",
            "BOOT_RECOVERY_A_ONLY_NOT_GLOBAL_G_B_PROOF",
            "UPDATER_DOCKER_STOP_OWNER_HANDOFF_UNVERIFIED",
            "UPDATER_SQLITE_WAL_RESTORE_OWNER_UNVERIFIED",
            "DETACHED_COMPOSE_G_HANDOFF_UNVERIFIED",
            "INSTALLER_REENABLES_AUTONOMOUS_RESTART",
            "ROLLBACK_SQLITE_WAL_SHM_REPLACE_UNVERIFIED",
            "ROLLBACK_SOURCE_REPLACEMENT_OWNER_UNVERIFIED",
        }
        self.assertTrue(needed <= self.codes())

    def test_missing_file_cannot_authorize_release(self) -> None:
        """A missing script is never proof of safe ownership."""
        (self.root / "compose.yaml").unlink()
        self.assertIn("MISSING_OR_UNSAFE_SOURCE", self.codes())

    def test_symlinked_file_refused(self) -> None:
        """Do not follow script symlinks through a changed source tree."""
        file = self.root / "scripts/install.sh"
        file.unlink()
        file.symlink_to(self.root / "scripts/rollback.sh")
        self.assertIsNone(AUDIT.read_required(self.root, "scripts/install.sh"))

    def test_directory_instead_of_file_is_denied(self) -> None:
        """Nonregular input is an unsafe or absent release source."""
        path = self.root / "scripts/start-compose.sh"
        path.unlink()
        path.mkdir()
        self.assertIn("MISSING_OR_UNSAFE_SOURCE", self.codes())

    def test_audit_does_not_modify_source(self) -> None:
        """An inspection must preserve all source bytes exactly."""
        file = self.root / "scripts/apply-update.sh"
        before = hashlib.sha256(file.read_bytes()).hexdigest()
        AUDIT.inspect(self.root)
        after = hashlib.sha256(file.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_known_risks_return_exit_three(self) -> None:
        """Known bypass paths must produce a stable nonzero NO_GO."""
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = AUDIT.report(AUDIT.inspect(self.root))
        self.assertEqual(code, 3)
        self.assertIn("NO_GO", output.getvalue())

    def test_clean_looking_static_source_never_returns_GO(self) -> None:
        """Even a clean lexical scan requires independent manual review."""
        for name in self.contents:
            (self.root / name).write_text("# invented source\n")
        self.assertEqual(AUDIT.inspect(self.root), [])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = AUDIT.report([])
        self.assertEqual(code, 4)
        self.assertIn("REVIEW_REQUIRED", output.getvalue())

    def test_adding_G_comment_cannot_hide_A_only_path(self) -> None:
        """A cosmetic reference to G must not erase updater risks."""
        path = self.root / "scripts/apply-update.sh"
        path.write_text(path.read_text() + "# G B W A\n")
        self.assertIn("UPDATER_A_LOCK_NOT_GLOBAL_G_B_PROOF", self.codes())

    def test_compose_restart_no_loses_only_one_risk(self) -> None:
        """Disabling auto-restart alone never certifies the release."""
        (self.root / "compose.yaml").write_text(
            "services:\n  writer:\n    restart: no\n"
        )
        self.assertNotIn(
            "AUTONOMOUS_DOCKER_RESTART_COVERAGE_UNVERIFIED",
            self.codes(),
        )

    def test_source_auditor_never_executes_subprocess(self) -> None:
        """No shell, Docker or subprocess calls are allowed in the source."""
        source = SCRIPT.read_text()
        for forbidden in ("subprocess.run(", "os.system(", "docker run"):
            self.assertNotIn(forbidden, source)

    def test_cli_uses_explicit_repo_fixture(self) -> None:
        """The CLI returns a stable NO_GO for known local risks."""
        with (
            patch.object(sys, "argv", ["qa", "--repo-root", str(self.root)]),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            code = AUDIT.main()
        self.assertEqual(code, 3)
        self.assertIn("DETACHED_COMPOSE", output.getvalue())

    def test_help_exits_without_auditing(self) -> None:
        """Standard -h/--help never creates or changes anything."""
        with (
            patch.object(sys, "argv", ["qa", "--help"]),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as result,
        ):
            AUDIT.main()
        self.assertEqual(result.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
