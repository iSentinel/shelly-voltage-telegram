"""Source-level and executable startup-order checks for guarded app.py."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app.py"
SPEC = importlib.util.spec_from_file_location(
    "writer_fence_app_contract", ROOT / "writer_fence.py"
)
FENCE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FENCE
SPEC.loader.exec_module(FENCE)


def find_function(name: str, container: ast.AST) -> ast.FunctionDef:
    """Return the exact source function from module or class body."""
    for node in container.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("source_function_not_found_" + name)


def extracted_function(
    node: ast.FunctionDef, namespace: dict[str, object]
) -> object:
    """Compile only the reviewed function, without device imports."""
    unit = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(unit, str(SOURCE), "exec"), namespace)
    return namespace[node.name]


class AppFenceIntegrationTests(unittest.TestCase):
    """Never import the full app, open live data or contact devices."""

    def setUp(self) -> None:
        """Make a private synthetic root with safe B/W inodes."""
        temp = tempfile.TemporaryDirectory(
            prefix="app-fence-unit-", dir="/var/tmp"
        )
        self.addCleanup(temp.cleanup)
        owner_patch = patch.object(FENCE, "_TRUSTED_UID", os.geteuid())
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        self.root = Path(temp.name)
        self.folder = self.root / "owner"
        self.folder.mkdir(mode=488)
        os.chmod(self.folder, 488)
        for name in ("B", "W"):
            item = self.folder / name
            item.touch(mode=416)
            os.chmod(item, 416)
        self.tree = ast.parse(SOURCE.read_text())

    def storage_connect(self) -> object:
        """Extract Storage.connect with invented SQLite dependencies."""
        klass = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Storage"
        )
        function = find_function("connect", klass)
        return extracted_function(
            function,
            {
                "sqlite3": sqlite3,
                "assert_sqlite_writer_fenced": (
                    FENCE.assert_sqlite_writer_fenced
                ),
            },
        )

    def main_entrypoint(self, mode: str, events: list[str]) -> object:
        """Extract current real main() with mock devices and test root."""

        def fence_scope(value: str) -> object:
            events.append("fence_enter")
            return FENCE.writer_fence(value, self.folder)

        def monitor(_config: object) -> object:
            events.append("monitor_constructed")
            return SimpleNamespace(run=lambda: events.append("monitor_run"))

        env = {
            "parser": lambda: SimpleNamespace(
                parse_args=lambda: SimpleNamespace(command="run")
            ),
            "load_config": lambda: SimpleNamespace(),
            "logger": SimpleNamespace(
                remove=lambda: None, add=lambda *_args, **_kwargs: None
            ),
            "os": os,
            "install_signals": lambda: events.append("signals_installed"),
            "writer_fence": fence_scope,
            "Monitor": monitor,
        }
        return extracted_function(find_function("main", self.tree), env)

    def test_app_imports_exact_owner_fence(self) -> None:
        """The production entrypoint imports both source guard hooks."""
        imports = [
            node
            for node in self.tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module == "writer_fence"
        ]
        self.assertEqual(len(imports), 1)
        self.assertEqual(
            {alias.name for alias in imports[0].names},
            {"writer_fence", "assert_sqlite_writer_fenced"},
        )

    def test_storage_guard_precedes_sqlite_open(self) -> None:
        """The real Storage.connect AST validates before any SQLite connect."""
        klass = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "Storage"
        )
        method = find_function("connect", klass)
        calls = [
            node.func
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
        ]
        validate = next(
            node
            for node in calls
            if isinstance(node, ast.Name)
            and node.id == "assert_sqlite_writer_fenced"
        )
        sqlite_open = next(
            node
            for node in calls
            if isinstance(node, ast.Attribute) and node.attr == "connect"
        )
        self.assertLess(validate.lineno, sqlite_open.lineno)

    def test_required_without_lease_denies_before_db_creation(self) -> None:
        """The real Storage.connect function cannot open unfenced SQLite."""
        connect = self.storage_connect()
        path = self.root / "no-write.sqlite3"
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            self.assertRaisesRegex(
                FENCE.WriterFenceError, "missing_at_database_open"
            ),
        ):
            connect(SimpleNamespace(path=path))
        self.assertFalse(path.exists())

    def test_required_lease_permits_synthetic_sqlite(self) -> None:
        """The actual extracted method opens SQLite only under B/W."""
        connect = self.storage_connect()
        path = self.root / "allowed.sqlite3"
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
            contextlib.closing(connect(SimpleNamespace(path=path))) as db,
        ):
            self.assertEqual(
                db.execute("PRAGMA journal_mode").fetchone()[0], "wal"
            )
        self.assertTrue(path.is_file())

    def test_B_maintenance_exclusion_blocks_SQLite(self) -> None:
        """A held maintenance B EX denies the real connect function."""
        connect = self.storage_connect()
        path = self.root / "blocked.sqlite3"
        with (self.folder / "B").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
                self.assertRaises(FENCE.WriterFenceError),
                FENCE.writer_fence("required", self.folder),
            ):
                connect(SimpleNamespace(path=path))
        self.assertFalse(path.exists())

    def test_inode_swap_blocks_future_SQLite(self) -> None:
        """After B path replacement a new SQLite file must not be created."""
        connect = self.storage_connect()
        path = self.root / "not-created.sqlite3"
        with (
            patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
            FENCE.writer_fence("required", self.folder),
        ):
            replacement = self.folder / "B.new"
            replacement.touch(mode=416)
            os.chmod(replacement, 416)
            os.replace(replacement, self.folder / "B")
            with self.assertRaisesRegex(
                FENCE.WriterFenceError, "inode_replaced"
            ):
                connect(SimpleNamespace(path=path))
        self.assertFalse(path.exists())

    def test_main_run_acquires_fence_before_monitor(self) -> None:
        """Real main body places both signal and Monitor under owner fence."""
        events: list[str] = []
        entrypoint = self.main_entrypoint("required", events)
        with patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}):
            self.assertEqual(entrypoint(), 0)
        self.assertEqual(
            events,
            [
                "fence_enter",
                "signals_installed",
                "monitor_constructed",
                "monitor_run",
            ],
        )

    def test_main_maintenance_denied_before_monitor(self) -> None:
        """A held B EX prevents the first Monitor object from appearing."""
        events: list[str] = []
        entrypoint = self.main_entrypoint("required", events)
        with (self.folder / "B").open("rb") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with (
                patch.dict(os.environ, {"SHELLY_WRITER_FENCE": "required"}),
                self.assertRaises(FENCE.WriterFenceError),
            ):
                entrypoint()
        self.assertEqual(events, ["fence_enter"])

    def test_main_default_off_preserves_legacy_start(self) -> None:
        """No env flag retains the existing functional entrypoint behavior."""
        events: list[str] = []
        entrypoint = self.main_entrypoint("", events)
        with patch.dict(os.environ, {"SHELLY_WRITER_FENCE": ""}):
            self.assertEqual(entrypoint(), 0)
        self.assertIn("monitor_run", events)


if __name__ == "__main__":
    unittest.main()
