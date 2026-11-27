"""Test conservative same-runtime mode planning without changing files."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "mode_plan",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-same-runtime-recovery-plan.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class ModePlanTests(unittest.TestCase):
    """Exercise exact source and target config binding."""

    def setUp(self):
        """Use synthetic nonsecret settings and release identity."""
        self.raw = (
            b"# fixture\nPOLL_INTERVAL=10\n\n"
            b"SCHEDULED_REPORTS_MODE=enabled\nLANGUAGE=uk\n"
        )
        self.receipt = {
            "version": "0.5.99-hw",
            "digest": "sha256:" + "a" * 64,
            "source_commit": "b" * 40,
            "source_sha256": "c" * 64,
            "transaction_token": "cutover-QA123456",
            "commit": "b" * 40,
            "archive_sha256": "c" * 64,
            "pair_status": "PASS_SIGNED_SOURCE_OCI_PAIR_ONLY",
            "source_signature_verified": True,
            "oci_signature_verified": True,
            "oci_attestations_verified": True,
        }

    def plan(self, **options):
        """Return a fixture plan with optional changed runtime facts."""
        args = {
            "image": "sha256:" + "d" * 64,
            "container": "e" * 64,
            "device": 1,
            "inode": 2,
        }
        args.update(options)
        return QA.plan(self.receipt, self.raw, "enabled", "paused", **args)

    def test_exact_reversible_bytes(self):
        """Only the canonical mode assignment changes."""
        target = QA.transition(self.raw, "enabled", "paused")
        self.assertEqual(
            target, self.raw.replace(b"MODE=enabled", b"MODE=paused")
        )
        self.assertEqual(QA.transition(target, "paused", "enabled"), self.raw)

    def test_plan_non_authorizing(self):
        """The plan preserves full pair and both configuration hashes."""
        result = self.plan()
        self.assertEqual(result["pair"], QA.pair_identity(self.receipt))
        self.assertEqual(result["source_config_sha256"], QA.sha(self.raw))
        self.assertEqual(
            result["target_config_sha256"],
            QA.sha(QA.transition(self.raw, "enabled", "paused")),
        )
        self.assertIs(result["startup_authorized"], False)
        self.assertIs(result["deployment_authorized"], False)

    def test_refuse_ambiguous_configuration(self):
        """Never guess duplicate, interpolated or complex assignments."""
        for data in (
            self.raw + b"SCHEDULED_REPORTS_MODE=enabled\n",
            self.raw + b"LANGUAGE=en\n",
            self.raw + b"INDIRECT=$SCHEDULED_REPORTS_MODE\n",
            self.raw + b'VALUE="multi\nline"\n',
            self.raw.replace(b"MODE=enabled", b"MODE =enabled"),
            self.raw.replace(b"MODE=enabled", b"MODE='enabled'"),
            self.raw.replace(b"MODE=enabled", b"MODE=paused"),
            self.raw.replace(b"SCHEDULED_REPORTS_MODE=enabled\n", b""),
            self.raw.replace(b"\n", b"\r\n"),
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                QA.transition(data, "enabled", "paused")

    def test_refuse_same_mode(self):
        """The transition must change one mode."""
        with self.assertRaises(ValueError):
            QA.transition(self.raw, "enabled", "enabled")

    def test_refuse_pair_drift(self):
        """Do not accept disagreeing aliases or incomplete proof flags."""
        for key, value in (
            ("commit", "f" * 40),
            ("transaction_token", "../invalid"),
            ("source_signature_verified", False),
        ):
            original = self.receipt[key]
            self.receipt[key] = value
            with self.assertRaises(ValueError):
                self.plan()
            self.receipt[key] = original

    def test_refuse_invalid_runtime(self):
        """Require real-shaped immutable identities and SQLite inode."""
        for options in (
            {"image": "latest"},
            {"container": "short"},
            {"inode": 0},
            {"device": True},
        ):
            with self.assertRaises(ValueError):
                self.plan(**options)

    def test_duplicate_evidence_fields(self):
        """Do not accept ambiguous JSON evidence."""
        with self.assertRaisesRegex(ValueError, "duplicate"):
            QA.unique_object([("version", 1), ("version", 2)])
