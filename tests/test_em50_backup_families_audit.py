"""Verify archived backup lineage without relaxing strict release parity."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/em50-backup-families-audit.py"
SPEC = importlib.util.spec_from_file_location("backup_family_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class FamilyInventoryTests(unittest.TestCase):
    """Use only synthetic SQLite snapshots in a private disposable tree."""

    def setUp(self) -> None:
        """Create a complete, small valid SQLite schema and status marker."""

        temp = tempfile.TemporaryDirectory(prefix="svt-family-audit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "live.sqlite3"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.executescript(
                "CREATE TABLE measurements("
                "id INTEGER PRIMARY KEY,ts REAL,voltage REAL,"
                "frequency REAL,phase TEXT);"
                "CREATE TABLE outages("
                "id INTEGER PRIMARY KEY,started_at REAL,"
                "ended_at REAL,phase TEXT);"
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
                "INSERT INTO measurements VALUES(1,100,220,50,'L1');"
                "INSERT INTO measurements VALUES(2,110,221,50,'L1');"
                "INSERT INTO outages VALUES(1,80,90,'L1');"
                "INSERT INTO meta VALUES("
                "'telegram_status_message_id','private-fixture-id');"
            )
            conn.commit()
        self.now = time.time()

    def archive(self, family: str) -> Path:
        """Write a checksum-protected private historical archive."""

        folder = self.root / family
        folder.mkdir(mode=0o700)
        path = folder / "voltage-fixture.sqlite3.gz"
        with gzip.open(path, "wb") as output:
            output.write(self.db.read_bytes())
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        Path(str(path) + ".sha256").write_text(
            f"{sha}  {path}\n", encoding="utf-8"
        )
        return folder

    def audit(self, *families: str) -> dict:
        """Inspect family labels with the exact production parity helper."""

        pairs = AUDIT.parse_families(
            [f"{family}={self.root / family}" for family in families]
        )
        return AUDIT.inventory(self.db, pairs, 30, time.time())

    def test_equal_archive_integrity_does_not_authorize(self) -> None:
        """Even perfect source parity cannot approve a new release."""

        self.archive("cutover")
        record = self.audit("cutover")
        self.assertEqual(record["status"], "PASS_ARCHIVES_ONLY")
        self.assertEqual(record["families"][0]["current_parity"], "PASS")
        self.assertFalse(record["release_authorized"])
        self.assertFalse(record["fresh_writer_fenced_backup_created"])
        self.assertEqual(
            record["families"][0]["writer_fenced"],
            "NOT_ATTESTED_BY_INVENTORY",
        )

    def test_archive_valid_but_new_records_make_parity_fail(self) -> None:
        """Historical archive integrity must be separate from freshness."""

        for family in ("periodic", "cutover", "updater", "source_upgrade"):
            self.archive(family)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO measurements VALUES(3,120,222,50,'L1')")
            conn.commit()
        record = self.audit("periodic", "cutover", "updater", "source_upgrade")
        self.assertEqual(record["status"], "PASS_ARCHIVES_ONLY")
        self.assertEqual(record["live_measurements"], 3)
        self.assertEqual(record["periodic_predeploy_parity"], "FAIL")
        for family in record["families"]:
            self.assertEqual(family["record_gap"], 1)
            self.assertEqual(family["current_parity"], "FAIL")
            self.assertIn(
                "measurements_not_equal", family["parity_reason_codes"]
            )

    def test_stale_archive_is_valid_but_not_current(self) -> None:
        """Old clock time is a parity failure, not a corrupt backup."""

        folder = self.archive("periodic")
        file = next(folder.glob("*.sqlite3.gz"))
        old = self.now - 31 * 60
        os.utime(file, (old, old))
        result = self.audit("periodic")
        self.assertEqual(result["status"], "PASS_ARCHIVES_ONLY")
        self.assertEqual(result["periodic_predeploy_parity"], "FAIL")
        self.assertIn(
            "backup_stale", result["families"][0]["parity_reason_codes"]
        )

    def test_corrupted_checksum_rejected(self) -> None:
        """Never report archive integrity PASS after a byte corruption."""

        folder = self.archive("updater")
        file = next(folder.glob("*.sqlite3.gz"))
        with file.open("ab") as writer:
            writer.write(b"untrusted")
        record = self.audit("updater")
        self.assertEqual(record["status"], "FAIL_ARCHIVE_INTEGRITY")
        self.assertEqual(record["families"][0]["status"], "ARCHIVE_INVALID")
        self.assertFalse(record["release_authorized"])

    def test_symlink_and_duplicate_labels_rejected(self) -> None:
        """No linked family folder or ambiguous family mapping is allowed."""

        self.archive("periodic")
        (self.root / "linked").symlink_to(self.root / "periodic")
        with self.assertRaises(ValueError):
            AUDIT.parse_families([f"periodic={self.root / 'linked'}"])
        with self.assertRaises(ValueError):
            AUDIT.parse_families(
                [
                    f"periodic={self.root / 'periodic'}",
                    f"periodic={self.root / 'periodic'}",
                ]
            )

    def test_unknown_family_and_bad_age_rejected(self) -> None:
        """Prevent synthetic offhost or weak time limits."""

        self.archive("cutover")
        with self.assertRaises(ValueError):
            AUDIT.parse_families([f"offhost={self.root / 'cutover'}"])
        with self.assertRaises(ValueError):
            AUDIT.inventory(
                self.db,
                [("cutover", self.root / "cutover")],
                10000,
                self.now,
            )

    def test_output_does_not_disclose_telegram_status_id(self) -> None:
        """Only expose a boolean for preserved private status metadata."""

        self.archive("periodic")
        output = json.dumps(self.audit("periodic"))
        self.assertNotIn("private-fixture-id", output)
        self.assertIn("saved_status_metadata_present", output)
        self.assertIn('"release_authorized": false', output)

    def test_cli_corrupt_archive_exits_nonzero(self) -> None:
        """CLI error is stable; never grant deployment on invalid archive."""

        folder = self.archive("cutover")
        next(folder.glob("*.sha256")).write_text("invalid\n")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--db",
                str(self.db),
                "--snapshot",
                f"cutover={folder}",
                "--jsonl",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["status"], "FAIL_ARCHIVE_INTEGRITY")
        self.assertFalse(parsed["release_authorized"])


if __name__ == "__main__":
    unittest.main()
