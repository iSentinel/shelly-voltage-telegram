"""Test closed synthetic incident manifest and ordered evidence chain."""

import importlib.util
import json
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "incident_manifest",
    Path(__file__).resolve().parents[1] / "scripts/qa-incident-manifest.py",
)
assert SPEC and SPEC.loader
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def encode(value):
    """Serialize stable fixture bytes."""
    return json.dumps(value, sort_keys=True).encode()


class ManifestTests(unittest.TestCase):
    """Require consistent synthetic records and reject malformed evidence."""

    @staticmethod
    def fixture_fields():
        """Return synthetic identity and stage-specific claims."""
        identity = {
            "container_id": "a" * 64,
            "image_id": "sha256:" + "b" * 64,
            "started_at": 100,
            "receipt_sha256": "c" * 64,
            "journal_sha256": "d" * 64,
            "database_device": 1,
            "database_inode": 2,
            "writer_lock_device": 1,
            "writer_lock_inode": 3,
        }
        extra = {
            "baseline": {"writer_lease_held": True, "readiness_http": 200},
            "fault": {
                "fault_class": "general-runtime",
                "observation_sha256": "e" * 64,
            },
            "stopped": {
                "writer_quiescent": True,
                "history_sha256": "f" * 64,
                "rescue_sha256": "1" * 64,
                "pending_intents": 1,
                "wal_capture_sha256": "2" * 64,
            },
            "hold": {
                "writer_quiescent": True,
                "history_sha256": "f" * 64,
                "hold_receipt_sha256": "3" * 64,
            },
        }
        return identity, extra

    def setUp(self):
        """Create a fully synthetic four-stage evidence chain."""
        identity, extra = self.fixture_fields()
        self.records = {
            s: {
                "schema": 1,
                "stage": s,
                "synthetic": True,
                "observed_at": 101 + i,
                "identity": dict(identity),
                **extra[s],
            }
            for i, s in enumerate(QA.STAGES)
        }

    def bundle(self):
        """Recompute anchors, including after deliberately invalid edits."""
        previous = None
        stages = {}
        for stage, record in self.records.items():
            record["previous_sha256"] = previous
            stages[stage] = encode(record)
            previous = QA.digest(stages[stage])
        raw = encode(
            {
                "schema": 1,
                "synthetic": True,
                "stages": {s: QA.digest(v) for s, v in stages.items()},
            }
        )
        return raw, stages, QA.digest(raw)

    def check(self):
        """Review the exact current fixture."""
        return QA.check_manifest(*self.bundle())

    def test_complete_chain(self):
        """Structural consistency never grants recovery authority."""
        result = self.check()
        self.assertEqual(result["stages_checked"], 4)
        self.assertEqual(
            result["status"], "PASS_SYNTHETIC_INCIDENT_MANIFEST_ONLY"
        )
        for key, value in result.items():
            if key.endswith(
                ("_verified", "_authorized", "_qualified", "_approved")
            ):
                self.assertIs(value, False)

    def test_anchor_mismatch(self):
        """Refuse a different externally pinned manifest hash."""
        raw, stages, _ = self.bundle()
        with self.assertRaisesRegex(ValueError, "anchor mismatch"):
            QA.check_manifest(raw, stages, "0" * 64)

    def test_missing_stage(self):
        """All four stages are mandatory."""
        raw, stages, anchor = self.bundle()
        del stages["fault"]
        with self.assertRaisesRegex(ValueError, "manifest schema"):
            QA.check_manifest(raw, stages, anchor)

    def test_stage_bytes_changed(self):
        """Do not accept even whitespace changes to anchored bytes."""
        raw, stages, anchor = self.bundle()
        stages["fault"] += b" "
        with self.assertRaisesRegex(ValueError, "stage bytes mismatch"):
            QA.check_manifest(raw, stages, anchor)

    def test_restart(self):
        """A later StartedAt cannot silently inherit the baseline."""
        self.records["fault"]["identity"]["started_at"] = 101
        with self.assertRaisesRegex(ValueError, "chain mismatch"):
            self.check()

    def test_replaced_database(self):
        """Reject changed database inode even when hashes are recomputed."""
        self.records["stopped"]["identity"]["database_inode"] = 4
        with self.assertRaisesRegex(ValueError, "chain mismatch"):
            self.check()

    def test_wrong_chronology(self):
        """Stop evidence must postdate the fault."""
        self.records["stopped"]["observed_at"] = 102
        with self.assertRaisesRegex(ValueError, "chain mismatch"):
            self.check()

    def test_history_changed(self):
        """Hold must refer to the exact stopped history."""
        self.records["hold"]["history_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "held history mismatch"):
            self.check()

    def test_quiescence_required(self):
        """A stopped-state claim requires explicit quiescence."""
        self.records["stopped"]["writer_quiescent"] = False
        with self.assertRaisesRegex(ValueError, "quiescence"):
            self.check()

    def test_unknown_authorization_field(self):
        """Additional authority claims are never accepted."""
        self.records["hold"]["startup_authorized"] = True
        with self.assertRaisesRegex(ValueError, "stage schema"):
            self.check()

    def test_non_synthetic(self):
        """Do not accept a purported real incident with this reviewer."""
        self.records["fault"]["synthetic"] = False
        with self.assertRaisesRegex(ValueError, "stage schema"):
            self.check()

    def test_invalid_pending_count(self):
        """Booleans are not integer counts."""
        self.records["stopped"]["pending_intents"] = True
        with self.assertRaisesRegex(ValueError, "evidence integer"):
            self.check()

    def test_duplicate_and_nonfinite_json(self):
        """Reject ambiguous JSON before evidence comparison."""
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                QA.decode(raw)
