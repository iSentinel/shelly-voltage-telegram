"""Unit safety checks for read-only live legacy owner migration preflight."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/legacy-owner-live-preflight.py"
SPEC = importlib.util.spec_from_file_location(
    "qa_live_owner_preflight", SCRIPT
)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


def sources() -> dict[str, str]:
    """Provide only invented owner scripts used by the read-only auditor."""
    return {
        "compose.yaml": "services:\n  writer:\n    restart: unless-stopped\n",
        "scripts/apply-update.sh": (
            "flock /run/lock/shelly-voltage-updater.lock\n"
        ),
        "scripts/start-compose.sh": "compose_cmd up -d --no-build\n",
        "scripts/install.sh": (
            "docker update --restart=unless-stopped test\n"
        ),
        "scripts/rollback.sh": "restore_source() { :; }\n",
    }


class LegacyLivePreflightTests(unittest.TestCase):
    """No test executes a real Docker or systemd command."""

    def setUp(self) -> None:
        """Create disposable local source fixture and admission path."""
        temp = tempfile.TemporaryDirectory(prefix="live-owner-preflight-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, content in sources().items():
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(content)

    def command_line(self, *args: str) -> int:
        """Avoid all runtime inspection when user has not opted in."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "audit", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """Unadorned CLI never contacts Docker or systemd."""
        self.assertEqual(self.command_line(), 0)

    def test_help_inert(self) -> None:
        """-h/--help exits before inspecting any owner."""
        with self.assertRaises(SystemExit) as result:
            self.command_line("--help")
        self.assertEqual(result.exception.code, 0)

    def test_real_docker_running_restart_is_blocker(self) -> None:
        """A running old container with autonomous restart is NO_GO."""
        with patch.object(
            QA, "run_readonly", return_value="true|unless-stopped\n"
        ):
            findings = QA.docker_findings()
        self.assertEqual(
            findings,
            [
                "OLD_WRITER_NOT_QUIESCENT",
                "DOCKER_AUTORESTART_NOT_FROZEN",
            ],
        )

    def test_stopped_docker_no_restart_only_passes_that_actor(self) -> None:
        """A stopped owner does not certify all other owner paths."""
        with patch.object(QA, "run_readonly", return_value="false|no\n"):
            self.assertEqual(QA.docker_findings(), [])

    def test_failed_docker_inspect_never_safe(self) -> None:
        """Unverifiable Docker is not evidence of no old writers."""
        with patch.object(QA, "run_readonly", return_value=None):
            self.assertIn("DOCKER_INSPECT_UNAVAILABLE", QA.docker_findings())

    def test_bad_docker_output_is_no_go(self) -> None:
        """A partial or malformed state must fail closed."""
        with patch.object(QA, "run_readonly", return_value="true\n"):
            self.assertEqual(
                QA.docker_findings(), ["DOCKER_STATE_UNVERIFIABLE"]
            )

    def test_actual_systemd_prestart_reload_hazards(self) -> None:
        """Systemd prestart recovery and detached starts remain unreviewed."""
        text = (
            "ExecStartPre=/opt/shelly-voltage/scripts/"
            "apply-update.sh --recover\n"
            "ExecStart=/opt/shelly-voltage/scripts/start-compose.sh\n"
            "ExecReload=/opt/shelly-voltage/scripts/start-compose.sh\n"
        )
        with patch.object(QA, "run_readonly", return_value=text):
            problems = QA.systemd_findings()
        self.assertEqual(
            problems,
            [
                "RECOVER_PRESTART_UNGATED",
                "COMPOSE_START_UNGATED",
                "COMPOSE_RELOAD_UNGATED",
            ],
        )

    def test_missing_systemd_unit_fails(self) -> None:
        """No unit output must not be treated as proof of disabled owner."""
        with patch.object(QA, "run_readonly", return_value=None):
            self.assertEqual(
                QA.systemd_findings(), ["SYSTEMD_OWNER_UNVERIFIABLE"]
            )

    def test_clean_looking_systemd_requires_review(self) -> None:
        """Static absence of legacy names is not systemd acceptance."""
        with patch.object(
            QA, "run_readonly", return_value="ExecStart=/tmp/new.sh\n"
        ):
            self.assertEqual(
                QA.systemd_findings(),
                [
                    "SYSTEMD_OWNER_INDEPENDENTLY_UNREVIEWED",
                ],
            )

    def test_known_source_bypasses(self) -> None:
        """All five invented source paths trigger expected live risks."""
        problems = QA.source_findings(self.root)
        self.assertEqual(
            set(problems),
            {
                "COMPOSE_AUTORESTART_UNGATED",
                "UPDATER_RECOVERY_A_ONLY",
                "DETACHED_START_HANDOFF_UNREVIEWED",
                "INSTALLER_OLD_AUTORESTART",
                "ROLLBACK_OLD_SOURCE_UNREVIEWED",
            },
        )

    def test_missing_source_blocks_migration(self) -> None:
        """A removed installer cannot certify it is disabled."""
        (self.root / "scripts/install.sh").unlink()
        self.assertEqual(
            QA.source_findings(self.root), ["OWNER_SOURCE_MISSING_OR_UNSAFE"]
        )

    def test_symlinked_source_blocks_migration(self) -> None:
        """Never follow an external script symlink for signoff."""
        path = self.root / "scripts/rollback.sh"
        path.unlink()
        path.symlink_to(self.root / "scripts/apply-update.sh")
        self.assertIsNone(QA.read_source(self.root, "scripts/rollback.sh"))

    def test_missing_G_inode_blocks_migration(self) -> None:
        """A new G lock must exist as a stable trusted inode."""
        self.assertEqual(
            QA.lock_findings(self.root / "G"),
            ["PRODUCTION_G_INODE_MISSING"],
        )

    def test_untrusted_G_permissions_block(self) -> None:
        """Never trust group-writable admission inode."""
        gate = self.root / "G"
        gate.touch()
        os.chmod(gate, 0o666)
        self.assertTrue(
            set(QA.lock_findings(gate))
            & {
                "PRODUCTION_G_INODE_WRITABLE",
                "PRODUCTION_G_INODE_UNTRUSTED",
            }
        )

    def test_sqlite_backup_same_filesystem_blocks(self) -> None:
        """Two directories on same physical filesystem are not independent."""
        db = self.root / "state.sqlite3"
        db.touch()
        self.assertEqual(
            QA.backup_findings(db, self.root),
            [
                "BACKUP_SAME_FILESYSTEM_AS_CURRENT_SQLITE",
            ],
        )

    def test_backup_different_fs_not_proof_of_physical_independence(
        self,
    ) -> None:
        """Even different st_dev may mean one physical drive."""
        with patch.object(
            QA.Path,
            "stat",
            side_effect=[
                type("Info", (), {"st_dev": 10})(),
                type("Info", (), {"st_dev": 20})(),
            ],
        ):
            self.assertEqual(
                QA.backup_findings(self.root / "db", self.root),
                [
                    "BACKUP_PHYSICAL_INDEPENDENCE_NOT_PROVEN",
                ],
            )

    def test_audit_reports_all_groups_without_mutation(self) -> None:
        """Composed report checks Docker, systemd, G, sources and backups."""
        with (
            patch.object(QA, "docker_findings", return_value=["D"]),
            patch.object(QA, "systemd_findings", return_value=["S"]),
            patch.object(QA, "lock_findings", return_value=["G"]),
            patch.object(QA, "backup_findings", return_value=["B"]),
        ):
            result = QA.audit(self.root)
        self.assertIn(("docker", "D"), result)
        self.assertIn(("systemd", "S"), result)
        self.assertIn(("admission", "G"), result)
        self.assertIn(("backup", "B"), result)
        self.assertTrue(any(group == "source" for group, _ in result))

    def test_all_clean_looking_inspection_still_no_go(self) -> None:
        """No read-only preflight is permitted to authorize a deployment."""
        with contextlib.redirect_stdout(io.StringIO()) as output:
            exit_code = QA.report([])
        self.assertEqual(exit_code, 4)
        self.assertIn("REVIEW_REQUIRED", output.getvalue())

    def test_live_blocker_exit_code_is_three(self) -> None:
        """A risk report must have stable nonzero status and no secrets."""
        with contextlib.redirect_stdout(io.StringIO()) as output:
            exit_code = QA.report([("docker", "AUTORESTART")])
        self.assertEqual(exit_code, 3)
        self.assertIn("NO_GO | docker | AUTORESTART", output.getvalue())

    def test_actual_audit_flag_invokes_inspection(self) -> None:
        """Explicit audit uses the supplied source root."""
        with (
            patch.object(
                sys,
                "argv",
                [
                    "qa",
                    "--audit",
                    "--root",
                    str(self.root),
                ],
            ),
            patch.object(
                QA,
                "audit",
                return_value=[("docker", "OLD_WRITER")],
            ) as called,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 3)
        self.assertEqual(called.call_args.args[0], self.root)


if __name__ == "__main__":
    unittest.main()
