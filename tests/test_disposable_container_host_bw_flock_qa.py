"""Default-inert unit checks for isolated Docker-host B/W flock QA."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/disposable-container-host-bw-flock-qa.py"
)
SPEC = importlib.util.spec_from_file_location("qa_docker_bw", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QA)


def reply(code: int = 0, value: str = "") -> SimpleNamespace:
    """Return invented subprocess status."""
    return SimpleNamespace(returncode=code, stdout=value, stderr="test")


class ContainerBWTests(unittest.TestCase):
    """Never start a Docker container as part of a unit test."""

    def setUp(self) -> None:
        """Create private temporary source directories only."""
        temp = tempfile.TemporaryDirectory(prefix="docker-bw-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def invoke(self, *args: str) -> int:
        """Execute default-inert CLI while suppressing output."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_noop(self) -> None:
        """No flags must never inspect or mutate Docker."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            self.assertEqual(self.invoke(), 0)

    def test_generic_confirmation_denied(self) -> None:
        """Exact consent must be required before any real QA."""
        with patch.object(QA, "guest_guard", side_effect=AssertionError):
            self.assertEqual(
                self.invoke("--run-disposable", "--confirm", "yes"), 2
            )

    def test_nonroot_denied(self) -> None:
        """Wrong privileges must fail before checking Docker."""
        with (
            patch.object(QA.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(RuntimeError, "NOT_EXACT_OFFLINE_QA"),
        ):
            QA.guest_guard()

    def test_wrong_host_denied(self) -> None:
        """Production hostname is never a permitted QA destination."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(QA.socket, "gethostname", return_value="production"),
            self.assertRaisesRegex(RuntimeError, "NOT_EXACT_OFFLINE_QA"),
        ):
            QA.guest_guard()

    def test_networked_guest_denied(self) -> None:
        """An external interface must stop this sandbox experiment."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(
                QA.socket,
                "if_nameindex",
                return_value=[(1, "lo"), (2, "eth0")],
            ),
            self.assertRaisesRegex(RuntimeError, "EXTERNAL_NETWORK"),
        ):
            QA.guest_guard()

    def test_running_signed_qa_writer_denied(self) -> None:
        """A running original QA writer must always block new experiments."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(QA, "call", return_value=reply(0, "true|no")),
            self.assertRaisesRegex(RuntimeError, "ORIGINAL_QA_WRITER"),
        ):
            QA.guest_guard()

    def test_immutable_image_mismatch_denied(self) -> None:
        """Unknown cached image cannot be used in the QA guest."""
        with (
            patch.object(QA.os, "geteuid", return_value=0),
            patch.object(
                QA.socket,
                "gethostname",
                return_value="shelly-docker-reboot-qa",
            ),
            patch.object(QA.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.object(
                QA,
                "call",
                side_effect=[reply(0, "false|no"), reply(0, "sha256:other")],
            ),
            self.assertRaisesRegex(RuntimeError, "IMAGE_ID_MISMATCH"),
        ):
            QA.guest_guard()

    def test_mount_permissions_are_group_only(self) -> None:
        """Container group may access B/W without writable lock data."""
        with patch.object(QA.os, "chown"):
            QA.prepare(self.root)
        self.assertEqual(
            stat.S_IMODE((self.root / "locks").stat().st_mode), 0o750
        )
        self.assertEqual(
            stat.S_IMODE((self.root / "events").stat().st_mode), 0o770
        )
        for name in ("B", "W"):
            self.assertEqual(
                stat.S_IMODE((self.root / "locks" / name).stat().st_mode),
                0o640,
            )

    def test_docker_sandbox_flags(self) -> None:
        """No external network, restarts or elevated privileges."""
        args = QA.docker_args(self.root, "svt-qa-bw-test", "first")
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertEqual(args[args.index("--restart") + 1], "no")
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop", args)
        self.assertIn("no-new-privileges", args)
        self.assertIn(QA.IMAGE, args)
        self.assertIn("org.svt.qa.lockowner=svt-qa-bw-test", args)

    def test_docker_mounts_are_qa_only(self) -> None:
        """No production paths or secrets are ever bind-mounted."""
        args = QA.docker_args(self.root, "svt-qa-bw-test", "first")
        mounts = [
            args[index + 1]
            for index, item in enumerate(args)
            if item == "--mount"
        ]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(all(str(self.root) in item for item in mounts))
        self.assertFalse(any("shelly-voltage/" in item for item in mounts))

    def test_worker_uses_two_flocks_no_sqlite(self) -> None:
        """Injected code only holds two locks and emits markers."""
        compile(QA.WORKER, "synthetic-qa-worker", "exec")
        self.assertIn('Path("/locks/B")', QA.WORKER)
        self.assertIn('Path("/locks/W")', QA.WORKER)
        self.assertIn("LOCK_SH", QA.WORKER)
        self.assertIn("LOCK_EX", QA.WORKER)
        self.assertNotIn("sqlite", QA.WORKER.lower())
        self.assertNotIn("telegram", QA.WORKER.lower())

    def test_exclusive_probe_denied_by_B_sh(self) -> None:
        """Host flock respects an independently held shared fd."""
        target = self.root / "B"
        target.touch(mode=0o600)
        self.assertTrue(QA.can_get_exclusive(target))
        with target.open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.can_get_exclusive(target))

    def test_foreign_container_never_removed(self) -> None:
        """Refuse to modify objects without exact QA owner label."""
        with (
            patch.object(
                QA,
                "call",
                return_value=reply(
                    0, json.dumps({"org.svt.qa.lockowner": "foreign"})
                ),
            ) as command,
            self.assertRaisesRegex(RuntimeError, "UNKNOWN_CONTAINER_OWNER"),
        ):
            QA.remove_owned("svt-qa-bw-test")
        self.assertEqual(command.call_count, 1)

    def test_unknown_container_is_noop(self) -> None:
        """A failed Docker start cannot trigger unrelated cleanup."""
        with patch.object(QA, "call", return_value=reply(1)) as command:
            QA.remove_owned("missing-qa")
        self.assertEqual(command.call_count, 1)

    def test_kill_requires_verified_label(self) -> None:
        """Never send SIGKILL without checking exact sandbox identity."""
        with (
            patch.object(QA, "verify_ownership", return_value=False),
            patch.object(QA, "call") as command,
            self.assertRaisesRegex(RuntimeError, "NOT_FOUND_FOR_KILL"),
        ):
            QA.crash_owned("unowned")
        command.assert_not_called()

    def test_cleanup_disables_restart_before_remove(self) -> None:
        """Owned disposable Docker policy is disabled before deletion."""
        with (
            patch.object(QA, "verify_ownership", return_value=True),
            patch.object(QA, "call", return_value=reply()) as command,
        ):
            QA.remove_owned("svt-qa-bw-test")
        self.assertEqual(command.call_count, 2)
        self.assertIn("--restart=no", command.call_args_list[0].args[0])
        self.assertEqual(command.call_args_list[1].args[0][1], "rm")


if __name__ == "__main__":
    unittest.main()
