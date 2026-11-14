"""Verify fail-closed fixed-file writer owner graph audit."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/writer-start-owner-graph-audit.py"
)
SPEC = importlib.util.spec_from_file_location("owner_graph_audit", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def known_code() -> dict[str, str]:
    """Return invented public code patterns, never source from production."""
    return {
        "compose_yaml": "services:\n  bot:\n    restart: unless-stopped\n",
        "updater": "exec 9>/run/lock/shelly-voltage-updater.lock\n",
        "installer": 'bash "$TARGET/scripts/start-compose.sh" --build\n',
        "rollback": "compose_cmd up -d --build\n",
        "compose_start": "compose_cmd up -d\n",
        "compose_unit": (
            "ExecStartPre=/bin/bash scripts/apply-update.sh --recover\n"
            "ExecStart=/bin/bash scripts/start-compose.sh\n"
            "ExecReload=/bin/bash scripts/start-compose.sh\n"
            "ExecStop=/usr/bin/docker compose stop\n"
        ),
        "manual_updater_unit": (
            "ExecStart=/bin/bash scripts/apply-update.sh --version %i\n"
        ),
        "update_check_unit": (
            "ExecStart=/usr/bin/python3 scripts/github_update.py check\n"
        ),
        "backup_unit": "ExecStart=/opt/shelly-voltage/scripts/backup.sh\n",
        "yasno_unit": "ExecStart=/usr/bin/python3 scripts/yasno-refresh.py\n",
        "emergency_unit": (
            "ExecStart=/usr/bin/python3 emergency --restore --execute\n"
        ),
        "emergency_script": '["docker","update","--restart=unless-stopped"]',
    }


class OwnerGraphAuditTests(unittest.TestCase):
    """Default command never accesses installed services or credentials."""

    def setUp(self) -> None:
        """Use only synthetic fixed-name public source files."""
        directory = tempfile.TemporaryDirectory(
            prefix="svt-owner-graph-unit-", dir="/tmp"
        )
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "src"
        self.units = self.root / "units"
        self.source.mkdir()
        self.units.mkdir()
        self.emergency = self.root / "emergency"
        data = known_code()
        for name, relative in QA.SOURCE_FILES.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(data[name])
        for name, relative in QA.UNIT_FILES.items():
            (self.units / relative).write_text(data[name])
        self.emergency.write_text(data["emergency_script"])
        for key, value in (
            ("SOURCE_ROOT", self.source),
            ("UNIT_ROOT", self.units),
            ("EMERGENCY", self.emergency),
        ):
            fake = patch.object(QA, key, value)
            fake.start()
            self.addCleanup(fake.stop)

    def cli(self, *args: str) -> tuple[int, str]:
        """Execute local CLI with all output captured."""
        output = io.StringIO()
        with (
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            contextlib.redirect_stdout(output),
        ):
            return QA.main(), output.getvalue()

    def test_default_inert(self) -> None:
        """No reads occur without explicit live-read-only flag."""
        with patch.object(QA, "collect", side_effect=AssertionError):
            status, text = self.cli()
        self.assertEqual(status, 0)
        self.assertIn("DRY-RUN", text)

    def test_explicit_dry_run_overrides_live_flag(self) -> None:
        """An explicit dry-run prevents even file metadata inspections."""
        with patch.object(QA, "collect", side_effect=AssertionError):
            self.assertEqual(self.cli("--dry-run", "--live-read-only")[0], 0)

    def test_help(self) -> None:
        """Help is available without any source or service access."""
        for arg in ("-h", "--help"):
            with self.subTest(arg=arg):
                with (
                    patch.object(sys, "argv", [str(SCRIPT), arg]),
                    self.assertRaises(SystemExit) as raised,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    QA.main()
                self.assertEqual(raised.exception.code, 0)

    def test_expected_live_blocker_matrix(self) -> None:
        """All twelve known startup risks have stable reason codes."""
        result = QA.evaluate(known_code(), [])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(result["reasons"]), 12)
        self.assertIn("COMPOSE_RELOAD_REQUIRES_B_FENCE", result["reasons"])
        self.assertIn(
            "EMERGENCY_RESTORE_CAN_REENABLE_DOCKER_RESTART",
            result["reasons"],
        )

    def test_benign_classification(self) -> None:
        """Background read-only timers are not restart owners."""
        result = QA.evaluate(known_code(), [])
        self.assertEqual(
            set(result["non_writer_classification"]),
            {
                "RELEASE_CHECK_METADATA_ONLY",
                "BACKUP_ONLINE_SQLITE_NOT_WRITER_START",
                "YASNO_REFRESH_NOT_WRITER_START",
            },
        )

    def test_no_production_authorization_ever(self) -> None:
        """Even an empty synthetic policy cannot grant maintenance."""
        for result in (
            QA.evaluate(known_code(), []),
            QA.evaluate({}, []),
        ):
            self.assertFalse(result["all_writer_owners_fenced"])
            self.assertFalse(result["owner_maintenance_authorized"])
            self.assertFalse(result["production_modified"])
            self.assertFalse(result["telegram_photo_authorized"])

    def test_collect_all_known_fixed_files(self) -> None:
        """The tool reads only twelve known public source paths."""
        code, errors = QA.collect()
        self.assertEqual(errors, [])
        self.assertEqual(len(code), 12)
        self.assertEqual(set(code), set(known_code()))

    def test_collect_absent_file_reports_unverified(self) -> None:
        """Missing service file must never disappear from blocker list."""
        (self.units / QA.UNIT_FILES["compose_unit"]).unlink()
        code, errors = QA.collect()
        result = QA.evaluate(code, errors)
        self.assertIn("SOURCE_COMPOSE_UNIT_UNVERIFIED", result["reasons"])
        self.assertEqual(result["status"], "BLOCKED")

    def test_symlink_file_is_never_followed(self) -> None:
        """Never follow potentially malicious symlinks or disclose data."""
        path = self.source / QA.SOURCE_FILES["rollback"]
        path.unlink()
        path.symlink_to(self.emergency)
        _, errors = QA.collect()
        self.assertIn("SOURCE_ROLLBACK_UNVERIFIED", errors)

    def test_large_source_is_refused(self) -> None:
        """Oversized code/units cause bounded fail-closed reporting."""
        path = self.source / QA.SOURCE_FILES["updater"]
        path.write_text("z" * (QA.MAX_SOURCE_BYTES + 1))
        _, errors = QA.collect()
        self.assertIn("SOURCE_UPDATER_UNVERIFIED", errors)

    def test_unexpected_compose_reload_is_unverified(self) -> None:
        """A changed reload path cannot silently bypass audit."""
        code = known_code()
        code["compose_unit"] = code["compose_unit"].replace(
            "ExecReload=",
            "ReloadUnknown=",
        )
        result = QA.evaluate(code, [])
        self.assertIn("EXECRELOAD_ENTRYPOINT_UNVERIFIED", result["reasons"])

    def test_changed_manual_updater_entrypoint_blocks(self) -> None:
        """Review is required when the template changes its ExecStart."""
        code = known_code()
        code["manual_updater_unit"] = "ExecStart=/bin/true\n"
        result = QA.evaluate(code, [])
        self.assertIn(
            "MANUAL_UPDATER_ENTRYPOINT_UNVERIFIED", result["reasons"]
        )

    def test_changed_installer_blocks(self) -> None:
        """Unknown new installer start path is not considered safe."""
        code = known_code()
        code["installer"] = "python3 unknown_installer.py\n"
        result = QA.evaluate(code, [])
        self.assertIn("INSTALLER_SOURCE_UNVERIFIED", result["reasons"])

    def test_changed_emergency_boot_blocks(self) -> None:
        """An unrecognized rescue command requires separate review."""
        code = known_code()
        code["emergency_unit"] = "ExecStart=/bin/true\n"
        result = QA.evaluate(code, [])
        self.assertIn(
            "EMERGENCY_BOOT_RESTORE_ENTRYPOINT_UNVERIFIED",
            result["reasons"],
        )

    def test_missing_a_mutex_blocks(self) -> None:
        """Removing existing A is not an allowed shortcut to safety."""
        code = known_code()
        code["updater"] = "# no A mutex\n"
        result = QA.evaluate(code, [])
        self.assertIn("UPDATER_A_MUTEX_MISSING_OR_CHANGED", result["reasons"])

    def test_changed_update_check_needs_review(self) -> None:
        """A future auto-installing release checker is not benign."""
        code = known_code()
        code["update_check_unit"] = "ExecStart=/bin/bash updater-install.sh\n"
        result = QA.evaluate(code, [])
        self.assertIn(
            "UPDATE_CHECK_UNIT_BEHAVIOR_UNVERIFIED", result["reasons"]
        )
        self.assertNotIn(
            "RELEASE_CHECK_METADATA_ONLY",
            result["non_writer_classification"],
        )

    def test_changed_backup_needs_review(self) -> None:
        """Unknown backup service can no longer be called non-writer."""
        code = known_code()
        code["backup_unit"] = "ExecStart=/bin/bash restore-database.sh\n"
        result = QA.evaluate(code, [])
        self.assertIn("BACKUP_UNIT_BEHAVIOR_UNVERIFIED", result["reasons"])

    def test_changed_yasno_refresh_needs_review(self) -> None:
        """A modified auxiliary service is not auto-approved."""
        code = known_code()
        code["yasno_unit"] = "ExecStart=/bin/sh do-anything.sh\n"
        result = QA.evaluate(code, [])
        self.assertIn("YASNO_UNIT_BEHAVIOR_UNVERIFIED", result["reasons"])

    def test_live_readonly_json_exit_two(self) -> None:
        """No CLI path can claim deployment readiness."""
        code = known_code()
        with patch.object(QA, "collect", return_value=(code, [])):
            status, text = self.cli("--live-read-only", "--jsonl")
        self.assertEqual(status, 2)
        result = json.loads(text)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["production_modified"])

    def test_source_exception_fails_closed(self) -> None:
        """Missing trusted data must be reported as blocker."""
        result = QA.evaluate({}, ["SOURCE_COMPOSE_UNIT_UNVERIFIED"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("SOURCE_COMPOSE_UNIT_UNVERIFIED", result["reasons"])


if __name__ == "__main__":
    unittest.main()
