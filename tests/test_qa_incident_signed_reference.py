"""Check exact reference evidence without qualifying a deployed runtime."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "incident_reference",
    Path(__file__).resolve().parents[1]
    / "scripts/qa-incident-signed-reference.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class ReferenceTests(unittest.TestCase):
    """Test pinned inputs and the non-authorizing incident boundary."""

    def setUp(self):
        """Create local synthetic receipt bytes and anchor arguments."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.pair = {
            "version": "0.5.28-hw",
            "digest": "sha256:" + "a" * 64,
            "source_commit": "b" * 40,
            "source_sha256": "c" * 64,
            "transaction_token": "cutover-QA123456",
        }
        self.receipt = dict(
            self.pair,
            commit=self.pair["source_commit"],
            archive_sha256=self.pair["source_sha256"],
        )
        self.raw = json.dumps(self.receipt).encode()
        self.anchor = QA.PAIR.digest(self.raw)
        self.args = SimpleNamespace(
            root=self.root,
            token="123456abcdef",
            fault="general-runtime",
            scope_sha256="d" * 64,
            hold_sha256="e" * 64,
            receipt_sha256=self.anchor,
            journal_sha256="f" * 64,
            online_sha256="1" * 64,
        )

    def test_receipt_anchor(self):
        """The exact receipt bytes and both aliases must match."""
        with patch.object(QA.PAIR, "private_bytes", return_value=self.raw):
            self.assertEqual(
                QA.receipt_pair(self.root, self.anchor),
                (self.receipt, self.pair),
            )
            with self.assertRaisesRegex(ValueError, "anchor mismatch"):
                QA.receipt_pair(self.root, "0" * 64)

    def test_alias_drift(self):
        """A freshly hashed contradictory receipt is still invalid."""
        raw = json.dumps(dict(self.receipt, commit="f" * 40)).encode()
        with (
            patch.object(QA.PAIR, "private_bytes", return_value=raw),
            self.assertRaisesRegex(ValueError, "alias mismatch"),
        ):
            QA.receipt_pair(self.root, QA.PAIR.digest(raw))

    def test_reference_checks(self):
        """Require every reference boundary before returning pair evidence."""
        journal = json.dumps(
            {
                "phase": "verified",
                "pair": self.receipt,
                "qa_fixture": True,
                "supervised": True,
            }
        ).encode()
        proof = {"image_id": "sha256:" + "a" * 64, "diff_ids": ["x"]}
        image = {"Id": proof["image_id"]}
        with (
            patch.object(
                QA, "receipt_pair", return_value=(self.receipt, self.pair)
            ),
            patch.object(QA.BOUND, "check_online") as online,
            patch.object(QA.CP, "check_journal") as check_journal,
            patch.object(QA.PAIR, "private_bytes", return_value=journal),
            patch.object(QA.OCI, "verify_chain", return_value=proof),
            patch.object(QA.PAIR, "inspect_image", return_value=image),
            patch.object(QA.OCI, "verify_local") as local,
            patch.object(QA.PAIR.SUP, "verify_receipt") as receipt,
        ):
            result = QA.check_reference(
                self.root, self.anchor, "f" * 64, "1" * 64
            )
        online.assert_called_once_with(
            self.root, {"pair": self.pair}, "1" * 64
        )
        check_journal.assert_called_once_with(
            self.root, {"pair": self.pair}, "f" * 64
        )
        local.assert_called_once_with(proof, image)
        receipt.assert_called_once()
        self.assertEqual(result["offline_layers_verified"], 1)

    def test_bad_journal_schema(self):
        """Even a reviewed synthetic journal must have closed fields."""
        with (
            patch.object(
                QA, "receipt_pair", return_value=(self.receipt, self.pair)
            ),
            patch.object(QA.BOUND, "check_online"),
            patch.object(QA.CP, "check_journal"),
            patch.object(
                QA.PAIR, "private_bytes", return_value=b'{"extra":1}'
            ),
            self.assertRaisesRegex(ValueError, "journal fields"),
        ):
            QA.check_reference(self.root, self.anchor, "f" * 64, "1" * 64)

    def test_hold_drift(self):
        """Do not accept a reference if the hold changed during review."""
        with (
            patch.object(
                QA.HOLD, "inspect", side_effect=[{"rows": 4}, {"rows": 5}]
            ),
            patch.object(QA, "check_reference", return_value={}),
            self.assertRaisesRegex(ValueError, "hold changed"),
        ):
            QA.review(self.args)

    def test_reference_only(self):
        """No reference proof implies installation or fault attribution."""
        with (
            patch.object(QA.HOLD, "inspect", return_value={"rows": 4}),
            patch.object(
                QA, "check_reference", return_value={"pair": self.pair}
            ),
        ):
            result = QA.review(self.args)
        self.assertTrue(result["synthetic_journal"])
        for name in (
            "installed_pair_verified",
            "signatures_reverified_offline",
            "fault_attribution_verified",
            "release_authorized",
            "startup_authorized",
            "deployment_authorized",
        ):
            self.assertIs(result[name], False)
