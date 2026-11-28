"""Exercise fixed Report Worker profile and launch evidence comparisons."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "report_start",
    Path(__file__).resolve().parents[1] / "scripts/qa-durable-report-start.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class RuntimeProfileTests(unittest.TestCase):
    """Test fixed synthetic runtime policy without starting containers."""

    def setUp(self):
        """Model a reviewed isolated fixture."""
        self.root = Path("/var/lib/svt-report-mode-tx-qa.synthetic")
        self.info = {"Config": {}, "HostConfig": {}, "Mounts": []}
        self.old = {
            "device": 1,
            "inode": 2,
            "last_id": 12,
            "rows": 12,
            "prefix_sha256": "a" * 64,
            "metadata_sha256": "b" * 64,
        }
        self.journal = {"target": "paused", "baseline": self.old}
        self.record = {
            "config_sha256": QA.START.hash_object({}),
            "host_config_sha256": QA.START.hash_object({}),
            "mounts_sha256": QA.START.hash_object([]),
            "compose_sha256": "c" * 64,
        }

    def test_closed_source(self):
        """No network, external build, arbitrary command or writable probe."""
        service = QA.source_plan(self.root, "paused")["services"][
            "shelly-voltage"
        ]
        self.assertEqual(service["network_mode"], "none")
        self.assertEqual(service["pull_policy"], "never")
        self.assertEqual(service["image"], QA.START.IMAGE)
        self.assertEqual(service["command"], ["/qa/probe.py"])
        self.assertEqual(service["environment"]["QA_STAGE"], "paused")
        self.assertTrue(service["volumes"][-1].endswith(":/qa:ro"))
        self.assertNotIn("build", service)

    def test_source_drift(self):
        """Unexpected Compose source is rejected before resolution."""
        source = QA.source_plan(self.root, "paused")
        source["services"]["shelly-voltage"]["privileged"] = True
        with (
            patch.object(
                QA.OP,
                "config",
                return_value=b"SCHEDULED_REPORTS_MODE=paused\n",
            ),
            patch.object(QA, "check_probe"),
            patch.object(
                QA.START,
                "read_bytes",
                return_value=json.dumps(source).encode(),
            ),
            patch.object(QA.CP, "resolved_plan") as resolve,
            self.assertRaisesRegex(ValueError, "Compose source"),
        ):
            QA.review_plan(self.root, "paused")
        resolve.assert_not_called()

    def test_corrected_candidate_not_historical_profile(self):
        """Do not accept the corrected image under the historical pin."""
        source = QA.source_plan(self.root, "paused")
        source["services"]["shelly-voltage"]["image"] = (
            "sha256:e37eeb2f2235acc17d5316c0a4789f4b629"
            "fe34cf86805bfdfabe7720c3571d3"
        )
        with (
            patch.object(
                QA.OP,
                "config",
                return_value=b"SCHEDULED_REPORTS_MODE=paused\n",
            ),
            patch.object(QA, "check_probe"),
            patch.object(
                QA.START,
                "read_bytes",
                return_value=json.dumps(source).encode(),
            ),
            patch.object(QA.CP, "resolved_plan") as resolve,
            self.assertRaisesRegex(ValueError, "Compose source"),
        ):
            QA.review_plan(self.root, "paused")
        resolve.assert_not_called()

    def observed(self, history):
        """Review a synthetic post-start observation."""
        with (
            patch.object(
                QA, "check_runtime", return_value={"compose_sha256": "c" * 64}
            ),
            patch.object(QA.TX, "history_snapshot", return_value=history),
        ):
            return QA.check_observed(
                self.root, self.info, self.record, self.journal
            )

    def test_history_preserved(self):
        """Accept growth while retaining the old prefix and paused metadata."""
        current = dict(self.old, rows=30, last_id=30)
        self.assertEqual(self.observed(current), current)

    def test_paused_metadata_drift(self):
        """Refuse changed pending or delivered report metadata in pause."""
        with self.assertRaisesRegex(ValueError, "paused metadata"):
            self.observed(dict(self.old, metadata_sha256="d" * 64))

    def test_prefix_drift(self):
        """Refuse changed historical rows even if the inode is identical."""
        with self.assertRaisesRegex(ValueError, "historical prefix"):
            self.observed(dict(self.old, prefix_sha256="d" * 64))

    def test_inspect_drift(self):
        """Changed HostConfig is rejected after the claim."""
        self.info["HostConfig"]["Privileged"] = True
        with self.assertRaisesRegex(ValueError, "identity drift"):
            self.observed(self.old)

    def test_started_fixture_refused_at_arm(self):
        """Already running or previously launched containers cannot arm."""
        info = {
            "Id": "a" * 64,
            "State": {"Running": False, "StartedAt": "previous"},
        }
        with self.assertRaisesRegex(ValueError, "stopped runtime"):
            QA.validate(self.root, info, self.journal)
