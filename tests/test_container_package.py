"""Prevent green source CI from publishing an unimportable Docker image."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def local_imports(path: Path, available: set[str]) -> set[str]:
    """Find local imports recursively without importing the application."""

    imports = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imports.update(name.name.split(".")[0] for name in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports & available


def reachable_modules() -> set[str]:
    """Compute the local import closure that a live container needs."""

    paths = {p.stem: p for p in ROOT.glob("*.py")}
    found = set()
    pending = ["app"]
    while pending:
        name = pending.pop()
        if name not in found:
            found.add(name)
            pending.extend(sorted(local_imports(paths[name], set(paths))))
    return found


class ContainerPackageTests(unittest.TestCase):
    """Verify source, Docker, release tarball and smoke-test alignment."""

    def test_docker_copies_every_app_local_module(self):
        """A newly imported root module cannot be omitted from the image."""

        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY *.py VERSION ./", text)
        ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertNotIn("*.py\n", ignored)
        self.assertGreaterEqual(len(reachable_modules()), 10)

    def test_ci_runs_image_imports_with_network_disabled(self):
        """The real image must import the app without credentials or HTTP."""

        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("docker build -t shelly-voltage-telegram:ci .", text)
        self.assertIn("docker run --rm --network none", text)
        self.assertIn("import app; import yasno_cache", text)
        self.assertIn("import telegram_channel_yasno", text)

    def test_release_sources_include_runtime_import_closure(self):
        """Reproducible archives cannot silently omit imported modules."""

        release = (ROOT / "scripts/release.sh").read_text(encoding="utf-8")
        fixture = (ROOT / "tests/test_release.sh").read_text(encoding="utf-8")
        for name in sorted(reachable_modules()):
            with self.subTest(module=name):
                self.assertIn(f"{name}.py", release)
                self.assertIn(f"{name}.py", fixture)

    def test_nonroot_container_user_remains_enforced(self):
        """Avoid running the Telegram bot as root while packaging new files."""

        text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("USER 10001:10001", text)
        self.assertIn("CMD [", text)


if __name__ == "__main__":
    unittest.main()
