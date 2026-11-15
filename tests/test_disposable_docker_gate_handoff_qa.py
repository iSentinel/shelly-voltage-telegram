"""Default-inert unit tests for real disposable Docker G/B/W/A handoff."""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/disposable-docker-gate-handoff-qa.py"
SPEC = importlib.util.spec_from_file_location("qa_real_docker_g", SCRIPT)
QA = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = QA
SPEC.loader.exec_module(QA)


def result(code: int = 0, output: str = "") -> SimpleNamespace:
    """Return a mocked subprocess result without executing Docker."""
    return SimpleNamespace(returncode=code, stdout=output)


class DockerAdmissionGateTests(unittest.TestCase):
    """No unit test may launch Docker or access the running Shelly app."""

    def setUp(self) -> None:
        """Build private synthetic B/W/G/A lock and marker paths."""
        temp = tempfile.TemporaryDirectory(prefix="docker-g-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def invoke(self, *args: str) -> int:
        """Run default-inert CLI with every real guest guard forbidden."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(QA, "load_qa", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return QA.main()

    def test_default_is_inert(self) -> None:
        """No flags must not inspect Docker or create any files."""
        self.assertEqual(self.invoke(), 0)

    def test_generic_consent_denied(self) -> None:
        """Only exact offline LXC114 confirmation permits Docker QA."""
        self.assertEqual(
            self.invoke("--run-disposable", "--confirm", "yes"), 2
        )

    def test_guard_points_to_sibling_offline_module(self) -> None:
        """The script must use only the fixed QA guest and owner guard."""
        self.assertEqual(QA.GUARD.parent, SCRIPT.parent)
        self.assertTrue(QA.GUARD.is_file())

    def test_child_source_uses_G_then_B_then_W(self) -> None:
        """Synthetic nonroot container locks G SH before B SH and W EX."""
        compile(QA.CHILD, "offline-docker-g-actor", "exec")
        self.assertLess(
            QA.CHILD.index('Path("/locks/G")'),
            QA.CHILD.index('Path("/locks/B")'),
        )
        self.assertLess(
            QA.CHILD.index('Path("/locks/B")'),
            QA.CHILD.index('Path("/locks/W")'),
        )
        self.assertIn("LOCK_SH", QA.CHILD)
        self.assertIn("LOCK_EX", QA.CHILD)
        self.assertIn("flock(g, fcntl.LOCK_UN)", QA.CHILD)

    def test_child_pid1_has_SIGTERM_handler(self) -> None:
        """A PID1 Docker Python process cannot ignore graceful shutdown."""
        self.assertIn("signal.signal(signal.SIGTERM", QA.CHILD)
        self.assertIn("stop_requested", QA.CHILD)
        self.assertIn("time.monotonic()", QA.CHILD)

    def test_docker_args_are_nonroot_offline(self) -> None:
        """QA must use cached image and no network or auto-restart."""
        helper = SimpleNamespace(
            base_flags=lambda name: [
                "docker",
                "run",
                "--network",
                "none",
                "--restart",
                "no",
                "--user",
                "10001:10001",
                "--label",
                "org.svt.qa.fence=" + name,
            ],
            sandbox_mounts=lambda root: [
                "--mount",
                "src=" + str(root) + ",dst=/locks",
            ],
            IMAGE="sha256:qa-fixed",
        )
        args = QA.args(self.root, "qa-only", "role", helper)
        self.assertIn("none", args)
        self.assertIn("no", args)
        self.assertIn("10001:10001", args)
        self.assertIn("sha256:qa-fixed", args)
        self.assertEqual(args[-1], "role")

    def test_host_exclusive_lock_probe(self) -> None:
        """A separate B EX probe fails when an independent B SH is held."""
        B = self.root / "B"
        B.touch(mode=0o600)
        self.assertTrue(QA.probe_exclusive(B))
        with B.open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertFalse(QA.probe_exclusive(B))

    def test_denied_G_does_not_produce_sqlite(self) -> None:
        """An admission denial is unrelated to production SQLite."""
        text = QA.CHILD
        self.assertLess(
            text.index('mark("DENIED_G")'), text.index("sqlite3.connect")
        )
        self.assertIn("raise SystemExit(20)", text)

    def test_missing_marker_is_failure(self) -> None:
        """No container handshake can be treated as proof."""
        with (
            patch.object(QA.time, "monotonic", side_effect=[100.0, 200.0]),
            self.assertRaisesRegex(RuntimeError, "MISSING_MARKER"),
        ):
            QA.expect(self.root, "never", "RUNNING")

    def test_wrong_marker_fails_closed(self) -> None:
        """A denied worker cannot be mistaken for a live writer."""
        events = self.root / "events"
        events.mkdir()
        (events / "role.status").write_text("DENIED_G")
        with self.assertRaisesRegex(RuntimeError, "BAD_MARKER"):
            QA.expect(self.root, "role", "RUNNING")

    def test_start_failure_is_propagated(self) -> None:
        """Never count a failed Docker run as an admitted writer."""
        helper = SimpleNamespace(
            command=lambda args, timeout: result(1),
            base_flags=lambda name: ["docker", "run", name],
            sandbox_mounts=lambda root: [],
            IMAGE="sha256:fixed",
        )
        with self.assertRaisesRegex(RuntimeError, "START_FAILED"):
            QA.start(self.root, "qa-only", "role", helper)

    def test_stop_refuses_unowned_docker_object(self) -> None:
        """Only the exact disposable label allows SIGTERM."""
        helper = SimpleNamespace(owner_label=lambda name: False)
        with self.assertRaisesRegex(RuntimeError, "UNOWNED"):
            QA.stop("not-qa", helper)

    def test_cleanup_order_targets_only_three_owned_containers(self) -> None:
        """A simulated partial run must try to clean all owned test roles."""
        recorded: list[str] = []
        helper = SimpleNamespace(
            base_flags=lambda name: ["docker", "run", name],
            sandbox_mounts=lambda root: [],
            IMAGE="sha256:fixed",
            command=lambda args, timeout: result(1),
            remove_owned=lambda name: recorded.append(name),
        )
        with self.assertRaisesRegex(RuntimeError, "START_FAILED"):
            QA.run(self.root, helper)
        self.assertEqual(len(recorded), 3)
        self.assertTrue(
            all(name.startswith("svt-qa-fence-g-") for name in recorded)
        )

    def test_sqlite_fixture_is_synthetic_only(self) -> None:
        """The embedded worker must open only invented mounted SQLite."""
        self.assertIn("/synthetic/qa.sqlite3", QA.CHILD)
        self.assertNotIn("/var/lib/shelly-voltage", QA.CHILD)
        self.assertNotIn("telegram", QA.CHILD.lower())


if __name__ == "__main__":
    unittest.main()
