"""Safeguard disposable systemd/Docker SQLite writer regression commands."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import call, patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-supervised-writer-regression-qa.py"
)
SPEC = importlib.util.spec_from_file_location(
    "svt_writer_regression_qa", SCRIPT
)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
NAME = "svt-qa-db-abcdef123456"
IMAGE = "sha256:" + "a" * 64
PRODUCTION = ("b" * 64, "0", "true", IMAGE)


class SupervisedWriterRegressionTests(unittest.TestCase):
    """Exercise isolation, real scratch archive parity and failure gates."""

    def setUp(self) -> None:
        """Build a private disposable SQLite fixture."""
        temporary = tempfile.TemporaryDirectory(
            dir="/tmp", prefix="svt-writer-unit-qa-"
        )
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        QA.prepare_fixture(self.root)

    def invoke(self, *options: str) -> int:
        """Run the CLI with redirected output and arguments."""
        with (
            patch.object(sys, "argv", [str(SCRIPT), *options]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return QA.main()

    def test_default_dry_run_never_executes_commands(self) -> None:
        """Without approval, the script performs no host operations."""
        with patch.object(QA, "run", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)
            self.assertEqual(self.invoke("--dry-run"), 0)

    def test_confirmation_is_exact(self) -> None:
        """No partial confirmation is enough for the real QA test."""
        with patch.object(QA, "execute", side_effect=AssertionError):
            self.assertEqual(self.invoke("--run-disposable"), 2)
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_exact_confirmation_can_invoke_disposable(self) -> None:
        """The only permitted flag combination calls the QA runner."""
        with patch.object(QA, "execute") as execute:
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", QA.CONFIRM),
                0,
            )
            execute.assert_called_once_with()

    def test_only_generated_container_names(self) -> None:
        """Forbid production names, shell payloads and inconsistent IDs."""
        for candidate in (
            "shelly-voltage-bot",
            "svt-qa-db-123",
            "svt-qa-db-abcdef123456;sudo",
            "another-container",
        ):
            with (
                self.subTest(candidate=candidate),
                self.assertRaises(QA.QAError),
            ):
                QA.safe_name(candidate)
        QA.safe_name(NAME)

    def test_only_generated_unit_names(self) -> None:
        """Reject any attempt to target Compose or unapproved phases."""
        with self.assertRaises(QA.QAError):
            QA.unit_name(NAME, "production")
        with self.assertRaises(QA.QAError):
            QA.unit_name("shelly-voltage-bot", "graceful")
        self.assertEqual(QA.unit_name(NAME, "kill"), NAME + "-kill.service")

    def test_writer_container_isolation_is_explicit(self) -> None:
        """Command has exactly one QA bind, no network or ports."""
        args = QA.writer_creation_args(NAME, IMAGE, self.root)
        self.assertEqual(args[:2], ["docker", "create"])
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(
            args[args.index("--mount") + 1],
            f"type=bind,src={self.root},dst=/qa",
        )
        for prohibited in ("-v", "-p", "--publish", "--env"):
            self.assertNotIn(prohibited, args)
        self.assertIn("--read-only", args)
        self.assertIn("no-new-privileges", args)
        self.assertIn("--cap-drop", args)
        self.assertEqual(args[args.index("--entrypoint") + 1], "python3")

    def test_mutable_image_tag_is_rejected(self) -> None:
        """No mutable tag or new image pull can be requested."""
        with self.assertRaises(QA.QAError):
            QA.writer_creation_args(NAME, "shelly-voltage:latest", self.root)

    def test_docker_actions_are_allowlisted(self) -> None:
        """The wrapper refuses removing real containers."""
        with self.assertRaises(QA.QAError):
            QA.docker(NAME, "rm", "-f")
        with self.assertRaises(QA.QAError):
            QA.docker("shelly-voltage-bot", "stop")

    def test_real_scratch_db_integrity(self) -> None:
        """The fixture contains 120 rows and no Shelly production data."""
        digest, rows = QA.db_digest(self.root / "history.sqlite3")
        self.assertEqual(rows, 120)
        self.assertEqual(len(digest), 64)

    def test_real_scratch_a_b_backup_parity(self) -> None:
        """Two SQLite backup API copies fully match the synthetic source."""
        original = QA.db_digest(self.root / "history.sqlite3")
        for copy in ("A", "B"):
            self.assertEqual(
                QA.capture_one(self.root, "graceful", copy), original
            )
            self.assertEqual(
                QA.verify_copy(self.root, "graceful", copy), original
            )

    def test_archive_corruption_is_detected(self) -> None:
        """Bad gzip output cannot be accepted as recovered history."""
        QA.capture_one(self.root, "kill", "A")
        archive = self.root / "snapshots/kill/A/history.sqlite3.gz"
        archive.write_bytes(b"bad gzip")
        with self.assertRaises(OSError):
            QA.verify_copy(self.root, "kill", "A")

    def test_corrupt_synthetic_source_content_is_detected(self) -> None:
        """Source bytes, not only tar members, must match the QA marker."""
        (self.root / "source/README.txt").write_text("modified source")
        QA.capture_one(self.root, "kill", "A")
        with self.assertRaises(QA.QAError):
            QA.verify_copy(self.root, "kill", "A")

    def test_decompression_size_limit_is_enforced(self) -> None:
        """Reject an unexpectedly expanded test SQLite archive."""
        QA.capture_one(self.root, "kill", "B")
        with (
            patch.object(QA, "MAX_ARCHIVE_BYTES", 100),
            self.assertRaises(QA.QAError),
        ):
            QA.verify_copy(self.root, "kill", "B")

    def test_malformed_tar_archive_is_rejected(self) -> None:
        """Corrupt source bytes may never be accepted as valid QA."""
        QA.capture_one(self.root, "kill", "A")
        archive = self.root / "snapshots/kill/A/source.tar.gz"
        archive.write_bytes(b"corrupt tar data")
        with self.assertRaises(tarfile.TarError):
            QA.verify_copy(self.root, "kill", "A")

    def test_tar_archive_failure_still_cleans(self) -> None:
        """A failed tar read must not leave generated QA resources."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(QA, "create_writer"),
            patch.object(
                QA,
                "supervise",
                side_effect=tarfile.ReadError("CORRUPT_QA_ARCHIVE"),
            ),
            patch.object(QA, "cleanup") as cleanup,
            patch.object(QA, "ensure_no_orphans"),
            self.assertRaises(QA.QAError),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_truncated_gzip_failure_still_cleans(self) -> None:
        """A truncated gzip must be a controlled QA failure."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(QA, "create_writer"),
            patch.object(
                QA,
                "supervise",
                side_effect=EOFError("TRUNCATED_QA_GZIP"),
            ),
            patch.object(QA, "cleanup") as cleanup,
            patch.object(QA, "ensure_no_orphans"),
            self.assertRaises(QA.QAError),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_logical_data_drift_changes_digest(self) -> None:
        """Any synthetic measurement change invalidates parity."""
        before = QA.db_digest(self.root / "history.sqlite3")
        with closing(sqlite3.connect(self.root / "history.sqlite3")) as db:
            db.execute("UPDATE measurements SET voltage=123 WHERE id=1")
            db.commit()
        self.assertNotEqual(
            before, QA.db_digest(self.root / "history.sqlite3")
        )

    def test_synthetic_source_archive_must_be_present(self) -> None:
        """Do not accept a backup pair lacking its QA source archive."""
        QA.capture_one(self.root, "kill", "B")
        archive = self.root / "snapshots/kill/B/source.tar.gz"
        archive.unlink()
        with self.assertRaises(OSError):
            QA.verify_copy(self.root, "kill", "B")

    def test_writer_fence_rejects_running_container(self) -> None:
        """No backup can proceed while the QA writer is running."""
        with (
            patch.object(QA, "state", return_value=("true", "no", "none")),
            self.assertRaises(QA.QAError),
        ):
            QA.assert_stopped(NAME, self.root)

    def test_full_pair_works_when_stopped(self) -> None:
        """Capture both synthetic backups after verifying the fence."""
        with (
            patch.object(QA, "state", return_value=("false", "no", "none")),
            patch.object(QA.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.capture_pair(NAME, self.root, "kill"), 120)

    def test_transient_service_execstop_targets_only_qa(self) -> None:
        """The generated systemd unit may stop only the generated writer."""
        with patch.object(QA, "run", return_value="") as runner:
            unit = QA.start_unit(NAME, "graceful")
        args = runner.call_args.args[0]
        self.assertEqual(unit, NAME + "-graceful.service")
        self.assertIn(f"ExecStop=/usr/bin/docker stop -t 3 {NAME}", args)
        self.assertIn("--collect", args)
        self.assertNotIn("shelly-voltage-compose.service", args)

    def test_kill_is_neutralized_before_signal(self) -> None:
        """A SIGKILL cannot be sent before disabling Docker auto-restart."""
        with (
            patch.object(QA, "docker") as docker,
            patch.object(QA, "run") as runner,
            patch.object(QA, "assert_stopped", return_value=130),
        ):
            count = QA.stop_abrupt(NAME, NAME + "-kill.service", self.root)
        self.assertEqual(count, 130)
        self.assertEqual(
            docker.call_args_list,
            [
                call(NAME, "update", "--restart=no"),
                call(NAME, "kill", "--signal", "KILL"),
            ],
        )
        runner.assert_called_once()

    def test_cleanup_only_generated_resources(self) -> None:
        """Cleanup does not inspect, restart or stop production."""
        units = [QA.unit_name(NAME, "graceful")]
        with patch.object(QA, "run") as runner:
            QA.cleanup(NAME, units)
        self.assertEqual(
            [x.args[0] for x in runner.call_args_list],
            [
                ["systemctl", "stop", units[0]],
                ["systemctl", "reset-failed", units[0]],
                ["docker", "rm", "-f", NAME],
            ],
        )

    def test_extra_cleanup_unit_is_blocked(self) -> None:
        """Unexpected service names cannot reach systemctl stop."""
        with (
            patch.object(QA, "run", side_effect=AssertionError),
            self.assertRaises(QA.QAError),
        ):
            QA.cleanup(NAME, ["shelly-voltage-compose.service"])

    def test_failed_supervision_still_cleans(self) -> None:
        """No aborted QA execution may bypass cleanup."""
        with (
            patch.object(QA, "prerequisites"),
            patch.object(QA, "production_identity", return_value=PRODUCTION),
            patch.object(QA, "create_writer"),
            patch.object(QA, "supervise", side_effect=QA.QAError("CRASH")),
            patch.object(QA, "cleanup") as cleanup,
            patch.object(QA, "ensure_no_orphans"),
            self.assertRaises(QA.QAError),
        ):
            QA.execute()
        cleanup.assert_called_once()

    def test_live_production_identity_change_fails(self) -> None:
        """Concurrent production changes invalidate QA acceptance."""
        different = ("c" * 64, "1", "true", IMAGE)
        with (
            patch.object(QA, "prerequisites"),
            patch.object(
                QA,
                "production_identity",
                side_effect=[PRODUCTION, different],
            ),
            patch.object(QA, "create_writer"),
            patch.object(QA, "supervise"),
            patch.object(QA, "cleanup"),
            patch.object(QA, "ensure_no_orphans"),
            self.assertRaises(QA.QAError),
        ):
            QA.execute()

    def test_failed_subprocess_exits_closed(self) -> None:
        """Never accept an unsuccessful Docker/systemd command."""
        result = subprocess.CompletedProcess(
            args=["docker"], returncode=1, stdout="", stderr=""
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.QAError),
        ):
            QA.run(["docker", "inspect"])

    def test_unrelated_unit_does_not_pass_cleanup(self) -> None:
        """Never allow a unit name with an uncontrolled suffix."""
        with self.assertRaises(QA.QAError):
            QA.unit_name(NAME, "resume;rm")


if __name__ == "__main__":
    unittest.main()
