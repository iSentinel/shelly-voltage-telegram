"""Test fail-closed Compose admission without starting real Docker."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/legacy-compose-start-gate.py"
SPEC = importlib.util.spec_from_file_location("qa_compose_start_gate", SOURCE)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


class ComposeStartGateTests(unittest.TestCase):
    """Use only fabricated local lock files and no Docker subprocess."""

    def setUp(self) -> None:
        """Build disposable 0700 owner-gate and pinned override fixture."""
        temp = tempfile.TemporaryDirectory(prefix="qa-compose-gate-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.gate = self.root / "gate"
        self.gate.mkdir(mode=0o700)
        self.image = (
            "ghcr.io/isentinel/shelly-voltage-telegram@sha256:" + "a" * 64
        )
        self.override = self.root / "pinned.yaml"
        self.override.write_text(
            "services:\n  shelly-voltage:\n    image: " + self.image + "\n"
        )
        self.override.chmod(0o600)
        for name in ("G", "B", "W"):
            path = self.gate / name
            path.touch()
            path.chmod(0o640)
        self.permit = self.gate / "compose-permit.json"
        self.update_permit()

    def update_permit(self, **changes: object) -> None:
        """Write an invented time-limited gated-only permission."""
        data = {
            "schema": 1,
            "mode": "gated-only",
            "signed_oci_ref": self.image,
            "writer_protocol": "G_SH_B_SH_W_EX",
            "expires_unix": time.time() + 90,
        }
        data.update(changes)
        self.permit.write_text(json.dumps(data))
        self.permit.chmod(0o600)

    @contextlib.contextmanager
    def synthetic_root(self):
        """Mock ONLY root ownership so UID65534 can test core decisions."""

        def open_stub(path: Path, mode: int) -> int:
            """Return one invented local lockfile descriptor."""
            return os.open(path, os.O_RDONLY | os.O_NOFOLLOW)

        with (
            patch.object(QA, "trusted_dir"),
            patch.object(QA, "trusted_fd", side_effect=open_stub),
            patch.object(QA.os, "geteuid", return_value=0),
        ):
            yield

    def run_cli(self, *args: str) -> int:
        """Execute refusal/default paths without checking actual host."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "check_gate", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_inert(self) -> None:
        """A bare invocation cannot touch G/B/W or Docker."""
        self.assertEqual(self.run_cli(), 0)

    def test_missing_parameters_refused(self) -> None:
        """Explicit --check needs both exact paths."""
        self.assertEqual(self.run_cli("--check"), 2)

    def test_known_pinned_image_accepts_permit(self) -> None:
        """A short-lived permit parses with an exact pinned image."""
        with self.synthetic_root():
            self.assertEqual(QA.check_permit(self.permit), self.image)

    def test_expired_permit_rejected(self) -> None:
        """An old permit cannot permit a new Compose launch."""
        self.update_permit(expires_unix=time.time() - 10)
        with (
            self.synthetic_root(),
            self.assertRaisesRegex(QA.AdmissionDenied, "EXPIRED"),
        ):
            QA.check_permit(self.permit)

    def test_future_permit_over_five_minutes_refused(self) -> None:
        """A long-lived authorization cannot silently bypass maintenance."""
        self.update_permit(expires_unix=time.time() + 800)
        with (
            self.synthetic_root(),
            self.assertRaisesRegex(QA.AdmissionDenied, "TOO_LONG"),
        ):
            QA.check_permit(self.permit)

    def test_unsigned_image_tag_rejected(self) -> None:
        """Never admit a floating image tag instead of an OCI digest."""
        self.update_permit(signed_oci_ref="latest")
        with (
            self.synthetic_root(),
            self.assertRaisesRegex(QA.AdmissionDenied, "PINNED_IMAGE"),
        ):
            QA.check_permit(self.permit)

    def test_unknown_writer_protocol_refused(self) -> None:
        """Only explicit G/B/W participant protocol may enter gate."""
        self.update_permit(writer_protocol="disabled")
        with (
            self.synthetic_root(),
            self.assertRaisesRegex(QA.AdmissionDenied, "NOT_GATED_ONLY"),
        ):
            QA.check_permit(self.permit)

    def test_wrong_override_image_refused(self) -> None:
        """Permit digest must equal the pinned private override digest."""
        self.override.write_text(
            "services:\n  shelly-voltage:\n"
            "    image: " + self.image[:-1] + "b\n"
        )
        with (
            self.synthetic_root(),
            self.assertRaisesRegex(QA.AdmissionDenied, "NOT_EXACTLY"),
        ):
            QA.check_override(self.override, self.image)

    def test_multiple_image_lines_refused(self) -> None:
        """Duplicate pinned image directives cannot pass regex screening."""
        self.override.write_text(
            "image: " + self.image + "\nimage: " + self.image + "\n"
        )
        with (
            self.synthetic_root(),
            self.assertRaisesRegex(QA.AdmissionDenied, "NOT_EXACTLY"),
        ):
            QA.check_override(self.override, self.image)

    def test_valid_fake_G_B_W_admits_gated_check(self) -> None:
        """Allow only an invented permit and no maintenance G EX."""
        with self.synthetic_root():
            QA.check_gate(self.gate, self.override)

    def test_held_maintenance_G_EX_denies_entry(self) -> None:
        """Kernel flock makes a simultaneous G EX reject admission."""
        with (
            (self.gate / "G").open("rb") as maintainer,
            self.synthetic_root(),
        ):
            fcntl.flock(maintainer, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(
                QA.AdmissionDenied, "MAINTENANCE_G_EX_ACTIVE"
            ):
                QA.check_gate(self.gate, self.override)

    def test_missing_B_lock_refused(self) -> None:
        """No gate success unless B and W inodes are already provisioned."""
        (self.gate / "B").unlink()
        with (
            self.synthetic_root(),
            self.assertRaises(FileNotFoundError),
        ):
            QA.check_gate(self.gate, self.override)

    def test_symlinked_inode_refused(self) -> None:
        """Do not follow linked lock files to an attacker-controlled path."""
        path = self.gate / "W"
        path.unlink()
        path.symlink_to(self.gate / "G")
        with self.assertRaisesRegex(
            QA.AdmissionDenied, "UNTRUSTED_ADMISSION_FILE"
        ):
            QA.trusted_fd(path, 0o640)

    def test_invalid_gate_parent_refused(self) -> None:
        """A group-writable gate directory must never be trusted."""
        self.gate.chmod(0o770)
        with self.assertRaisesRegex(
            QA.AdmissionDenied, "UNTRUSTED_ADMISSION_DIRECTORY"
        ):
            QA.trusted_dir(self.gate)

    def test_invalid_lock_permission_refused(self) -> None:
        """A world-readable lock inode cannot pass the root-only check."""
        file = self.gate / "W"
        file.chmod(0o666)
        with self.assertRaises(QA.AdmissionDenied):
            QA.trusted_fd(file, 0o640)

    def test_cli_check_refusal_is_nonzero(self) -> None:
        """A rejected permit must fail closed and never claim approval."""
        with (
            patch.object(
                sys,
                "argv",
                [
                    "qa",
                    "--check",
                    "--gate-dir",
                    str(self.gate),
                    "--pinned-override",
                    str(self.override),
                ],
            ),
            patch.object(
                QA, "check_gate", side_effect=QA.AdmissionDenied("NO_PERMIT")
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 3)
        self.assertIn("COMPOSE_ADMISSION_DENIED", output.getvalue())

    def test_cli_success_is_not_release_approval(self) -> None:
        """Gated entry check must not pretend a whole release is approved."""
        with (
            patch.object(
                sys,
                "argv",
                [
                    "qa",
                    "--check",
                    "--gate-dir",
                    str(self.gate),
                    "--pinned-override",
                    str(self.override),
                ],
            ),
            patch.object(QA, "check_gate", return_value=None),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(QA.main(), 0)
        self.assertIn("NOT_RELEASE_OR_SIGNATURE_APPROVAL", output.getvalue())


if __name__ == "__main__":
    unittest.main()
