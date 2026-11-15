"""Offline lifecycle tests for real Monitor.run shutdown and B/W release."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import importlib.util
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app.py"
SPEC = importlib.util.spec_from_file_location(
    "writer_fence_shutdown_candidate", ROOT / "writer_fence.py"
)
FENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FENCE
SPEC.loader.exec_module(FENCE)


def app_function(name: str, klass: str | None = None) -> ast.FunctionDef:
    """Extract exactly one function from the current candidate app source."""
    module = ast.parse(SOURCE.read_text())
    scope = module.body
    if klass is not None:
        scope = next(
            item.body
            for item in scope
            if isinstance(item, ast.ClassDef) and item.name == klass
        )
    return next(
        item
        for item in scope
        if isinstance(item, ast.FunctionDef) and item.name == name
    )


def compile_function(node: ast.FunctionDef, env: dict) -> object:
    """Execute only reviewed source function, not the real app imports."""
    module = ast.fix_missing_locations(
        ast.Module(body=[node], type_ignores=[])
    )
    exec(compile(module, str(SOURCE), "exec"), env)
    return env[node.name]


def lock_available(path: Path) -> bool:
    """Check separate open FD against the synthetic writer's inode."""
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


class ShutdownLifeTests(unittest.TestCase):
    """Check real Monitor.run with invented SQLite and no network."""

    def setUp(self) -> None:
        """Make only private invented B/W and WAL test fixtures."""
        tmp = tempfile.TemporaryDirectory(prefix="svt-shutdown-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.folder = self.root / "owner"
        self.folder.mkdir(mode=0o750)
        os.chmod(self.folder, 0o750)
        for name in ("B", "W"):
            item = self.folder / name
            item.touch(mode=0o640)
            os.chmod(item, 0o640)
        trusted = patch.object(FENCE, "_TRUSTED_UID", os.geteuid())
        trusted.start()
        self.addCleanup(trusted.stop)
        self.stop = threading.Event()

    def actual_run(self) -> object:
        """Compile only the real Monitor.run implementation."""
        return compile_function(
            app_function("run", "Monitor"),
            {
                "threading": threading,
                "time": time,
                "STOP": self.stop,
                "os": os,
                "logger": SimpleNamespace(critical=lambda *_args: None),
            },
        )

    def mock_monitor(
        self, done: threading.Event, events: list[str]
    ) -> SimpleNamespace:
        """Build one source-compatible fake monitor with invented callbacks."""
        db_path = self.root / "invented.sqlite3"
        begin = threading.Event()
        events.append("constructed")

        def commands() -> None:
            """Hold a transaction open until the test permits commit."""
            FENCE.assert_sqlite_writer_fenced()
            with contextlib.closing(sqlite3.connect(db_path)) as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE records(v INTEGER)")
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO records VALUES(42)")
                begin.set()
                if not done.wait(timeout=6):
                    raise RuntimeError("INVENTED_COMMIT_SIGNAL_TIMEOUT")
                db.commit()
                events.append("sqlite_committed")

        def poll() -> None:
            """Stop the main loop only after the worker opened its WAL txn."""
            if not begin.wait(timeout=4):
                raise RuntimeError("INVENTED_WORKER_NOT_READY")
            self.stop.set()
            events.append("stop_requested")

        return self.mock_stub(commands, poll, events)

    def mock_stub(
        self, commands: object, poll: object, events: list[str]
    ) -> SimpleNamespace:
        """Build only no-network callback and listener placeholders."""
        server = SimpleNamespace(
            shutdown=lambda: events.append("http_shutdown"),
            server_close=lambda: events.append("http_closed"),
        )
        return SimpleNamespace(
            health_server=server,
            telegram=SimpleNamespace(enabled=True),
            cfg=SimpleNamespace(poll_interval=0.01),
            initialize=lambda: events.append("initialized"),
            command_loop=commands,
            poll_cycle=poll,
            maybe_finalize_voltage_episode=lambda: None,
            maybe_rotate_stable_recovery_status=lambda: None,
            update_status=lambda: None,
            run_periodic_tasks=lambda: None,
        )

    def test_join_while_worker_transaction_is_open(self) -> None:
        """Both B and W stay held until SQLite worker exits."""
        release = threading.Event()
        events: list[str] = []
        monitor = self.mock_monitor(release, events)
        errors: list[BaseException] = []

        def runner() -> None:
            """Run exactly the real Monitor.run under the candidate lease."""
            try:
                with FENCE.writer_fence("required", self.folder):
                    self.actual_run()(monitor)
            except BaseException as exc:
                errors.append(exc)

        with patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}):
            thread = threading.Thread(target=runner)
            thread.start()
            try:
                deadline = time.monotonic() + 4
                while "http_closed" not in events:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.01)
                self.assertTrue(thread.is_alive())
                self.assertFalse(lock_available(self.folder / "B"))
                self.assertFalse(lock_available(self.folder / "W"))
            finally:
                release.set()
                thread.join(timeout=7)
        self.assert_join_result(events, errors, thread)

    def assert_join_result(
        self,
        events: list[str],
        errors: list[BaseException],
        thread: threading.Thread,
    ) -> None:
        """Require a committed WAL row before both lock releases."""
        self.assertFalse(errors)
        self.assertFalse(thread.is_alive())
        self.assertTrue(lock_available(self.folder / "B"))
        self.assertTrue(lock_available(self.folder / "W"))
        with contextlib.closing(
            sqlite3.connect(self.root / "invented.sqlite3")
        ) as db:
            self.assertEqual(
                db.execute("SELECT v FROM records").fetchone(), (42,)
            )
        self.assertLess(
            events.index("http_closed"), events.index("sqlite_committed")
        )

    def test_initialize_exception_sets_stop_and_releases(self) -> None:
        """An early monitor startup exception cannot strand the B/W owner."""
        events: list[str] = []
        monitor = SimpleNamespace(
            health_server=None,
            initialize=lambda: (_ for _ in ()).throw(RuntimeError("QA_INIT")),
            telegram=SimpleNamespace(enabled=False),
        )
        with FENCE.writer_fence("required", self.folder):
            with self.assertRaisesRegex(RuntimeError, "QA_INIT"):
                self.actual_run()(monitor)
            self.assertTrue(self.stop.is_set())
            self.assertFalse(lock_available(self.folder / "B"))
        self.assertTrue(lock_available(self.folder / "B"))
        self.assertEqual(events, [])

    def test_required_shutdown_failure_terminates_whole_process(self) -> None:
        """Never return a live writer lock after incomplete shutdown."""
        self.stop.set()

        def broken_shutdown() -> None:
            """Simulate failure to stop an active HTTP listener."""
            raise OSError("synthetic_http_shutdown_failed")

        monitor = SimpleNamespace(
            telegram=SimpleNamespace(enabled=False),
            initialize=lambda: None,
            health_server=SimpleNamespace(
                shutdown=broken_shutdown, server_close=lambda: None
            ),
        )
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            patch.object(os, "_exit", side_effect=SystemExit(70)) as abort,
            self.assertRaises(SystemExit) as raised,
            FENCE.writer_fence("required", self.folder),
        ):
            self.actual_run()(monitor)
        self.assertEqual(raised.exception.code, 70)
        abort.assert_called_once_with(70)

    def test_legacy_shutdown_exception_does_not_force_abort(self) -> None:
        """Do not introduce forced exits without explicit fence opt-in."""
        self.stop.set()

        def broken_shutdown() -> None:
            """Provide an entirely synthetic HTTP shutdown error."""
            raise OSError("synthetic_legacy_shutdown")

        monitor = SimpleNamespace(
            telegram=SimpleNamespace(enabled=False),
            initialize=lambda: None,
            health_server=SimpleNamespace(
                shutdown=broken_shutdown, server_close=lambda: None
            ),
        )
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": ""}),
            patch.object(os, "_exit") as abort,
            self.assertRaisesRegex(OSError, "synthetic_legacy_shutdown"),
        ):
            self.actual_run()(monitor)
        abort.assert_not_called()

    def test_shutdown_http_server_drains_request_workers(self) -> None:
        """The real HTTP creator must disable daemon request handling."""

        class FakeHTTP:
            """No network socket is opened by this fixture."""

            def __init__(self, *_args: object) -> None:
                self.daemon_threads = True
                self.block_on_close = False

            def serve_forever(self) -> None:
                """No real HTTP server starts."""
                return

        fake_thread = SimpleNamespace(start=lambda: None)

        def fake_thread_ctor(**_kwargs: object) -> object:
            """Return an inert server listener thread."""
            return fake_thread

        context = {
            "ThreadingHTTPServer": FakeHTTP,
            "health_handler": lambda _monitor: object(),
            "threading": SimpleNamespace(Thread=fake_thread_ctor),
        }
        server = compile_function(
            app_function("start_health_server"), context
        )(
            SimpleNamespace(
                cfg=SimpleNamespace(health_host="127.0.0.1", health_port=0)
            )
        )
        self.assertFalse(server.daemon_threads)
        self.assertTrue(server.block_on_close)

    def test_source_tracks_http_server_on_monitor(self) -> None:
        """Monitor startup retains exact returned server for later shutdown."""
        body = ast.unparse(app_function("initialize", "Monitor"))
        self.assertIn("self.health_server = start_health_server(self)", body)

    def test_source_stops_then_joins_non_daemon_thread(self) -> None:
        """A normal run cannot return before the command worker exits."""
        body = ast.unparse(app_function("run", "Monitor"))
        for expected in (
            "daemon=False",
            "STOP.set()",
            "health_server.shutdown()",
            "health_server.server_close()",
            "command_thread.join()",
        ):
            self.assertIn(expected, body)


if __name__ == "__main__":
    unittest.main()
