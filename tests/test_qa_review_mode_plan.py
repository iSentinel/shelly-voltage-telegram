"""Test static signed-source feature review and exact plan binding."""

import ast
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "review_plan", ROOT / "scripts/qa-review-mode-plan.py"
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class ReviewPlanTests(unittest.TestCase):
    """Verify rejection paths without authorizing any operation."""

    def test_current_candidate_source(self):
        """The reviewed candidate source contains the known pause branch."""
        QA.feature_present(ast.parse((ROOT / "app.py").read_bytes()))

    def test_old_source(self):
        """An old monitor cannot qualify merely by matching its signature."""
        tree = ast.parse("class Monitor:\n    def run(self): pass\n")
        with self.assertRaisesRegex(ValueError, "feature absent"):
            QA.feature_present(tree)

    def test_missing_pause_return(self):
        """A report loop alone is insufficient to show pause support."""
        tree = ast.parse((ROOT / "app.py").read_bytes())
        run = QA.class_member(tree, "Monitor", "run", ast.FunctionDef)
        for node in run.body:
            if isinstance(
                node, ast.If
            ) and "scheduled_reports_mode" in ast.unparse(node.test):
                node.body.pop()
        with self.assertRaisesRegex(ValueError, "feature absent"):
            QA.feature_present(tree)

    def test_exact_plan_anchor(self):
        """Recompute the canonical plan fingerprint, not just its label."""
        record = {"schema": 1, "deployment_authorized": False}
        anchor = QA.digest_plan(record)
        wrapper = {
            "status": "PASS_QA_MODE_PLAN_ONLY",
            "plan": record,
            "plan_sha256": anchor,
        }
        root = Path("/qa")
        with patch.object(
            QA.PAIR,
            "private_bytes",
            side_effect=lambda *_: json.dumps(wrapper).encode(),
        ):
            self.assertEqual(QA.read_plan(root, anchor), record)
            record["schema"] = 2
            with self.assertRaisesRegex(ValueError, "anchor mismatch"):
                QA.read_plan(root, anchor)

    def test_oci_image_binding(self):
        """An independently verified image must be the planned local ID."""
        info = {"Image": "sha256:" + "a" * 64}
        record = {
            "pair": {"digest": "sha256:" + "b" * 64, "source_commit": "c" * 40}
        }
        with (
            patch.object(QA.BOUND, "check_online"),
            patch.object(QA.PAIR.SUP, "verify_receipt"),
            patch.object(
                QA.OCI,
                "verify_chain",
                return_value={"image_id": "sha256:" + "d" * 64},
            ),
            self.assertRaisesRegex(ValueError, "local image binding mismatch"),
        ):
            QA.verify_feature_pair(Path("/qa"), info, {}, record, {}, "0" * 64)
