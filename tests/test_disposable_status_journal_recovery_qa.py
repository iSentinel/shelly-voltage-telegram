"""Unit tests for isolated durable status journal and B/A lock model."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-status-journal-recovery-qa.py"
)
SPEC = importlib.util.spec_from_file_location("journal_qa", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


class JournalTests(unittest.TestCase):
    """Exercise only synthetic local metadata, never production state."""

    def setUp(self) -> None:
        """Prepare an invented, private, pending update transaction."""
        folder = tempfile.TemporaryDirectory(prefix="journal-unit-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        QA.initialize(self.root)
        self.trust = QA.trusted()

    def published(self) -> None:
        """Write both versions and durable published journal."""
        for phase in ("private_written", "public_written", "published"):
            QA.checkpoint(self.root, phase)

    def status(self, trust: tuple | None = None) -> str:
        """Return classification with explicitly mocked trust evidence."""
        return QA.classify(
            self.root, *(self.trust if trust is None else trust)
        )

    def test_prepared_blocked(self) -> None:
        """Prepare cannot mean commit."""
        self.assertEqual(self.status(), "BLOCKED_PREPARED")

    def test_private_without_publish_blocked(self) -> None:
        """Writing only private deployed version cannot mean success."""
        QA.checkpoint(self.root, "private_written")
        self.assertEqual(self.status(), "BLOCKED_PREPARED")

    def test_both_versions_without_journal_blocked(self) -> None:
        """Matching deployed files do not replace publication journal."""
        QA.checkpoint(self.root, "private_written")
        QA.checkpoint(self.root, "public_written")
        self.assertEqual(self.status(), "BLOCKED_PREPARED")

    def test_missing_journal_blocked(self) -> None:
        """Missing journal is not permission to publish."""
        (self.root / "journal.json").unlink()
        self.assertEqual(self.status(), "BLOCKED_NO_JOURNAL")

    def test_corrupt_public_blocked(self) -> None:
        """Malformed public JSON must fail closed."""
        self.published()
        (self.root / "public.json").write_text("{bad-json")
        self.assertEqual(self.status(), "BLOCKED_INVALID_METADATA")

    def test_old_public_no_pending_blocked(self) -> None:
        """No pending marker cannot override a stale public version."""
        self.published()
        (self.root / "pending-update").unlink()
        QA.write_json(self.root / "public.json", QA.OLD, 0o644)
        self.assertEqual(self.status(), "BLOCKED_METADATA_MISMATCH")

    def test_no_signature_proof_blocked(self) -> None:
        """An explicit false trust input blocks the transaction."""
        self.published()
        _, reference, image = self.trust
        self.assertEqual(
            self.status((False, reference, image)),
            "BLOCKED_UNVERIFIED_RUNTIME",
        )

    def test_wrong_manifest_digest_blocked(self) -> None:
        """Old immutable OCI reference cannot authenticate new journal."""
        self.published()
        _, _, image = self.trust
        reference = (
            "ghcr.io/isentinel/shelly-voltage-telegram@" + QA.OLD["digest"]
        )
        self.assertEqual(
            self.status((True, reference, image)), "BLOCKED_UNVERIFIED_RUNTIME"
        )

    def test_wrong_image_id_blocked(self) -> None:
        """A separate Docker image-id mismatch must block."""
        self.published()
        _, reference, _ = self.trust
        self.assertEqual(
            self.status((True, reference, "sha256:" + "a" * 64)),
            "BLOCKED_UNVERIFIED_RUNTIME",
        )

    def test_invalid_phase_blocked(self) -> None:
        """Reject any unknown phase."""
        self.published()
        QA.write_json(self.root / "journal.json", QA.journal("other"))
        self.assertEqual(self.status(), "BLOCKED_INVALID_PHASE")

    def test_other_release_blocked(self) -> None:
        """Reject a journal for an unrelated digest."""
        self.published()
        record = {**QA.journal("published"), "digest": QA.OLD["digest"]}
        QA.write_json(self.root / "journal.json", record)
        self.assertEqual(self.status(), "BLOCKED_OTHER_RELEASE")

    def test_after_unlink_reconcile_needed(self) -> None:
        """Unlinked pending without committed journal is not success."""
        self.published()
        (self.root / "pending-update").unlink()
        self.assertEqual(self.status(), "RECONCILE_REQUIRED")

    def test_reconcile_with_pending(self) -> None:
        """Both real file locks allow idempotent mock-trusted commit."""
        self.published()
        self.assertEqual(QA.reconcile(self.root, *self.trust), "COMMITTED")
        self.assertFalse((self.root / "pending-update").exists())

    def test_reconcile_without_pending(self) -> None:
        """The post-unlink gap can be repaired only with lock ownership."""
        self.published()
        (self.root / "pending-update").unlink()
        self.assertEqual(QA.reconcile(self.root, *self.trust), "COMMITTED")

    def test_repeat_commit_idempotent(self) -> None:
        """A repeated commit must not change public bytes."""
        self.published()
        QA.reconcile(self.root, *self.trust)
        original = (self.root / "public.json").read_bytes()
        self.assertEqual(QA.reconcile(self.root, *self.trust), "COMMITTED")
        self.assertEqual((self.root / "public.json").read_bytes(), original)

    def test_busy_B_fails_closed(self) -> None:
        """A different handle holding B EX prevents reconciliation."""
        self.published()
        with (self.root / "maintenance.lock").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(QA.Blocked, "LOCK_BUSY_maintenance"):
                QA.reconcile(self.root, *self.trust)
        self.assertEqual(self.status(), "RECONCILE_REQUIRED")

    def test_busy_A_fails_closed(self) -> None:
        """A different handle holding A EX prevents reconciliation."""
        self.published()
        with (self.root / "updater.lock").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(QA.Blocked, "LOCK_BUSY_updater"):
                QA.reconcile(self.root, *self.trust)
        self.assertEqual(self.status(), "RECONCILE_REQUIRED")

    def assert_child_lock_blocks(self, name: str) -> None:
        """A separate process holding the same inode must block B/A."""
        self.published()
        command = (
            "import fcntl,sys,time;"
            "f=open(sys.argv[1], 'rb');"
            "fcntl.flock(f, fcntl.LOCK_EX);"
            "print('HELD',flush=True);"
            "time.sleep(10)"
        )
        with subprocess.Popen(
            [sys.executable, "-c", command, str(self.root / name)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            try:
                self.assertEqual(child.stdout.readline().strip(), "HELD")
                with self.assertRaisesRegex(QA.Blocked, "LOCK_BUSY_" + name):
                    QA.reconcile(self.root, *self.trust)
            finally:
                child.terminate()
                child.wait(timeout=3)
        self.assertEqual(self.status(), "RECONCILE_REQUIRED")

    def test_child_process_holds_maintenance_B(self) -> None:
        """Independent B EX owner blocks the journal reconciliation."""
        self.assert_child_lock_blocks("maintenance.lock")

    def test_child_process_holds_updater_A(self) -> None:
        """Independent A EX owner blocks the journal reconciliation."""
        self.assert_child_lock_blocks("updater.lock")

    def test_committed_with_pending_blocked(self) -> None:
        """Contradictory record and marker are never considered safe."""
        self.published()
        QA.write_json(self.root / "journal.json", QA.journal("committed"))
        self.assertEqual(self.status(), "BLOCKED_PENDING_AFTER_COMMIT")

    def test_private_public_modes(self) -> None:
        """Public 0644 and journal 0600 stay inside private 0700 dir."""
        self.published()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE((self.root / "public.json").stat().st_mode), 0o644
        )
        self.assertEqual(
            stat.S_IMODE((self.root / "journal.json").stat().st_mode), 0o600
        )
        self.assertFalse(list(self.root.glob(".journal-*")))

    def test_journal_symlink_blocked(self) -> None:
        """A symlink never supplies an authoritative journal."""
        path = self.root / "journal.json"
        path.unlink()
        path.symlink_to(self.root / "public.json")
        self.assertEqual(self.status(), "BLOCKED_INVALID_METADATA")

    def test_default_cli_noop(self) -> None:
        """Default CLI never calls guest guard or starts subprocesses."""
        with (
            patch.object(sys, "argv", ["qa"]),
            patch.object(QA, "guest_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 0)

    def test_confirmation_required(self) -> None:
        """A generic yes does not start fault testing."""
        with (
            patch.object(
                sys, "argv", ["qa", "--run-disposable", "--confirm", "yes"]
            ),
            patch.object(QA, "guest_guard", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(QA.main(), 2)

    def test_nonroot_guest_denied(self) -> None:
        """No real fault test can begin outside guarded offline root."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(QA.Blocked, "OFFLINE_LXC114_ONLY"),
        ):
            QA.guest_guard()


if __name__ == "__main__":
    unittest.main()
