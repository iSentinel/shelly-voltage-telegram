import argparse
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "promotion-check.py"
SPEC = importlib.util.spec_from_file_location("promotion_check", SCRIPT)
PROMOTION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROMOTION
assert SPEC.loader is not None
SPEC.loader.exec_module(PROMOTION)


def write_versions(root: Path) -> None:
    """Create the three version markers used by promotion checks."""

    (root / "VERSION").write_text("0.5.6-hw\n", encoding="utf-8")
    (root / "RELEASE_VERSION").write_text("0.5.5-hw\n", encoding="utf-8")
    (root / "LIVE_VERSION").write_text("0.5.4-hw\n", encoding="utf-8")


def args(root: Path, env: Path) -> argparse.Namespace:
    """Build CLI-like arguments for direct function tests."""

    return argparse.Namespace(
        root=root,
        live_env=env,
        db=root / "db.sqlite3",
        baseline=root / "baseline.json",
        health_url="http://health",
        ready_url="http://ready",
        timeout=1.0,
        json_output=False,
    )


def good_recovery() -> dict[str, object]:
    """Return one successful recovery verification payload."""

    return {
        "status": "pass",
        "health": {"version": "0.5.4-hw", "connectivity": "online"},
        "ready": {"ready": True},
        "errors": [],
    }


class PromotionCheckTests(unittest.TestCase):
    def test_promotion_passes_after_valid_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_versions(root)
            env = root / ".env"
            env.write_text("SHELLY_METER_TYPE=pro_em50\n", encoding="utf-8")
            recovery = Mock(verify_payload=Mock(return_value=good_recovery()))
            with patch.object(
                PROMOTION, "load_recovery_module", return_value=recovery
            ):
                payload = PROMOTION.build_payload(args(root, env))
            self.assertEqual(payload["status"], "pass")
            self.assertTrue(payload["read_only"])

    def test_wrong_meter_type_blocks_promotion(self):
        errors = PROMOTION.promotion_errors(
            {
                "candidate": "0.5.6-hw",
                "release": "0.5.5-hw",
                "live": "0.5.4-hw",
            },
            "pro3em",
            good_recovery(),
        )
        self.assertIn("live meter type is not pro_em50", errors)

    def test_live_version_mismatch_blocks_promotion(self):
        recovery = good_recovery()
        recovery["health"] = {"version": "0.5.3-hw", "connectivity": "online"}
        errors = PROMOTION.promotion_errors(
            {
                "candidate": "0.5.6-hw",
                "release": "0.5.5-hw",
                "live": "0.5.4-hw",
            },
            "pro_em50",
            recovery,
        )
        self.assertIn(
            "live service version does not match LIVE_VERSION", errors
        )

    def test_recovery_failure_is_preserved(self):
        recovery = good_recovery()
        recovery["errors"] = ["captured outage is still open"]
        errors = PROMOTION.promotion_errors(
            {
                "candidate": "0.5.6-hw",
                "release": "0.5.5-hw",
                "live": "0.5.4-hw",
            },
            "pro_em50",
            recovery,
        )
        self.assertIn("captured outage is still open", errors)

    def test_missing_meter_setting_is_fail_closed(self):
        errors = PROMOTION.promotion_errors(
            {
                "candidate": "0.5.6-hw",
                "release": "0.5.5-hw",
                "live": "0.5.4-hw",
            },
            "",
            good_recovery(),
        )
        self.assertIn("live meter type is not pro_em50", errors)


if __name__ == "__main__":
    unittest.main()
