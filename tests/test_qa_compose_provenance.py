"""Exercise strict QA Compose and transaction provenance comparisons."""

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "provenance",
    Path(__file__).resolve().parents[1] / "scripts/qa-compose-provenance.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class ComposeProvenanceTests(unittest.TestCase):
    """Use synthetic records without actual startup or Docker operations."""

    def setUp(self):
        """Construct exact expected synthetic plan and image defaults."""
        self.root = Path("/var/lib/svt-report-mode-tx-qa.synthetic")
        self.image_id = "sha256:" + "a" * 64
        self.service = QA.expected_service(self.root, self.image_id)
        self.plan = {
            "name": "svt-report-mode-tx-qasynthetic",
            "services": {"shelly-voltage": self.service},
        }
        self.image = {
            "Config": {"Env": ["PATH=/bin", "LANG=C"], "Cmd": ["run"]}
        }
        self.info = {
            "Config": {
                "Env": [
                    "PATH=/bin",
                    "LANG=C",
                    "SCHEDULED_REPORTS_MODE=paused",
                ],
                "Cmd": ["run"],
            },
            "HostConfig": {
                "CgroupnsMode": "private",
                "Runtime": "runc",
                "UsernsMode": "",
            },
        }

    def test_plan_exact(self):
        """Accept only the closed fixture plan."""
        QA.check_plan(self.root, self.plan, self.image_id)

    def test_plan_drift(self):
        """Refuse command, environment, mounts and service changes."""
        for key, value in (
            ("command", ["other"]),
            ("privileged", True),
            ("environment", {"SCHEDULED_REPORTS_MODE": "enabled"}),
            ("volumes", []),
            ("pull_policy", "always"),
        ):
            plan = copy.deepcopy(self.plan)
            plan["services"]["shelly-voltage"][key] = value
            with self.assertRaisesRegex(ValueError, "profile mismatch"):
                QA.check_plan(self.root, plan, self.image_id)
        self.plan["services"]["other"] = {}
        with self.assertRaisesRegex(ValueError, "profile mismatch"):
            QA.check_plan(self.root, self.plan, self.image_id)

    def test_actual_config(self):
        """Image defaults plus resolved environment must match inspect."""
        QA.check_runtime(self.info, self.image, self.service)

    def test_config_drift(self):
        """Reject added environment and process overrides."""
        for key, value in (
            ("Env", self.info["Config"]["Env"] + ["EXTRA=yes"]),
            ("Cmd", ["other"]),
            ("WorkingDir", "/tmp"),
        ):
            info = copy.deepcopy(self.info)
            info["Config"][key] = value
            with self.assertRaisesRegex(ValueError, "config mismatch"):
                QA.check_runtime(info, self.image, self.service)

    def test_duplicate_env(self):
        """Reject ambiguous environment rather than selecting last value."""
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            QA.env_map(["MODE=paused", "MODE=enabled"])

    def test_host_drift(self):
        """Refuse additional security-relevant settings."""
        for key, value in (
            ("Sysctls", {"x": "y"}),
            ("GroupAdd", ["0"]),
            ("CgroupnsMode", "host"),
            ("Runtime", "other"),
        ):
            info = copy.deepcopy(self.info)
            info["HostConfig"][key] = value
            with self.assertRaisesRegex(ValueError, "host configuration"):
                QA.check_runtime(info, self.image, self.service)

    def journal(self, phase="verified", pair=None, anchor=None):
        """Review a fake journal while preserving real matching logic."""
        receipt = {"transaction_token": "cutover-QA123456"}
        policy = {"pair": receipt}
        record = {
            "phase": phase,
            "supervised": True,
            "pair": receipt if pair is None else pair,
            "qa_fixture": True,
        }
        raw = json.dumps(record).encode()
        with (
            patch.object(QA, "load_private", return_value=receipt),
            patch.object(QA.PAIR.TX, "protected"),
            patch.object(QA.PAIR, "private_bytes", return_value=raw),
            patch.object(QA.PAIR.SUP, "verify_paired_source_archive"),
        ):
            QA.check_journal(self.root, policy, anchor or QA.PAIR.digest(raw))

    def test_journal_exact(self):
        """Verify matching token and verified journal."""
        self.journal()

    def test_journal_drift(self):
        """Refuse stale phase, wrong pair or independently pinned hash."""
        for options in (
            {"phase": "prepared"},
            {"pair": {}},
            {"anchor": "0" * 64},
        ):
            with self.assertRaises(ValueError):
                self.journal(**options)
