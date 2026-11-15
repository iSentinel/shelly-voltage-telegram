"""Test safe local online preparation backup creation."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/create-local-preparation-backups.py"
SPEC = importlib.util.spec_from_file_location("qa_local_prep_builder", SOURCE)
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)
IMAGE = "offline-qa@sha256:" + "a" * 64


class LocalPreparationBuilderTests(unittest.TestCase):
    """Create only invented local SQLite and never contact Docker or Shelly."""

    def setUp(self) -> None:
        """Create private temporary DB and empty local backup root."""
        temporary = tempfile.TemporaryDirectory(prefix="qa-prep-builder-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root / "archives"
        self.archive.mkdir(mode=0o700)
        self.db = self.root / "synthetic.sqlite3"
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE measurements(ts REAL, voltage REAL)")
            conn.executemany(
                "INSERT INTO measurements VALUES (?, ?)",
                [(float(x), 220.0 + x) for x in range(1, 15)],
            )
            conn.commit()

    def build(self, name: str = "release-prep-unit") -> dict:
        """Build only into the current test's private output root."""
        return BUILDER.build(self.db, self.archive, name, IMAGE)

    def cli(self, *args: str) -> int:
        """Test refusal/default paths without running integration."""
        with (
            patch.object(sys, "argv", ["qa", *args]),
            patch.object(BUILDER, "build", side_effect=AssertionError),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return BUILDER.main()

    def test_default_inert(self) -> None:
        """CLI without flags cannot create backup folders."""
        self.assertEqual(self.cli(), 0)
        self.assertEqual(list(self.archive.iterdir()), [])

    def test_missing_confirmation_refused(self) -> None:
        """A generic confirmation cannot enable local file mutations."""
        self.assertEqual(self.cli("--prepare", "--confirm", "yes"), 2)

    def test_missing_parameters_refused(self) -> None:
        """Required source/output paths may never be assumed."""
        self.assertEqual(
            self.cli("--prepare", "--confirm", BUILDER.CONFIRM), 2
        )

    def test_successful_twin_preparation(self) -> None:
        """Both standalone copies and their manifest pass immutable QA."""
        report = self.build()
        self.assertEqual(report["snapshot_A_rows"], 14)
        self.assertEqual(report["snapshot_B_rows"], 14)
        self.assertEqual(report["status"], "PASS_LOCAL_COPY_READ_ONLY")
        self.assertEqual(report["release_decision"], "NO_GO")

    def test_only_expected_private_files_remain(self) -> None:
        """Verify no WAL/SHM or restore scratch was generated."""
        self.build()
        root = self.archive / "release-prep-unit"
        self.assertEqual(
            {p.name for p in root.iterdir()},
            {
                "current-A.sqlite3",
                "current-B.sqlite3",
                "manifest.json",
            },
        )
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        for item in root.iterdir():
            self.assertEqual(item.stat().st_mode & 0o777, 0o600)

    def test_backup_contains_expected_unsigned_metadata(self) -> None:
        """Observed image ID is not authenticated by this builder."""
        self.build()
        path = self.archive / "release-prep-unit/manifest.json"
        data = json.loads(path.read_text())
        self.assertEqual(data["runtime_oci_ref_observed"], IMAGE)
        self.assertFalse(data["runtime_signature_reverified_in_this_run"])
        self.assertFalse(data["last_cutover_snapshot"])
        self.assertFalse(data["release_approval"])

    def test_existing_backup_never_overwritten(self) -> None:
        """Reject identical destination name without mutating source."""
        self.build()
        file = self.archive / "release-prep-unit/current-A.sqlite3"
        old_hash = hashlib.sha256(file.read_bytes()).hexdigest()
        with self.assertRaises(FileExistsError):
            self.build()
        self.assertEqual(
            hashlib.sha256(file.read_bytes()).hexdigest(), old_hash
        )

    def test_invalid_new_backup_name_refused(self) -> None:
        """Reject traversal, absolute and mislabelled directories."""
        with self.assertRaisesRegex(
            BUILDER.PreparationBlocked, "INVALID_NEW_BACKUP_NAME"
        ):
            self.build("../production")
        self.assertFalse((self.root / "production").exists())

    def test_invalid_image_digest_refused(self) -> None:
        """Never record a tag without a full observed digest."""
        with self.assertRaisesRegex(
            BUILDER.PreparationBlocked, "INVALID_OBSERVED_OCI_DIGEST"
        ):
            BUILDER.build(self.db, self.archive, "release-prep-unit", "latest")

    def test_missing_source_refused(self) -> None:
        """No creation from a missing or invented production DB path."""
        with self.assertRaisesRegex(
            BUILDER.PreparationBlocked, "SOURCE_NOT_REGULAR"
        ):
            BUILDER.build(
                self.root / "missing",
                self.archive,
                "release-prep-unit",
                IMAGE,
            )

    def test_symlink_source_refused(self) -> None:
        """Never follow a symlink masquerading as a SQLite source."""
        link = self.root / "linked.sqlite3"
        link.symlink_to(self.db)
        with self.assertRaisesRegex(
            BUILDER.PreparationBlocked, "SOURCE_NOT_REGULAR"
        ):
            BUILDER.build(link, self.archive, "release-prep-unit", IMAGE)

    def test_output_root_must_exist(self) -> None:
        """Do not create unknown parent directories implicitly."""
        with self.assertRaisesRegex(
            BUILDER.PreparationBlocked, "OUTPUT_DIRECTORY_UNSAFE"
        ):
            BUILDER.build(
                self.db, self.root / "missing", "release-prep-unit", IMAGE
            )

    def test_source_database_unchanged(self) -> None:
        """Only the two online copies and manifest may be written."""
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            original = conn.execute(
                "SELECT count(*), sum(voltage) FROM measurements"
            ).fetchone()
        self.build()
        with contextlib.closing(sqlite3.connect(self.db)) as conn:
            after = conn.execute(
                "SELECT count(*), sum(voltage) FROM measurements"
            ).fetchone()
        self.assertEqual(original, after)

    def test_cli_build_failure_is_nonzero(self) -> None:
        """A failed builder can never be presented as a release GO."""
        args = [
            "qa",
            "--prepare",
            "--confirm",
            BUILDER.CONFIRM,
            "--database",
            str(self.db),
            "--output-root",
            str(self.archive),
            "--name",
            "release-prep-unit",
            "--image",
            IMAGE,
        ]
        with (
            patch.object(sys, "argv", args),
            patch.object(
                BUILDER,
                "build",
                side_effect=BUILDER.PreparationBlocked("fake"),
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(BUILDER.main(), 3)
        self.assertIn("BACKUP_PREPARATION_BLOCKED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
