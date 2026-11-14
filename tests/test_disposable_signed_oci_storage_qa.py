"""Safety tests for networkless, disposable real-OCI Storage acceptance."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-signed-oci-storage-qa.py"
)
SPEC = importlib.util.spec_from_file_location("svt_oci_storage_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)
DIGEST = "sha256:" + "a" * 64
PIN = "ghcr.io/isentinel/shelly-voltage-telegram@" + DIGEST
NAME = "svt-qa-oci-abcdef012345-seed"
CRASH_NAME = "svt-qa-oci-abcdef012345-crash"
GOOD_RESULT = {
    "status": "PASS",
    "rows": 65002,
    "quick_check": "ok",
    "last_committed": 225.4,
    "last_appended": 223.7,
    "uncommitted_absent": True,
}


class DisposableSignedOciStorageTests(unittest.TestCase):
    """Do not contact Docker, live SQLite or network in unit testing."""

    def setUp(self) -> None:
        """Create only private local paths for argv construction."""
        temp = tempfile.TemporaryDirectory(
            prefix="svt-oci-regression-unit-",
            dir="/tmp",
        )
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()

    def invoke(self, *args: str) -> tuple[int, str]:
        """Run the inert CLI with captured output."""
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            code = QA.main()
        return code, output.getvalue()

    def test_default_dry_run_touches_no_docker(self) -> None:
        """No arguments can trigger Docker, SQLite, or service changes."""
        with patch.object(QA, "run_isolated", side_effect=AssertionError):
            self.assertEqual(self.invoke()[0], 0)
            self.assertEqual(self.invoke("--dry-run")[0], 0)

    def test_wrong_confirmation_never_runs(self) -> None:
        """A regular --run-disposable without exact consent is inert."""
        with patch.object(QA, "run_isolated", side_effect=AssertionError):
            code, _ = self.invoke(
                "--run-disposable", "--image", PIN, "--confirm", "yes"
            )
        self.assertEqual(code, 2)

    def test_missing_image_never_runs(self) -> None:
        """An image is always an explicit immutable operator choice."""
        with patch.object(QA, "run_isolated", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", QA.CONFIRM)[0], 2
            )

    def test_mutable_image_tags_rejected(self) -> None:
        """Never run latest, arbitrary registries, or partial SHA256 tags."""
        values = (
            "ghcr.io/isentinel/shelly-voltage-telegram:latest",
            "ghcr.io/isentinel/shelly-voltage-telegram@sha256:abcd",
            "docker.io/library/python@sha256:" + "a" * 64,
            "ghcr.io/isentinel/other@sha256:" + "a" * 64,
        )
        for value in values:
            with self.subTest(image=value):
                self.assertIsNone(QA.IMAGE.fullmatch(value))

    def test_exact_consent_calls_qa_without_production_fallback(self) -> None:
        """Passing the full immutable digest reaches only the QA runner."""
        with patch.object(QA, "run_isolated", return_value=GOOD_RESULT) as run:
            code, output = self.invoke(
                "--run-disposable",
                "--confirm",
                QA.CONFIRM,
                "--image",
                PIN,
                "--jsonl",
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), GOOD_RESULT)
        run.assert_called_once_with(PIN)

    def test_command_has_no_network_ports_secrets_or_build(self) -> None:
        """Docker argv must enforce strict isolation regardless of image."""
        args = QA.common(PIN, self.data, NAME)
        for pair in (
            ("--pull", "never"),
            ("--network", "none"),
            ("--restart", "no"),
            ("--user", "10001:10001"),
            ("--cap-drop", "ALL"),
        ):
            self.assertIn(pair[0], args)
            self.assertEqual(args[args.index(pair[0]) + 1], pair[1])
        for forbidden in ("--publish", "-p", "--privileged", "--env-file"):
            self.assertNotIn(forbidden, args)
        self.assertIn("--read-only", args)
        self.assertIn("no-new-privileges", args)
        self.assertIn("--entrypoint", args)
        self.assertEqual(args[-1], PIN)

    def test_scratch_mount_is_private_only(self) -> None:
        """A Docker bind must target exactly the disposable data path."""
        args = QA.common(PIN, self.data, NAME)
        mount = args[args.index("--mount") + 1]
        self.assertEqual(mount, f"type=bind,src={self.data},dst=/data")
        self.assertNotIn("/var/lib/shelly-voltage", mount)

    def test_rejects_production_or_user_selected_container_names(self) -> None:
        """No caller can choose a production Docker identity."""
        for value in (
            "shelly-voltage-bot",
            "svt-qa-oci-production-crash",
            NAME + ";rm",
            "svt-qa-oci-abcdef012345-another",
        ):
            with self.subTest(name=value), self.assertRaises(QA.Blocked):
                QA.common(PIN, self.data, value)

    def test_rejects_nonexistent_and_relative_scratch(self) -> None:
        """Do not create implicit mounts at arbitrary user paths."""
        for value in (self.root / "missing", Path("relative")):
            with self.assertRaises(QA.Blocked):
                QA.common(PIN, value, NAME)

    def test_cleanup_never_accepts_production_name(self) -> None:
        """Only generated QA names can reach docker rm."""
        with self.assertRaises(QA.Blocked):
            QA.remove_qa(QA.PRODUCTION)

    def test_cleanup_uses_exact_generated_qa_name(self) -> None:
        """Cleanup has no broad image or container deletion operation."""
        proc = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with patch.object(subprocess, "run", return_value=proc) as run:
            QA.remove_qa(NAME)
        self.assertEqual(run.call_args.args[0], ["docker", "rm", "-f", NAME])

    def test_cleanup_failure_cannot_report_success(self) -> None:
        """A persistent QA container must be reported as an error."""
        proc = subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="",
            stderr="permission denied",
        )
        with (
            patch.object(subprocess, "run", return_value=proc),
            self.assertRaises(QA.Blocked),
        ):
            QA.remove_qa(NAME)

    def test_cleanup_missing_transient_container_is_ok(self) -> None:
        """Previously removed --rm containers do not block cleanup."""
        proc = subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="",
            stderr="Error response from daemon: No such container: " + NAME,
        )
        with patch.object(subprocess, "run", return_value=proc):
            QA.remove_qa(NAME)

    def test_production_inspection_is_read_only_and_fixed(self) -> None:
        """Never inspect the production container by a supplied name."""
        with patch.object(QA, "command", return_value="identity") as cmd:
            self.assertEqual(QA.inspect_production(), "identity")
        arguments = cmd.call_args.args[0]
        self.assertEqual(arguments[0:2], ["docker", "inspect"])
        self.assertEqual(arguments[-1], "shelly-voltage-bot")

    def test_command_failure_is_not_silent_success(self) -> None:
        """A real Docker failure cannot be accepted as a passing case."""
        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="",
            stderr="failure",
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["docker", "inspect"])

    def test_command_timeout_is_fail_closed(self) -> None:
        """Timeouts must not create a false positive."""
        with (
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("docker", 1),
            ),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["docker", "inspect"])

    def test_stdout_limit_blocks_unbounded_tool_output(self) -> None:
        """Keep QA reports small and avoid leaking arbitrary logs."""
        result = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="X" * 9000,
            stderr="",
        )
        with (
            patch.object(subprocess, "run", return_value=result),
            self.assertRaises(QA.Blocked),
        ):
            QA.command(["docker", "inspect"])

    def test_strict_positive_json_result(self) -> None:
        """Pass only when the exact sample count and values match."""
        self.assertEqual(
            QA.verify_output(json.dumps(GOOD_RESULT)), GOOD_RESULT
        )

    def test_verify_rejects_loss_of_committed_history(self) -> None:
        """The final sample count is part of success, not just quick_check."""
        wrong = dict(GOOD_RESULT, rows=65001)
        with self.assertRaises(QA.Blocked):
            QA.verify_output(json.dumps(wrong))

    def test_verify_rejects_uncommitted_row_visible(self) -> None:
        """Never classify a crash as safe if partial writes become visible."""
        wrong = dict(GOOD_RESULT, uncommitted_absent=False)
        with self.assertRaises(QA.Blocked):
            QA.verify_output(json.dumps(wrong))

    def test_verify_rejects_untrusted_output(self) -> None:
        """Non-JSON or malformed QA output must fail closed."""
        with self.assertRaises(QA.Blocked):
            QA.verify_output("PASS but not JSON")

    def test_sigkill_only_generated_qa_writer(self) -> None:
        """Only the random QA container may receive SIGKILL."""
        marker = self.data / "qa-ready"
        marker.write_text("done")
        values = iter(
            [
                "created",
                "started",
                "none|no",
                CRASH_NAME,
                "137",
                "0",
            ]
        )
        with patch.object(
            QA, "command", side_effect=lambda _: next(values)
        ) as call:
            QA.start_and_crash(
                QA.common(PIN, self.data, CRASH_NAME),
                CRASH_NAME,
                marker,
            )
        kill = [
            args.args[0]
            for args in call.call_args_list
            if args.args[0][0:2] == ["docker", "kill"]
        ]
        self.assertEqual(
            kill, [["docker", "kill", "--signal", "SIGKILL", CRASH_NAME]]
        )

    def test_wrong_crash_isolation_blocks_before_sigkill(self) -> None:
        """A bridged QA Docker network must block the destructive test."""
        marker = self.data / "qa-ready"
        marker.write_text("done")
        values = iter(["created", "started", "bridge|no"])
        with (
            patch.object(
                QA, "command", side_effect=lambda _: next(values)
            ) as run,
            self.assertRaises(QA.Blocked),
        ):
            QA.start_and_crash(
                QA.common(PIN, self.data, CRASH_NAME),
                CRASH_NAME,
                marker,
            )
        actions = [call.args[0][:2] for call in run.call_args_list]
        self.assertNotIn(["docker", "kill"], actions)

    def test_seed_and_crash_are_storage_only(self) -> None:
        """Never launch the Telegram Monitor command in a QA container."""
        self.assertIn("app.Storage(", QA.SEED)
        self.assertIn("storage.add(", QA.CRASH)
        self.assertIn("time.sleep(90)", QA.CRASH)
        self.assertNotIn("Monitor(", QA.SEED + QA.CRASH + QA.VERIFY)
        self.assertNotIn("TELEGRAM_", QA.SEED + QA.CRASH + QA.VERIFY)

    def test_help_exists(self) -> None:
        """Support -h/--help without Docker access."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-h"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--run-disposable", result.stdout)


if __name__ == "__main__":
    unittest.main()
