"""Prove coordinated writer-fence QA never operates on production owners."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import call, patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-coordinated-writer-fence-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_multi_fence_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
NAME = "svt-qa-multi-abcdef123456"
IMAGE = "sha256:" + "a" * 64
PRODUCTION = ("b" * 64, "0", "true", IMAGE)
FENCED = ("false", "no", "none", "1")


class CoordinatedFenceTests(unittest.TestCase):
    """Use mocks for Docker/systemd and real private synthetic SQLite."""

    def setUp(self) -> None:
        """Create exactly one test-only SQLite and README scratch fixture."""
        folder = tempfile.TemporaryDirectory(
            prefix="svt-coordinated-unit-", dir="/tmp"
        )
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        QA.create_fixture(self.root)

    def invoke(self, *args: str) -> int:
        """Call the confirmation-guarded CLI without host side effects."""
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_default_dry_run_never_calls_host(self) -> None:
        """Inert default mode must not touch Docker or systemd."""
        with patch.object(QA, "run", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke("--dry-run"), 0)

    def test_requires_exact_opt_in(self) -> None:
        """Any incomplete confirmation must return BLOCKED."""
        with patch.object(QA, "execute", side_effect=AssertionError):
            self.assertEqual(self.invoke("--run-disposable"), 2)
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_confirmed_mode_calls_disposable_runner(self) -> None:
        """Only exact QA consent can invoke the integration."""
        with patch.object(QA, "execute") as runner:
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", QA.CONFIRM),
                0,
            )
            runner.assert_called_once_with()

    def test_production_names_cannot_be_selected(self) -> None:
        """Restrict Docker and systemd to the generated QA prefix."""
        for name in (
            "shelly-voltage-bot",
            "svt-qa-multi-012",
            "svt-qa-multi-abcdef123456;rm",
            "service",
        ):
            with (
                self.subTest(name=name),
                self.assertRaises(QA.Blocked),
            ):
                QA.safe_name(name)
        QA.safe_name(NAME)

    def test_unit_names_are_allowlisted(self) -> None:
        """No production updater or Compose timer can be targeted."""
        with self.assertRaises(QA.Blocked):
            QA.unit(NAME, "updater")
        self.assertEqual(QA.unit(NAME, "restart"), NAME + "-restart")

    def test_unapproved_docker_actions_rejected(self) -> None:
        """The Docker wrapper cannot remove production objects."""
        with self.assertRaises(QA.Blocked):
            QA.docker(NAME, "rm", "-f")
        with self.assertRaises(QA.Blocked):
            QA.docker("shelly-voltage-bot", "stop")

    def test_synthetic_sqlite_fully_hashed(self) -> None:
        """Initial fixture includes measurements and extra tables."""
        first = QA.db_identity(self.root / "history.sqlite3")
        self.assertEqual(first[1], 120)
        self.assertEqual(len(first[0]), 64)

    def test_schema_drift_changes_history_hash(self) -> None:
        """A same-count metadata edit must break history identity."""
        before = QA.db_identity(self.root / "history.sqlite3")
        with closing(sqlite3.connect(self.root / "history.sqlite3")) as db:
            db.execute("UPDATE meta SET value='changed'")
            db.commit()
        self.assertNotEqual(
            QA.db_identity(self.root / "history.sqlite3"), before
        )

    def test_source_marker_guards_synthetic_source(self) -> None:
        """The QA source marker may not be altered."""
        self.assertEqual(len(QA.source_identity(self.root)), 64)
        (self.root / "source/README.txt").write_text("not QA")
        with self.assertRaises(QA.Blocked):
            QA.source_identity(self.root)

    def test_create_container_has_only_one_qa_mount(self) -> None:
        """No live SQLite, ports, environment or privileged access."""
        with (
            patch.object(QA, "run", return_value="") as execute,
            patch.object(
                QA,
                "docker_state",
                return_value=("false", "unless-stopped", "none", "1"),
            ),
        ):
            QA.create_container(NAME, IMAGE, self.root)
            args = execute.call_args.args[0]
        self.assertEqual(args[0:2], ["docker", "create"])
        self.assertIn("--network", args)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn(f"type=bind,src={self.root},dst=/qa", args)
        for prohibited in ("-v", "-p", "--publish", "--env", "--privileged"):
            self.assertNotIn(prohibited, args)
        self.assertIn("--cap-drop", args)
        self.assertIn("--read-only", args)

    def test_supervisor_execstop_targets_only_test_writer(self) -> None:
        """The transient supervisor cannot stop a real Compose service."""
        with patch.object(QA, "run", return_value="") as execute:
            QA.start_supervisor(NAME)
        args = execute.call_args.args[0]
        self.assertIn(
            f"--property=ExecStop=/usr/bin/docker stop -t 3 {NAME}",
            args,
        )
        self.assertNotIn("shelly-voltage-compose.service", str(args))

    def test_timer_only_restarts_test_writer(self) -> None:
        """The timer is precise and points only at the QA name."""
        with patch.object(QA, "run", return_value="") as execute:
            QA.arm_external_timer(NAME)
        args = execute.call_args.args[0]
        self.assertIn("--timer-property=AccuracySec=1ms", args)
        self.assertIn("--timer-property=RandomizedDelaySec=0", args)
        self.assertEqual(args[-3:], ["/usr/bin/docker", "start", NAME])

    def test_stop_owners_before_docker_stop(self) -> None:
        """Timer and supervisor are stopped before restart=no and stop."""
        with (
            patch.object(QA, "run") as process,
            patch.object(QA, "docker") as docker,
        ):
            QA.stop_owners(NAME)
        self.assertEqual(process.call_count, 2)
        self.assertIn(
            NAME + "-restart.timer", process.call_args_list[0].args[0]
        )
        self.assertIn(
            NAME + "-supervisor.service",
            process.call_args_list[1].args[0],
        )
        self.assertEqual(
            docker.call_args_list,
            [
                call(NAME, "update", "--restart=no"),
                call(NAME, "stop", "-t", "3"),
            ],
        )

    def test_running_writer_fails_backup_fence(self) -> None:
        """A running writer may never enter the A/B capture."""
        with (
            patch.object(
                QA,
                "docker_state",
                return_value=(
                    "true",
                    "no",
                    "none",
                    "1",
                ),
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.assert_fenced(NAME, self.root)

    def test_active_external_timer_blocks_snapshot(self) -> None:
        """A timer can restart a currently stopped writer."""
        with (
            patch.object(QA, "docker_state", return_value=FENCED),
            patch.object(QA, "timer_state", return_value="active"),
            patch.object(QA, "supervisor_state", return_value="inactive"),
            self.assertRaises(QA.Blocked),
        ):
            QA.assert_fenced(NAME, self.root)

    def test_active_supervisor_blocks_snapshot(self) -> None:
        """A supervisor that could recreate a writer must be fenced."""
        with (
            patch.object(QA, "docker_state", return_value=FENCED),
            patch.object(QA, "timer_state", return_value="inactive"),
            patch.object(QA, "supervisor_state", return_value="active"),
            self.assertRaises(QA.Blocked),
        ):
            QA.assert_fenced(NAME, self.root)

    def test_real_scratch_a_b_restores_same_history(self) -> None:
        """Two backup API snapshots must match entire synthetic history."""
        with (
            patch.object(QA, "docker_state", return_value=FENCED),
            patch.object(QA, "timer_state", return_value="inactive"),
            patch.object(QA, "supervisor_state", return_value="inactive"),
            patch.object(QA.time, "sleep"),
        ):
            rows = QA.backup_pair(NAME, self.root)
        self.assertEqual(rows, 120)
        original = QA.db_identity(self.root / "history.sqlite3")
        for name in ("A", "B"):
            archive = self.root / f"snapshots/{name}/history.sqlite3"
            self.assertEqual(QA.db_identity(archive), original)

    def test_backup_fails_on_history_drift(self) -> None:
        """A changed digest must not produce a successful archive pair."""
        initial = QA.db_identity(self.root / "history.sqlite3")
        altered = ("f" * 64, initial[1])
        with (
            patch.object(QA, "assert_fenced", side_effect=[initial, altered]),
            self.assertRaises(QA.Blocked),
        ):
            QA.backup_pair(NAME, self.root)

    def test_resume_rejects_an_unfenced_container(self) -> None:
        """A controlled start requires policy no and stopped writer."""
        with (
            patch.object(
                QA,
                "docker_state",
                return_value=(
                    "false",
                    "unless-stopped",
                    "none",
                    "1",
                ),
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.controlled_resume(NAME, self.root, 120)

    def test_cleanup_only_generated_names(self) -> None:
        """No production service, timer or container is stopped."""
        with (
            patch.object(QA, "run", return_value="") as commands,
            patch.object(QA, "stop_owners"),
        ):
            QA.cleanup(NAME)
        for entry in commands.call_args_list:
            self.assertNotIn("shelly-voltage-compose.service", str(entry))
            self.assertNotIn("shelly-voltage-update-check.timer", str(entry))
        self.assertEqual(
            commands.call_args_list[-1].args[0],
            ["docker", "rm", "-f", NAME],
        )

    def test_orphan_container_blocks_acceptance(self) -> None:
        """Fail closed if the QA container could not be removed."""
        with (
            patch.object(QA, "run", return_value="notempty"),
            self.assertRaises(QA.Blocked),
        ):
            QA.verify_cleanup(NAME)

    def test_subprocess_failures_propagate(self) -> None:
        """Systemd or Docker command failures are not treated as success."""
        proc = subprocess.CompletedProcess(
            args=["docker"], returncode=2, stdout="", stderr=""
        )
        with (
            patch.object(subprocess, "run", return_value=proc),
            self.assertRaises(QA.Blocked),
        ):
            QA.run(["docker", "stop"])

    def test_execution_cleans_after_fault(self) -> None:
        """An injected test crash must still clean generated resources."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(QA, "production_owners", return_value=("a", "b")),
            patch.object(
                QA,
                "create_container",
                side_effect=QA.Blocked("SIMULATED_FAILURE"),
            ),
            patch.object(QA, "cleanup") as cleanup,
            patch.object(QA, "verify_cleanup"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_changed_production_identity_blocks_success(self) -> None:
        """An unexpected live restart invalidates disposable acceptance."""
        changed = ("c" * 64, "1", "true", IMAGE)
        with (
            patch.object(QA, "prerequisites"),
            patch.object(
                QA, "production_identity", side_effect=[PRODUCTION, changed]
            ),
            patch.object(QA, "production_owners", return_value=("a", "b")),
            patch.object(QA, "create_container"),
            patch.object(QA, "exercise"),
            patch.object(QA, "cleanup"),
            patch.object(QA, "verify_cleanup"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()

    def test_supervisor_exit_waits_until_no_longer_active(self) -> None:
        """The old supervisor must finish before starting a new timer."""
        with (
            patch.object(
                QA, "supervisor_state", side_effect=["active", "inactive"]
            ),
            patch.object(QA.time, "sleep") as sleep,
        ):
            QA.await_supervisor_exit(NAME)
        sleep.assert_called_once()

    def test_supervisor_still_active_blocks_restart(self) -> None:
        """Indefinitely active supervisor must be reported as unsafe."""
        with (
            patch.object(QA, "supervisor_state", return_value="active"),
            patch.object(QA.time, "monotonic", side_effect=[0, 1, 9]),
            patch.object(QA.time, "sleep"),
            self.assertRaises(QA.Blocked),
        ):
            QA.await_supervisor_exit(NAME)

    def test_timer_is_armed_after_supervisor_has_exited(self) -> None:
        """No timer restart may race an incomplete QA ExecStop."""
        events = []

        def record(label: str) -> None:
            events.append(label)

        with (
            patch.object(
                QA,
                "start_supervisor",
                side_effect=lambda _: record("supervisor"),
            ),
            patch.object(QA, "await_growth", return_value=121),
            patch.object(
                QA, "docker", side_effect=lambda *args: record("docker-stop")
            ),
            patch.object(
                QA,
                "await_supervisor_exit",
                side_effect=lambda _: record("supervisor-exit"),
            ),
            patch.object(
                QA, "arm_external_timer", side_effect=lambda _: record("timer")
            ),
            patch.object(QA, "await_timer_restart", return_value=122),
            patch.object(QA, "stop_owners"),
            patch.object(QA, "backup_pair", return_value=122),
            patch.object(QA, "controlled_resume", return_value=123),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            QA.exercise(NAME, self.root)
        self.assertEqual(
            events, ["supervisor", "docker-stop", "supervisor-exit", "timer"]
        )

    def test_stopped_writer_with_restart_policy_is_not_fenced(self):
        """Docker restart policy must be no before recording any A/B."""
        with (
            patch.object(
                QA,
                "docker_state",
                return_value=("false", "unless-stopped", "none", "1"),
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.assert_fenced(NAME, self.root)

    def test_readonly_production_owner_inspection(self) -> None:
        """Real services and timers are only inspected, never modified."""
        with patch.object(QA, "run", return_value="state") as runner:
            self.assertEqual(QA.production_owners(), ("state", "state"))
        args = [entry.args[0] for entry in runner.call_args_list]
        self.assertEqual(len(args), 2)
        self.assertIn("shelly-voltage-compose.service", args[0])
        self.assertIn("shelly-voltage-update-check.timer", args[1])
        self.assertTrue(all(cmd[:2] == ["systemctl", "show"] for cmd in args))

    def test_production_systemd_owner_drift_is_rejected(self) -> None:
        """Changing real Compose or updater state invalidates QA results."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(
                QA,
                "production_owners",
                side_effect=[("active", "enabled"), ("inactive", "enabled")],
            ),
            patch.object(QA, "create_container"),
            patch.object(QA, "exercise"),
            patch.object(QA, "cleanup"),
            patch.object(QA, "verify_cleanup"),
            self.assertRaises(QA.Blocked),
        ):
            QA.execute()

    def test_cleanup_attempts_every_generated_unit(self) -> None:
        """Every QA timer, supervisor and Docker object is cleaned."""
        with patch.object(QA, "run", return_value="") as runner:
            QA.cleanup(NAME)
        args = [entry.args[0] for entry in runner.call_args_list]
        self.assertEqual(len(args), 8)
        self.assertEqual(args[-1], ["docker", "rm", "-f", NAME])
        self.assertTrue(
            all(
                "shelly-voltage-compose.service" not in str(cmd)
                for cmd in args
            )
        )


if __name__ == "__main__":
    unittest.main()
