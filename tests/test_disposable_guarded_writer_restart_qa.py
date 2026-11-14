"""QA guarded restart must never target a real production writer."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-guarded-writer-restart-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_guarded_disposable", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)

GOOD_IMAGE = QA.IMAGE
GOOD_COMPOSE = {
    "services": {
        "writer": {
            "container_name": QA.NAME,
            "restart": "no",
            "network_mode": "none",
        }
    }
}


class DisposableGuardedWriterRestartTests(unittest.TestCase):
    """Use only synthetic SQLite and mocks; never contact live Docker."""

    def setUp(self) -> None:
        """Allocate an isolated private QA fixture and read-only DB."""
        directory = tempfile.TemporaryDirectory(
            prefix="svt-qa-guarded-unit-",
            dir="/tmp",
        )
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "data").mkdir()
        (self.root / "state").mkdir()
        with (
            closing(sqlite3.connect(self.root / "data/history.sqlite3")) as db,
            db,
        ):
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("CREATE TABLE measurements(id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO meta VALUES('schema_version','3')")
            db.execute("INSERT INTO measurements(id) VALUES(1)")
        patcher = patch.object(QA, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *args: str) -> tuple[int, str]:
        """Capture only the local QA CLI result."""
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
        ):
            return QA.main(), output.getvalue()

    def test_default_is_inert(self) -> None:
        """No command can run without an explicit acceptance token."""
        with patch.object(QA, "restart_if_safe", side_effect=AssertionError):
            self.assertEqual(self.cli()[0], 0)

    def test_dry_run_is_inert_with_run_flag(self) -> None:
        """Explicit dry-run must win over the run flag."""
        with patch.object(QA, "restart_if_safe", side_effect=AssertionError):
            code, _ = self.cli("--dry-run", "--run-disposable")
        self.assertEqual(code, 0)

    def test_wrong_consent_refused(self) -> None:
        """Generic confirmation is never accepted."""
        with patch.object(QA, "restart_if_safe", side_effect=AssertionError):
            code, _ = self.cli("--run-disposable", "--confirm", "yes")
        self.assertEqual(code, 2)

    def test_exact_consent_calls_only_qa_runner(self) -> None:
        """Any success explicitly excludes production authorization."""
        with patch.object(
            QA, "restart_if_safe", return_value={"status": "SKIP_PENDING"}
        ) as runner:
            code, output = self.cli(
                "--run-disposable", "--confirm", QA.CONFIRM
            )
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["scope"], "DISPOSABLE_QA_LXC114_ONLY")
        self.assertFalse(payload["production_modified"])
        self.assertFalse(payload["boot_start_authorized"])
        runner.assert_called_once_with()

    def test_host_must_be_exact_qa114(self) -> None:
        """Copying script into a real production guest must block."""
        with (
            patch.object(QA.socket, "gethostname", return_value="production"),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_target()

    def test_no_network_interface_except_loopback(self) -> None:
        """An unexpected eth0 blocks QA restart."""
        with (
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_qa_systemd_owner()

    def test_disabled_qa_unit_refused(self) -> None:
        """The known systemd owner must remain enabled."""
        with (
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(QA, "command", return_value="disabled"),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_qa_systemd_owner()

    def test_unrecognized_qa_unit_refused(self) -> None:
        """A dummy systemd unit cannot take over restart delegation."""
        with (
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(
                QA,
                "command",
                side_effect=["enabled", "ExecStartPre=/bin/true"],
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_qa_systemd_owner()

    def test_expected_qa_unit_observed(self) -> None:
        """Exact prestart, Compose path and Docker executable must match."""
        good = (
            "ExecStartPre="
            + str(self.root / "prestart.py")
            + "\nExecStart=/usr/bin/docker compose -f "
            + str(self.root / "compose.yaml")
        )
        with (
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(QA, "command", side_effect=["enabled", good]),
        ):
            QA.check_qa_systemd_owner()

    def test_wrong_image_id_refused(self) -> None:
        """Only the exact staged signed OCI image ID can be restarted."""
        with (
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA, "check_qa_systemd_owner"),
            patch.object(QA, "inspect", return_value="sha256:wrong"),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_target()

    def test_wrong_docker_restart_policy_refused(self) -> None:
        """Docker on-failure and unless-stopped cannot be delegated."""
        with (
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA, "check_qa_systemd_owner"),
            patch.object(
                QA,
                "inspect",
                side_effect=[GOOD_IMAGE, "on-failure"],
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_target()

    def test_docker_network_exposure_refused(self) -> None:
        """Even a trusted image cannot run with bridge networking."""
        with (
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA, "check_qa_systemd_owner"),
            patch.object(
                QA,
                "inspect",
                side_effect=[GOOD_IMAGE, "no", "bridge"],
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_target()

    def test_compose_restart_unless_stopped_refused(self) -> None:
        """Compose may re-enable unsafe Docker restart after recreation."""
        bad = {
            "services": {
                "writer": {
                    "container_name": QA.NAME,
                    "restart": "unless-stopped",
                    "network_mode": "none",
                }
            }
        }
        self.assert_target_rejected_with_compose(bad)

    def test_compose_wrong_container_name_refused(self) -> None:
        """Never select production or another QA service name."""
        bad = {
            "services": {
                "writer": {
                    "container_name": "shelly-voltage-bot",
                    "restart": "no",
                    "network_mode": "none",
                }
            }
        }
        self.assert_target_rejected_with_compose(bad)

    def test_compose_ports_refused(self) -> None:
        """A QA replica may not expose any port."""
        bad = {
            "services": {
                "writer": {
                    "container_name": QA.NAME,
                    "restart": "no",
                    "network_mode": "none",
                    "ports": ["8080:8080"],
                }
            }
        }
        self.assert_target_rejected_with_compose(bad)

    def assert_target_rejected_with_compose(self, plan: dict) -> None:
        """Provide exact Docker observations but an unsafe Compose."""
        with (
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA, "check_qa_systemd_owner"),
            patch.object(
                QA,
                "inspect",
                side_effect=[GOOD_IMAGE, "no", "none"],
            ),
            patch.object(QA, "command", return_value=json.dumps(plan)),
            self.assertRaises(QA.Blocked),
        ):
            QA.check_target()

    def safe_compose(self) -> dict:
        """Represent only the fixed synthetic writer and its private bind."""
        return {
            "image": "svt-qa-verified-local:0.5.21-hw",
            "pull_policy": "never",
            "read_only": True,
            "user": "10001:10001",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "volumes": [
                {
                    "type": "bind",
                    "source": str(self.root / "data"),
                    "target": "/data",
                }
            ],
            "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
            "command": [
                "python",
                "-c",
                "import app;storage=app.Storage;qa_writer_starts=1",
            ],
        }

    def test_exact_synthetic_compose_hardening_accepts(self) -> None:
        """The expected immutable image and sole QA bind are acceptable."""
        QA.check_qa_compose_hardening(self.safe_compose())

    def test_extra_bind_mount_rejected(self) -> None:
        """A bind to any other location must block the QA restart."""
        plan = self.safe_compose()
        plan["volumes"].append(
            {
                "type": "bind",
                "source": "/var/lib/shelly-voltage",
                "target": "/production",
            }
        )
        with self.assertRaises(QA.Blocked):
            QA.check_qa_compose_hardening(plan)

    def test_extra_secret_or_environment_rejected(self) -> None:
        """No credentials may be added to the disposable Storage writer."""
        plan = self.safe_compose()
        plan["environment"]["TOKEN"] = "redacted"
        with self.assertRaises(QA.Blocked):
            QA.check_qa_compose_hardening(plan)

    def test_default_monitor_command_rejected(self) -> None:
        """A QA runner must never launch the actual Telegram monitor."""
        plan = self.safe_compose()
        plan["command"] = ["python", "/app/app.py", "run"]
        with self.assertRaises(QA.Blocked):
            QA.check_qa_compose_hardening(plan)

    def test_existing_sqlite_row_count_observed_without_write(self) -> None:
        """The current synthetic DB must be observed with mode=ro."""
        path = self.root / "data/history.sqlite3"
        before = path.read_bytes()
        self.assertEqual(QA.check_sqlite(), 1)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_sqlite_fails(self) -> None:
        """No implicit SQLite creation during restart gating."""
        path = self.root / "data/history.sqlite3"
        path.unlink()
        with self.assertRaises(QA.Blocked):
            QA.check_sqlite()
        self.assertFalse(path.exists())

    def test_symlink_sqlite_fails(self) -> None:
        """No path redirection outside private QA fixture."""
        path = self.root / "data/history.sqlite3"
        alias = self.root / "alias.sqlite3"
        path.rename(alias)
        path.symlink_to(alias)
        with self.assertRaises(QA.Blocked):
            QA.check_sqlite()

    def test_future_schema_fails(self) -> None:
        """Signed image v3 must not open future v4 history."""
        with (
            closing(sqlite3.connect(self.root / "data/history.sqlite3")) as db,
            db,
        ):
            db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
        with self.assertRaises(QA.Blocked):
            QA.check_sqlite()

    def test_pending_blocks_delegate(self) -> None:
        """A fake pending marker prevents even a single QA restart."""
        (self.root / "state/pending-update").write_text("synthetic\n")
        with (
            patch.object(QA, "check_target", return_value={"running": False}),
            patch.object(QA, "command", side_effect=AssertionError),
        ):
            self.assertEqual(QA.restart_if_safe()["status"], "SKIP_PENDING")

    def test_running_writer_is_idempotent(self) -> None:
        """Never create a second writer when a healthy one exists."""
        with (
            patch.object(QA, "check_target", return_value={"running": True}),
            patch.object(QA, "command", side_effect=AssertionError),
        ):
            self.assertEqual(
                QA.restart_if_safe()["status"], "SKIP_ALREADY_RUNNING"
            )

    def test_pending_skip_does_not_open_sqlite(self) -> None:
        """A stopped writer with pending needs no SQLite sidecar access."""
        (self.root / "state/pending-update").write_text("synthetic\n")
        with (
            patch.object(QA, "check_target", return_value={"running": False}),
            patch.object(QA, "check_sqlite", side_effect=AssertionError),
        ):
            self.assertEqual(QA.evaluate()["status"], "SKIP_PENDING")

    def test_healthy_writer_skip_does_not_open_sqlite(self) -> None:
        """Recurring watchdog ticks must not open live WAL while running."""
        with (
            patch.object(QA, "check_target", return_value={"running": True}),
            patch.object(QA, "check_sqlite", side_effect=AssertionError),
        ):
            self.assertEqual(QA.evaluate()["status"], "SKIP_ALREADY_RUNNING")

    def test_pending_with_running_writer_is_explicit_blocker(self) -> None:
        """Do not hide the competing-start owner race behind SKIP_PENDING."""
        (self.root / "state/pending-update").write_text("synthetic\n")
        with (
            patch.object(QA, "check_target", return_value={"running": True}),
            patch.object(QA, "check_sqlite", side_effect=AssertionError),
            self.assertRaisesRegex(
                QA.Blocked,
                "PENDING_BUT_QA_WRITER_RUNNING",
            ),
        ):
            QA.evaluate()

    def test_stopped_without_pending_checks_sqlite_before_start(self) -> None:
        """Only a valid stopped-writer database may be restarted."""
        with (
            patch.object(QA, "check_target", return_value={"running": False}),
            patch.object(QA, "check_sqlite", return_value=12) as check,
        ):
            result = QA.evaluate()
        check.assert_called_once_with()
        self.assertEqual(
            result,
            {
                "status": "QA_RESTART_CANDIDATE",
                "samples": 12,
            },
        )

    def test_pending_race_recheck_blocks(self) -> None:
        """A marker appearing after the first check blocks restart."""
        with patch.object(
            QA, "evaluate", return_value={"status": "QA_RESTART_CANDIDATE"}
        ):
            marker = self.root / "state/pending-update"
            marker.write_text("new pending\n")
            with patch.object(QA, "command", side_effect=AssertionError):
                self.assertEqual(
                    QA.restart_if_safe()["status"], "SKIP_PENDING_RACE"
                )

    def test_exact_qa_unit_delegation_only(self) -> None:
        """Restart only svt-qa-compose.service, never Docker directly."""
        with (
            patch.object(
                QA,
                "evaluate",
                return_value={"status": "QA_RESTART_CANDIDATE"},
            ),
            patch.object(QA, "command", return_value="") as run,
            patch.object(QA, "inspect", return_value="true"),
        ):
            state = QA.restart_if_safe()
        self.assertEqual(state["status"], "QA_SYSTEMD_RESTARTED")
        run.assert_called_once_with(
            ["systemctl", "restart", "svt-qa-compose.service"]
        )

    def test_failed_systemctl_does_not_claim_restart(self) -> None:
        """Never report success when delegated systemd restart fails."""
        with (
            patch.object(
                QA,
                "evaluate",
                return_value={"status": "QA_RESTART_CANDIDATE"},
            ),
            patch.object(QA, "command", side_effect=QA.Blocked),
            self.assertRaises(QA.Blocked),
        ):
            QA.restart_if_safe()

    def test_command_failure_is_blocked(self) -> None:
        """No optional fallback to direct Docker on systemctl failure."""
        result = subprocess.CompletedProcess(
            args=["systemctl"],
            returncode=1,
            stdout="",
            stderr="error",
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["systemctl", "restart", QA.UNIT])

    def test_command_timeout_is_blocked(self) -> None:
        """No unlimited hang on broken daemon."""
        with (
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("docker", 10),
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["docker", "inspect"])

    def test_cli_error_has_stable_exit_code(self) -> None:
        """A blocked QA path returns exit 3, never a success response."""
        with patch.object(QA, "restart_if_safe", side_effect=QA.Blocked):
            code, text = self.cli("--run-disposable", "--confirm", QA.CONFIRM)
        self.assertEqual(code, 3)
        self.assertIn("BLOCKED", text)

    def test_help_is_available(self) -> None:
        """-h and --help work without Docker."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--run-disposable", proc.stdout)


if __name__ == "__main__":
    unittest.main()
