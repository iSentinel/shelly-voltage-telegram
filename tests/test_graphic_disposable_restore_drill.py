"""Regression gates for the disposable systemd recovery drill.

These tests only simulate systemd commands, never operating on real units.
"""

from __future__ import annotations

import importlib.util
import io
import json
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/graphic-disposable-restore-drill.py"
)
SPEC = importlib.util.spec_from_file_location("disposable_restore", SCRIPT)
DRILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRILL)


class DisposableRestoreDrillTests(unittest.TestCase):
    """Exercise the safety fences without contacting systemd or Docker."""

    def test_default_cli_cannot_create_units(self):
        """A missing --execute must only print a dry-run result."""
        output = io.StringIO()
        with (
            patch("sys.argv", [str(SCRIPT)]),
            patch.object(DRILL, "exercise", side_effect=AssertionError),
            redirect_stdout(output),
        ):
            result = DRILL.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "DRY_RUN")

    def test_help_does_not_invoke_exercise(self):
        """Even an explicit help request must not invoke systemd."""
        with (
            patch("sys.argv", [str(SCRIPT), "--help"]),
            patch.object(DRILL, "exercise", side_effect=AssertionError),
            redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            DRILL.main()
        self.assertEqual(caught.exception.code, 0)

    def test_unprivileged_execution_is_rejected_before_probing(self):
        """A non-root process must not inspect or change systemd units."""
        with (
            patch.object(DRILL.os, "geteuid", return_value=1000),
            patch.object(DRILL, "production_ok") as production,
            patch.object(DRILL.tempfile, "mkdtemp") as create,
            self.assertRaisesRegex(RuntimeError, "PRODUCTION_BASELINE_UNSAFE"),
        ):
            DRILL.exercise()
        production.assert_not_called()
        create.assert_not_called()

    def test_unhealthy_baseline_does_not_create_files(self):
        """An unhealthy production baseline must fail before setup."""
        with (
            patch.object(DRILL.os, "geteuid", return_value=0),
            patch.object(DRILL, "production_ok", return_value=False),
            patch.object(DRILL.tempfile, "mkdtemp") as create,
            self.assertRaisesRegex(RuntimeError, "PRODUCTION_BASELINE_UNSAFE"),
        ):
            DRILL.exercise()
        create.assert_not_called()

    def test_failed_setup_always_calls_cleanup(self):
        """The cleanup block must run after a transient setup failure."""
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "disposable"
            scratch.mkdir()
            with (
                patch.object(DRILL.os, "geteuid", return_value=0),
                patch.object(DRILL, "production_ok", return_value=True),
                patch.object(
                    DRILL.tempfile, "mkdtemp", return_value=str(scratch)
                ),
                patch.object(
                    DRILL, "create_disposable", side_effect=RuntimeError
                ),
                patch.object(DRILL, "cleanup") as clean,
                self.assertRaises(RuntimeError),
            ):
                DRILL.exercise()
            clean.assert_called_once()
            self.assertEqual(clean.call_args.args[2], scratch)

    def test_cleanup_targets_only_unique_disposable_units(self):
        """Never stop the production writer during temporary cleanup."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp_unit = "shelly-graphic-drill-123-writer.service"
            work = root / "work"
            work.mkdir()
            with (
                patch.object(DRILL, "UNIT_ROOT", root),
                patch.object(DRILL, "command", return_value="") as command,
            ):
                (root / temp_unit).write_text("test", encoding="utf-8")
                DRILL.cleanup(
                    temp_unit, "shelly-graphic-drill-123-timer", work
                )
            self.assertFalse((root / temp_unit).exists())
            self.assertFalse(work.exists())
            touched = [c.args[0] for c in command.call_args_list]
            self.assertEqual(len(touched), 7)
            self.assertFalse(any(DRILL.WRITER in args for args in touched))
            self.assertEqual(touched[-1], ["systemctl", "daemon-reload"])

    def test_retry_rejects_missing_failed_attempt(self):
        """A successful-looking flag without a failure probe is invalid."""
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(DRILL, "wait_until", return_value=False),
            self.assertRaisesRegex(
                RuntimeError, "NO_FAILED_HEALTH_RESTORE_ATTEMPT"
            ),
        ):
            DRILL.verify_retry(
                Path(directory), "mock.service", Path(directory) / "ok"
            )

    def test_retry_requires_a_successful_second_attempt(self):
        """The retry must create a health marker before a pass result."""
        with tempfile.TemporaryDirectory() as directory:
            healthy = Path(directory) / "health-ok"
            with (
                patch.object(DRILL, "wait_until", side_effect=[True, True]),
                patch.object(DRILL, "unit_value", return_value="active"),
            ):
                result = DRILL.verify_retry(
                    Path(directory), "mock.service", healthy
                )
            self.assertTrue(healthy.exists())
            self.assertTrue(result["health_verified_after_retry"])
            self.assertTrue(result["writer_healthy"])

    def test_execute_failure_returns_nonzero(self):
        """An explicit failed drill may not report a successful execution."""
        output = io.StringIO()
        with (
            patch("sys.argv", [str(SCRIPT), "--execute"]),
            patch.object(
                DRILL, "exercise", side_effect=RuntimeError("MOCK_FAIL")
            ),
            redirect_stdout(output),
        ):
            status = DRILL.main()
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "FAIL")

    def test_execute_success_requires_verified_pass(self):
        """An explicit mock success must differ from a default dry-run."""
        output = io.StringIO()
        with (
            patch("sys.argv", [str(SCRIPT), "--execute"]),
            patch.object(DRILL, "exercise", return_value={"pass": True}),
            redirect_stdout(output),
        ):
            status = DRILL.main()
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.getvalue())["pass"])

    def test_generated_restore_script_is_private(self):
        """A generated test script must be mode 0700 and fail closed."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "restore.sh"
            DRILL.make_restore_script(
                path, "mock-writer.service", Path(directory) / "ok"
            )
            content = path.read_text(encoding="utf-8")
            self.assertIn(
                "/usr/bin/systemctl restart mock-writer.service", content
            )
            self.assertIn("exit 42", content)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
