"""Reject unsafe YASNO Docker mounts and premature Telegram activation."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "yasno_docker_gate", ROOT / "scripts/yasno-docker-reader-preflight.py"
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def inspection(enabled: str = "0") -> dict:
    """Return a synthetic hardened container with no real credentials."""

    return {
        "Config": {
            "User": "10001:10001",
            "Env": [
                "TELEGRAM_YASNO_STATUS_ENABLED=" + enabled,
                "YASNO_TRUSTED_CACHE_FILE=/run/yasno/yasno.json",
                "YASNO_GROUP=10.1",
                "SENSITIVE_FAKE_TOKEN=do-not-print",
            ],
        },
        "HostConfig": {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": ["no-new-privileges:true"],
        },
        "Mounts": [
            {
                "Destination": "/run/yasno",
                "Source": "/var/lib/shelly-yasno-reader",
                "Type": "bind",
                "RW": False,
            }
        ],
    }


class YasnoDockerReaderGateTests(unittest.TestCase):
    """Keep producer data separate from the read-only Telegram reader."""

    def test_staging_requires_mount_with_disabled_telegram_feature(self):
        """Initial Docker rollout must not publish unreviewed schedules."""

        result = QA.verify_reader(inspection("0"), "staging")
        self.assertIn("phase=staging", result)
        self.assertIn("feature_enabled=0", result)

    def test_staging_rejects_active_and_active_rejects_disabled(self):
        """Feature flag must agree exactly with the reviewed phase."""

        for phase, flag in (("staging", "1"), ("active", "0")):
            with (
                self.subTest(phase=phase, flag=flag),
                self.assertRaisesRegex(ValueError, "phase mismatch"),
            ):
                QA.verify_reader(inspection(flag), phase)

    def test_active_requires_valid_protected_recipient_cache(self):
        """Active means not merely a path or a guessed schedule."""

        with (
            patch.object(QA, "validate_target") as perms,
            patch.object(
                QA, "read_verified_cache", return_value=({"10.1": {}}, "time")
            ) as read,
            patch.object(QA, "parse_schedule") as schedule,
        ):
            result = QA.verify_reader(inspection("1"), "active")
        self.assertIn("feature_enabled=1", result)
        perms.assert_called_once()
        read.assert_called_once()
        schedule.assert_called_once()

    def test_active_invalid_cache_never_passes(self):
        """No stale or corrupted source can authorize a visible forecast."""

        with (
            patch.object(QA, "validate_target"),
            patch.object(
                QA,
                "read_verified_cache",
                side_effect=ValueError("expired"),
            ),
            self.assertRaisesRegex(ValueError, "expired"),
        ):
            QA.verify_reader(inspection("1"), "active")

    def test_missing_duplicate_or_writable_mount_rejected(self):
        """No missing, ambiguous or writable YASNO bind is acceptable."""

        for variant in ("missing", "duplicate", "writable", "wrong_source"):
            document = inspection()
            mount = document["Mounts"][0]
            if variant == "missing":
                document["Mounts"] = []
            if variant == "duplicate":
                document["Mounts"].append(dict(mount))
            if variant == "writable":
                mount["RW"] = True
            if variant == "wrong_source":
                mount["Source"] = "/tmp/untrusted"
            with (
                self.subTest(variant=variant),
                self.assertRaises(ValueError),
            ):
                QA.verify_reader(document, "staging")

    def test_reader_mount_rejects_overlapping_destinations(self):
        """No other Docker mount may shadow or replace the trusted cache."""

        for destination in (
            "/run/yasno/yasno.json",
            "/run/yasno/extra",
            "/run",
            "/run/yasno/subdir",
            "/run/yasno/",
            "/",
        ):
            document = inspection()
            document["Mounts"].append(
                {
                    "Destination": destination,
                    "Source": "/tmp/untrusted-qa",
                    "Type": "bind",
                    "RW": True,
                }
            )
            with (
                self.subTest(destination=destination),
                self.assertRaisesRegex(ValueError, "overlap"),
            ):
                QA.verify_reader(document, "staging")

    def test_traversal_or_malformed_destinations_fail_closed(self):
        """Do not trust noncanonical or invalid Docker mount metadata."""

        for destination in (
            "/run/yasno/../yasno/yasno.json",
            "run/yasno/yasno.json",
            None,
        ):
            document = inspection()
            document["Mounts"].append(
                {"Destination": destination, "Source": "/tmp", "RW": True}
            )
            with (
                self.subTest(destination=destination),
                self.assertRaises(ValueError),
            ):
                QA.verify_reader(document, "staging")

    def test_unrelated_readonly_mount_remains_supported(self):
        """A separate private mount cannot override the trusted YASNO bind."""

        document = inspection()
        document["Mounts"].append(
            {
                "Destination": "/data/certs",
                "Source": "/var/lib/qa-certs",
                "Type": "bind",
                "RW": False,
            }
        )
        self.assertIn("phase=staging", QA.verify_reader(document, "staging"))

    def test_docker_user_and_filesystem_must_remain_hardened(self):
        """Reject privilege elevation and writable container filesystem."""

        variants = [
            ("root_user", "Config", "User", "root"),
            ("writable_root", "HostConfig", "ReadonlyRootfs", False),
            ("privileged", "HostConfig", "Privileged", True),
            ("missing_cap_drop", "HostConfig", "CapDrop", []),
            ("added_capability", "HostConfig", "CapAdd", ["SYS_ADMIN"]),
            ("missing_nnp", "HostConfig", "SecurityOpt", []),
            (
                "unconfined_seccomp",
                "HostConfig",
                "SecurityOpt",
                ["no-new-privileges:true", "seccomp=unconfined"],
            ),
            (
                "unconfined_apparmor",
                "HostConfig",
                "SecurityOpt",
                ["no-new-privileges:true", "apparmor=unconfined"],
            ),
        ]
        for label, parent, key, value in variants:
            document = inspection()
            document[parent][key] = value
            with (
                self.subTest(label=label),
                self.assertRaisesRegex(ValueError, "isolation"),
            ):
                QA.verify_reader(document, "staging")

    def test_bad_environment_and_conflicting_cache_path_rejected(self):
        """No implicit feature flags or duplicate reader selection."""

        for kind in ("duplicate", "wrong_path", "missing_group"):
            document = inspection()
            env = document["Config"]["Env"]
            if kind == "duplicate":
                env.append("TELEGRAM_YASNO_STATUS_ENABLED=1")
            if kind == "wrong_path":
                env[1] = "YASNO_TRUSTED_CACHE_FILE=/data/writable.json"
            if kind == "missing_group":
                env.remove("YASNO_GROUP=10.1")
            with (
                self.subTest(kind=kind),
                self.assertRaises(ValueError),
            ):
                QA.verify_reader(document, "staging")

    def test_unknown_phase_is_always_rejected(self):
        """Misspelled rollout stages must fail before inspection."""

        with self.assertRaisesRegex(ValueError, "phase"):
            QA.verify_reader(inspection("0"), "deploy")

    def test_dry_run_does_not_access_docker_or_files(self):
        """Default exploratory mode never inspects credentials."""

        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=type(
                    "Args",
                    (),
                    {
                        "dry_run": True,
                        "check": False,
                    },
                )(),
            ),
            patch.object(QA, "docker_inspect") as inspect,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 0)
        inspect.assert_not_called()
        self.assertIn("no_docker_access=true", output.getvalue())

    def test_fixture_mode_does_not_need_docker(self):
        """A static inspect snapshot is enough for offline QA."""

        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "inspect.json"
            fixture.write_text(json.dumps(inspection()), encoding="utf-8")
            values = type(
                "Args",
                (),
                {
                    "dry_run": False,
                    "check": True,
                    "phase": "staging",
                    "container": "test",
                    "inspect_fixture": fixture,
                },
            )()
            with (
                patch.object(
                    QA.argparse.ArgumentParser,
                    "parse_args",
                    return_value=values,
                ),
                patch.object(QA, "docker_inspect") as inspect,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(QA.main(), 0)
            inspect.assert_not_called()
            self.assertIn("phase=staging", output.getvalue())

    def test_cli_failures_never_leak_environment_secrets(self):
        """Only a generic failure is reported, not inspect JSON."""

        document = inspection()
        document["Mounts"] = []
        values = type(
            "Args",
            (),
            {
                "dry_run": False,
                "check": True,
                "phase": "staging",
                "container": "bot",
                "inspect_fixture": None,
            },
        )()
        with (
            patch.object(
                QA.argparse.ArgumentParser,
                "parse_args",
                return_value=values,
            ),
            patch.object(QA, "docker_inspect", return_value=document),
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 1)
        self.assertIn("validation_failed", output.getvalue())
        self.assertNotIn("do-not-print", output.getvalue())


if __name__ == "__main__":
    unittest.main()
