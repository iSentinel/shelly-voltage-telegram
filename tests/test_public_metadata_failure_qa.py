"""Contract tests for offline public deployed-version metadata QA."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-public-metadata-failure-qa.py"
)
SPEC = importlib.util.spec_from_file_location("public_metadata_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class PublicMetadataFaultTests(unittest.TestCase):
    """Require fail-closed classification and atomic JSON publishing."""

    def setUp(self) -> None:
        """Create a temporary, entirely invented public-state directory."""
        temp = tempfile.TemporaryDirectory(prefix="metadata-unit-")
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.old, self.current, _, _ = QA.baseline()
        self.target = self.folder / "deployed.json"

    def test_exact_metadata_contract(self) -> None:
        """Reject unexpected keys, invalid names and truncated digests."""
        self.assertTrue(QA.valid_deployed(self.current))
        self.assertFalse(QA.valid_deployed(None))
        self.assertFalse(QA.valid_deployed({"version": "0.5.21-hw"}))
        invalid = {**self.current, "token": "never"}
        self.assertFalse(QA.valid_deployed(invalid))

    def test_pending_blocks_even_with_public_match(self) -> None:
        """Never report success while a pending transaction exists."""
        self.assertEqual(
            QA.status(self.current, self.current, pending=True),
            "PENDING_BLOCKED",
        )

    def test_stale_public_must_reconcile(self) -> None:
        """A cleared pending marker does not imply publication."""
        self.assertEqual(
            QA.status(self.current, self.old),
            "RECONCILE_REQUIRED",
        )

    def test_signed_image_identity_required(self) -> None:
        """Mocked certificate flag and image reference are separate gates."""
        self.assertEqual(
            QA.status(self.current, self.old, verified=False),
            "TRUST_NOT_VERIFIED_BLOCKED",
        )
        self.assertEqual(
            QA.status(self.current, self.old, running_id="sha256:" + "d" * 64),
            "RUNNING_IMAGE_ID_MISMATCH_BLOCKED",
        )

    def test_corrupted_public_refused(self) -> None:
        """Invalid or extra JSON keys cannot masquerade as a version."""
        self.assertEqual(
            QA.status(self.current, {"invalid": True}),
            "PUBLIC_CORRUPT_BLOCKED",
        )

    def test_atomic_before_replace_keeps_old(self) -> None:
        """Failure before rename must not modify existing public bytes."""
        original = json.dumps(self.old).encode()
        self.target.write_bytes(original)
        with self.assertRaises(QA.InjectedFailure):
            QA.atomic_publish(self.target, self.current, "after_file_fsync")
        self.assertEqual(self.target.read_bytes(), original)
        self.assertFalse(list(self.folder.glob(".deployed-*")))

    def test_atomic_after_replace_complete(self) -> None:
        """Interrupted caller still sees a complete replacement JSON."""
        self.target.write_text(json.dumps(self.old))
        with self.assertRaises(QA.InjectedFailure):
            QA.atomic_publish(self.target, self.current, "after_replace")
        self.assertEqual(QA.read_public(self.target), self.current)

    def test_atomic_after_directory_fsync(self) -> None:
        """Directory metadata flush leaves complete JSON."""
        with self.assertRaises(QA.InjectedFailure):
            QA.atomic_publish(self.target, self.current, "after_dir_fsync")
        self.assertEqual(QA.read_public(self.target), self.current)

    def test_atomic_success_and_permissions(self) -> None:
        """Successful publish is mode 0600 with no temporary siblings."""
        QA.atomic_publish(self.target, self.current)
        self.assertEqual(QA.read_public(self.target), self.current)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertFalse(list(self.folder.glob(".deployed-*")))

    def test_read_public_missing_and_corrupt(self) -> None:
        """Missing means reconciliation, malformed JSON is an error."""
        self.assertIsNone(QA.read_public(self.target))
        self.target.write_text("{invalid")
        with self.assertRaises(json.JSONDecodeError):
            QA.read_public(self.target)

    def test_default_cli_is_inert(self) -> None:
        """No network or host preflight is executed without opt-in."""
        captured = io.StringIO()
        with (
            patch.object(sys, "argv", ["qa"]),
            patch.object(QA, "guest_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(captured),
        ):
            self.assertEqual(QA.main(), 0)
        self.assertIn("DRY_RUN", captured.getvalue())

    def test_confirmation_required(self) -> None:
        """Even a synthetic run requires its exact token."""
        with (
            patch.object(
                sys, "argv", ["qa", "--run-fixtures", "--confirm", "yes"]
            ),
            patch.object(QA, "guest_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 2)

    def test_non_qa_host_rejected(self) -> None:
        """Never perform even fixture execution on production."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(RuntimeError, "OFFLINE_QA"),
        ):
            QA.guest_guard()

    def test_concurrent_reader_atomicity(self) -> None:
        """Repeated atomic replacement never exposes partial public JSON."""
        QA.atomic_publish(self.target, self.old)
        failures: list[str] = []
        stop = threading.Event()
        expected = (self.old, self.current)
        entered = threading.Event()

        def reader() -> None:
            """Observe the same file while replacements are committed."""
            entered.set()
            while not stop.is_set():
                try:
                    actual = json.loads(self.target.read_text())
                    if actual not in expected:
                        failures.append("UNKNOWN_PUBLIC_VERSION")
                except (OSError, json.JSONDecodeError):
                    failures.append("PARTIAL_OR_MISSING_JSON")
                if failures:
                    return

        worker = threading.Thread(target=reader, daemon=True)
        worker.start()
        self.assertTrue(entered.wait(3))
        try:
            for _ in range(150):
                QA.atomic_publish(self.target, self.current)
                QA.atomic_publish(self.target, self.old)
        finally:
            stop.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(QA.read_public(self.target), self.old)

    def test_all_synthetic_scenarios(self) -> None:
        """The complete test matrix remains explicit and reproducible."""
        cases = []
        QA.state_checks(cases)
        QA.fault_checks(self.folder, cases)
        self.assertEqual(len(cases), 17)


if __name__ == "__main__":
    unittest.main()
