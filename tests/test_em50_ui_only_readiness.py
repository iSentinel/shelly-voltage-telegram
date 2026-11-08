"""Verify UI-only diagnostics never authorize a real EM-50 release."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "em50_ui_readiness", ROOT / "scripts/em50-ui-only-readiness.py"
)
assert SPEC is not None and SPEC.loader is not None
UI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UI)


def compose_fixture(flag: str | None = "0", reader: bool = False) -> dict:
    """Build a fake Compose config containing an unprintable secret."""

    environment = {
        "TELEGRAM_TOKEN": "PRIVATE_SYNTHETIC_TOKEN",
        "YASNO_CACHE_FILE": "",
        "YASNO_TRUSTED_CACHE_FILE": "",
        "YASNO_GROUP": "",
    }
    if flag is not None:
        environment["TELEGRAM_YASNO_STATUS_ENABLED"] = flag
    mounts = [{"target": "/run/yasno/yasno.json"}] if reader else []
    return {
        "services": {
            "shelly-voltage": {
                "environment": environment,
                "volumes": mounts,
            }
        }
    }


def base_release_report(
    parity: str = "PASS", acceptance: str = "PASS"
) -> dict:
    """Model the independent full release guard with YASNO unprovisioned."""

    checks = {
        key: {"status": "PASS"}
        for key in (
            "source_candidate",
            "live_health",
            "sqlite_readonly",
            "latest_backup_sha256",
            "predeploy_backup_parity",
        )
    }
    checks["predeploy_backup_parity"]["status"] = parity
    checks["recipient_cache"] = {"status": "FAIL"}
    return {
        "checks": checks,
        "manual_evidence_status": acceptance,
        "manual_pending": ["explicit_release_and_rollback_authorization"],
        "release_authorized": False,
    }


class UiOnlyReadinessTests(unittest.TestCase):
    """A design-only candidate must fail closed without production writes."""

    def setUp(self):
        """Create synthetic source and runtime files, never actual secrets."""

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "source"
        self.runtime = self.root / "runtime"
        (self.source / "deploy").mkdir(parents=True)
        self.runtime.mkdir()
        (self.source / "compose.yaml").write_text("services: {}\n")
        (self.source / "deploy/compose.ui-only.example.yml").write_text(
            "services: {}\n"
        )
        self.args = argparse.Namespace(
            candidate_root=self.source,
            runtime_root=self.runtime,
            db=self.root / "synthetic.sqlite3",
            backup_dir=self.root,
            incident_id=19,
            acceptance_evidence=None,
        )

    def fake_result(self, config: dict, returncode: int = 0):
        """Simulate Compose resolving environment locally."""

        return subprocess.CompletedProcess(
            args=["docker", "compose", "config"],
            returncode=returncode,
            stdout=json.dumps(config),
            stderr="PRIVATE_SYNTHETIC_TOKEN",
        )

    def test_ui_only_overlay_explicitly_disables_yasno(self):
        """Compose input must force the application feature off."""

        overlay = ROOT / "deploy/compose.ui-only.example.yml"
        text = overlay.read_text(encoding="utf-8")
        self.assertIn('TELEGRAM_YASNO_STATUS_ENABLED: "0"', text)
        for key in (
            "YASNO_CACHE_FILE",
            "YASNO_TRUSTED_CACHE_FILE",
            "YASNO_GROUP",
        ):
            self.assertIn(f'{key}: ""', text)

    def test_only_explicit_zero_is_accepted(self):
        """Missing, enabled and incorrectly typed values fail closed."""

        for flag in (None, "1", "false", "", "00"):
            with (
                self.subTest(flag=flag),
                patch.object(
                    UI.subprocess,
                    "run",
                    return_value=self.fake_result(compose_fixture(flag)),
                ),
                self.assertRaisesRegex(ValueError, "explicitly disabled"),
            ):
                UI.effective_configuration(self.source, self.runtime)

    def test_off_config_never_exposes_secrets_or_starts_containers(self):
        """Config operation must be read-only and never print resolved env."""

        output = io.StringIO()
        config = self.fake_result(compose_fixture())
        with (
            patch.object(UI.subprocess, "run", return_value=config) as run,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                UI.effective_configuration(self.source, self.runtime), "PASS"
            )
        command = run.call_args.args[0]
        self.assertEqual(command[-3:], ["config", "--format", "json"])
        self.assertNotIn("up", command)
        self.assertNotIn("PRIVATE_SYNTHETIC_TOKEN", output.getvalue())

    def test_private_preview_cache_configuration_must_be_blank(self):
        """Public YASNO off must not leave private preview cache enabled."""

        keys = ("YASNO_CACHE_FILE", "YASNO_TRUSTED_CACHE_FILE", "YASNO_GROUP")
        for name in keys:
            config = compose_fixture()
            config["services"]["shelly-voltage"]["environment"][name] = (
                "/run/yasno/pending.json" if name.endswith("FILE") else "10.1"
            )
            with (
                self.subTest(name=name),
                patch.object(
                    UI.subprocess,
                    "run",
                    return_value=self.fake_result(config),
                ),
                self.assertRaisesRegex(ValueError, "preview"),
            ):
                UI.effective_configuration(self.source, self.runtime)

    def test_reader_mount_is_rejected(self):
        """Do not permit a YASNO mount in the UI-only release candidate."""

        config = self.fake_result(compose_fixture(reader=True))
        with (
            patch.object(UI.subprocess, "run", return_value=config),
            self.assertRaisesRegex(ValueError, "mount"),
        ):
            UI.effective_configuration(self.source, self.runtime)

    def test_compose_error_fails_closed_without_leaking_stderr(self):
        """CLI failures may contain credentials and must not be shown."""

        config = self.fake_result(compose_fixture(), returncode=1)
        output = io.StringIO()
        with (
            patch.object(UI.subprocess, "run", return_value=config),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(ValueError, "could not be resolved"),
        ):
            UI.effective_configuration(self.source, self.runtime)
        self.assertNotIn("PRIVATE_SYNTHETIC_TOKEN", output.getvalue())

    def test_missing_overlay_is_rejected_before_compose(self):
        """An old or incomplete source tree cannot declare UI-only mode."""

        (self.source / "deploy/compose.ui-only.example.yml").unlink()
        with (
            patch.object(UI.subprocess, "run") as run,
            self.assertRaisesRegex(ValueError, "missing"),
        ):
            UI.effective_configuration(self.source, self.runtime)
        run.assert_not_called()

    def diagnostic(
        self, *, parity="PASS", recovery="pass", config="PASS", normal="PASS"
    ):
        """Run the report with synthetic release/recovery providers."""

        release = SimpleNamespace(
            inspect=lambda _: base_release_report(parity=parity)
        )
        telemetry = SimpleNamespace(
            inspect_database=lambda *_: {"status": recovery}
        )
        with (
            patch.object(UI, "load_script", side_effect=[release, telemetry]),
            patch.object(UI, "effective_configuration", return_value=config),
            patch.object(UI, "normal_voltage_status", return_value=normal),
        ):
            return UI.inspect(self.args)

    def test_yasno_cache_failed_but_design_qa_cannot_authorize(self):
        """Only design readiness may be eligible, never a real release."""

        result = self.diagnostic()
        self.assertEqual(result["status"], "MANUAL_REVIEW_ONLY")
        self.assertTrue(result["automated_non_yasno_pass"])
        self.assertEqual(
            result["full_release_checks"]["recipient_cache"], "FAIL"
        )
        self.assertFalse(result["release_authorized"])
        self.assertIn(
            "explicit_signed_UI_only_release_and_rollback_authorization",
            result["manual_pending"],
        )

    def test_backup_parity_failure_never_becomes_design_eligible(self):
        """A missing measurement is fatal even with YASNO switched off."""

        result = self.diagnostic(parity="FAIL")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["release_authorized"])

    def test_each_inherited_release_gate_stays_mandatory(self):
        """No disabled YASNO feature can conceal a failed full safety gate."""

        for missing in (
            "source_candidate",
            "live_health",
            "sqlite_readonly",
            "latest_backup_sha256",
            "predeploy_backup_parity",
        ):
            report = base_release_report()
            report["checks"][missing]["status"] = "FAIL"
            with self.subTest(missing=missing):
                result = UI.diagnostic_result(report, "PASS", "pass", "PASS")
                self.assertEqual(result["status"], "BLOCKED")
                self.assertFalse(result["release_authorized"])

    def test_missing_verification_of_mount_shape_is_a_blocker(self):
        """An unknown volume format cannot hide an external cache mount."""

        config = compose_fixture()
        config["services"]["shelly-voltage"]["volumes"] = ["opaque:mount"]
        with (
            patch.object(
                UI.subprocess,
                "run",
                return_value=self.fake_result(config),
            ),
            self.assertRaisesRegex(ValueError, "resolved mapping"),
        ):
            UI.effective_configuration(self.source, self.runtime)

    def test_unverified_15min_recovery_blocks_design_qa(self):
        """Brief newly restored Shelly connectivity is insufficient."""

        result = self.diagnostic(recovery="blocked")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["automated_non_yasno_pass"])

    def test_compose_feature_error_blocks_design_qa(self):
        """Any failure to prove YASNO opt-out is an automatic blocker."""

        result = self.diagnostic(config="FAIL")
        self.assertEqual(result["status"], "BLOCKED")

    def test_abnormal_voltage_blocks_even_with_valid_connection(self):
        """Connection telemetry PASS never makes undervoltage normal."""

        report = base_release_report()
        result = UI.diagnostic_result(report, "PASS", "pass", "FAIL")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["release_authorized"])

    def test_normal_band_failures_are_detected(self):
        """A whole window at 201 V cannot be considered normal."""

        for minimum, maximum, outage, limits, expected in (
            (201.0, 206.0, 19, (207.0, 253.0), "FAIL"),
            (207.0, 245.0, 19, (207.0, 253.0), "FAIL"),
            (212.0, 253.0, 19, (207.0, 253.0), "FAIL"),
            (212.0, 253.1, 19, (207.0, 253.0), "FAIL"),
            (212.0, 240.0, 18, (207.0, 253.0), "FAIL"),
            (212.0, 240.0, 19, None, "FAIL"),
            (212.0, 240.0, 19, (207.0, 253.0), "PASS"),
        ):
            stability = SimpleNamespace(
                inspect=lambda *_: {
                    "status": "PASS",
                    "outage_id": outage,
                    "voltage_min": minimum,
                    "voltage_max": maximum,
                    "sample_count": 90,
                }
            )
            acceptance = SimpleNamespace(
                configured_normal_bounds=lambda _: limits
            )
            with (
                self.subTest(minimum=minimum, maximum=maximum, outage=outage),
                patch.object(
                    UI, "load_script", side_effect=[stability, acceptance]
                ),
                patch.object(UI, "exact_normal_window", return_value=True),
            ):
                self.assertEqual(UI.normal_voltage_status(self.args), expected)

    def test_fractional_threshold_uses_unrounded_samples(self):
        """A 207.06 V reading fails a 207.07 V low threshold."""

        db = self.root / "fractional.sqlite3"
        import sqlite3

        with contextlib.closing(sqlite3.connect(db)) as conn:
            conn.execute(
                "CREATE TABLE outages "
                "(id INTEGER, phase TEXT, started_at REAL, ended_at REAL)"
            )
            conn.execute("INSERT INTO outages VALUES (19, 'L1', 1, 2)")
            conn.execute(
                "CREATE TABLE measurements (ts REAL, phase TEXT, voltage REAL)"
            )
            conn.executemany(
                "INSERT INTO measurements VALUES (?,?,?)",
                [
                    (float(i * 10), "L1", 207.06 if i == 20 else 220.0)
                    for i in range(90)
                ],
            )
            conn.commit()
        self.assertFalse(
            UI.exact_normal_window(db, 19, 0.0, 900.0, 207.07, 253.0, 90)
        )

    def test_missing_real_normal_window_blocks_diagnostic(self):
        """A connection PASS with brownout samples cannot grant readiness."""

        result = self.diagnostic(normal="FAIL")
        self.assertEqual(result["natural_recovery_15min"], "pass")
        self.assertEqual(result["normal_voltage_15min"], "FAIL")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["release_authorized"])

    def test_cli_never_exits_zero_or_claims_release(self):
        """No green technical result may be used as a deploy command."""

        for status, expected in (("BLOCKED", 1), ("MANUAL_REVIEW_ONLY", 2)):
            result = {"status": status, "release_authorized": False}
            args = [
                "readiness.py",
                "--db",
                str(self.args.db),
                "--backup-dir",
                str(self.args.backup_dir),
                "--incident-id",
                "19",
                "--json",
            ]
            with (
                self.subTest(status=status),
                patch.object(sys, "argv", args),
                patch.object(UI, "inspect", return_value=result),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(UI.main(), expected)
                self.assertFalse(
                    json.loads(output.getvalue())["release_authorized"]
                )


if __name__ == "__main__":
    unittest.main()
