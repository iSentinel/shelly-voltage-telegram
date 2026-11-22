"""Run retained boot against disposable storage and mocked Docker."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "a" * 64
IMAGE = f"ghcr.io/isentinel/shelly-voltage-telegram@{DIGEST}"
CONTAINER = "b" * 64
LOCAL_IMAGE = "sha256:" + "c" * 64
TOKEN = "20261122T120000Z-0.5.26-hw"
RECOVERY_FIXTURE = """
    source "$1"
    UPDATE_LOCK="$STATE/updater.lock"
    select_cosign() { return 0; }
    verify_image() {
        echo verify >>"$TRACE"
        if [[ "$SIGNATURE" == changed ]]; then
            printf 'changed' >"$PENDING_FILE"
        fi
        [[ "$SIGNATURE" != invalid ]]
    }
    start_stack() { echo compose-start >>"$TRACE"; }
    run_recovery_command "$2"
"""


class RetainedBootTests(unittest.TestCase):
    """Protect the original selection while keeping current SQLite history."""

    def setUp(self):
        """Prepare matching receipts, an old archive and newer live data."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state = self.root / "state"
        self.snapshot = self.state / "snapshots" / TOKEN
        for folder in (self.state, self.snapshot.parent, self.snapshot):
            folder.mkdir(mode=0o700)
        self.marker = self.state / "pending-update"
        self.private_write(self.marker, TOKEN)
        self.write_receipts()
        self.db = self.root / "voltage.sqlite3"
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("CREATE TABLE measurements(id INTEGER PRIMARY KEY)")
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO measurements VALUES (1)")
            db.execute("INSERT INTO meta VALUES ('schema_version', '3')")
        self.archive = self.snapshot / "voltage-test.sqlite3.gz"
        self.write_archive(gzip.compress(self.db.read_bytes()))
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO measurements VALUES (2)")
        self.trace = self.root / "trace"
        self.details = self.root / "inspect"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.write_docker()
        self.write_inspection()

    def private_write(self, path: Path, text: str):
        """Create one private test input."""
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def write_receipts(self):
        """Match the source and snapshot to an immutable registry digest."""
        version = (ROOT / "VERSION").read_text().strip()
        for folder in (self.state, self.snapshot):
            self.private_write(
                folder / "deployed.json",
                json.dumps({"version": version, "digest": DIGEST}),
            )
            self.private_write(
                folder / "override.yaml",
                f"services:\n  shelly-voltage:\n    image: {IMAGE}\n",
            )
        self.private_write(self.snapshot / "container-id", CONTAINER)

    def write_archive(self, content: bytes):
        """Bind the archive checksum to its exact test pathname."""
        self.archive.write_bytes(content)
        self.archive.chmod(0o600)
        checksum = hashlib.sha256(content).hexdigest()
        self.private_write(
            self.archive.with_suffix(".gz.sha256"),
            f"{checksum}  {self.archive}\n",
        )

    def write_docker(self):
        """Replace Docker with commands limited to the test directory."""
        fake = self.bin / "docker"
        fake.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  inspect) if [ "$3" = "{{.State.Health.Status}}" ]; then\n'
            '    printf "%s\\n" "$HEALTH"; else cat "$DETAILS"; fi;;\n'
            f"  image) echo {LOCAL_IMAGE};;\n"
            '  start) printf "start %s\\n" "$2" >> "$TRACE";;\n'
            '  *) echo unexpected-docker >> "$TRACE"; exit 9;;\n'
            "esac\n"
        )
        fake.chmod(0o700)

    def write_inspection(self, container=CONTAINER, policy="no"):
        """Describe the original stopped container bound to the test DB."""
        mounts = json.dumps(
            [
                {
                    "Source": str(self.root),
                    "Destination": "/data",
                    "Type": "bind",
                    "RW": True,
                }
            ]
        )
        fields = (
            container,
            IMAGE,
            LOCAL_IMAGE,
            "exited",
            policy,
            mounts,
            "DATABASE_PATH=/data/voltage.sqlite3",
        )
        self.details.write_text("|".join(fields))

    def run_recovery(
        self, signature="ok", mode="--recover-and-start", health="healthy"
    ):
        """Use the real boot dispatcher with an isolated lock and signature."""
        env = dict(
            os.environ,
            SVT_ROOT=str(ROOT),
            SVT_UPDATE_STATE=str(self.state),
            DATABASE_PATH=str(self.db),
            SVT_PUBLIC_STATE=str(self.root / "public"),
            DETAILS=str(self.details),
            TRACE=str(self.trace),
            SIGNATURE=signature,
            HEALTH=health,
            PATH=f"{self.bin}:{os.environ['PATH']}",
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                RECOVERY_FIXTURE,
                "bash",
                str(ROOT / "scripts/apply-update.sh"),
                mode,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def test_stale_compatible_history_starts_only_original_container(self):
        """Keep both current records and the journal during startup."""
        before = self.db.stat().st_ino, self.db.read_bytes()
        receipt = (self.state / "deployed.json").read_bytes()
        result = self.run_recovery()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.trace.read_text().splitlines(),
            ["verify", f"start {CONTAINER}"],
        )
        self.assertEqual(self.marker.read_text(), TOKEN)
        self.assertEqual((self.db.stat().st_ino, self.db.read_bytes()), before)
        self.assertEqual((self.state / "deployed.json").read_bytes(), receipt)
        self.assertFalse((self.root / "public").exists())

    def test_manual_recovery_cannot_start_retained_container(self):
        """The exceptional startup is available only to the boot entry."""
        self.assertEqual(self.run_recovery(mode="--recover").returncode, 5)
        self.assertFalse(self.trace.exists())

    def test_corrupt_archive_does_not_start(self):
        """A matching checksum does not make corrupt gzip eligible."""
        self.write_archive(b"not a gzip archive")
        self.assertNotEqual(self.run_recovery().returncode, 0)
        self.assertFalse(self.trace.exists())

    def test_different_schema_does_not_start(self):
        """A structural difference must block use of the old image."""
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("ALTER TABLE measurements ADD COLUMN notes TEXT")
        self.assertNotEqual(self.run_recovery().returncode, 0)
        self.assertFalse(self.trace.exists())

    def test_pending_telegram_publication_does_not_start(self):
        """A restart cannot assume an ambiguous public send was cleared."""
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(
                "INSERT INTO meta VALUES (?, ?)",
                ("telegram_graphic_publish_pending", "send"),
            )
        self.assertNotEqual(self.run_recovery().returncode, 0)
        self.assertFalse(self.trace.exists())

    def test_recreated_container_with_same_digest_does_not_start(self):
        """Pin the container identity in addition to the image."""
        self.write_inspection(container="d" * 64)
        result = self.run_recovery()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONTAINER_REPLACED", result.stderr)
        self.assertFalse(self.trace.exists())

    def test_autonomous_docker_restart_is_rejected(self):
        """Only systemd may start the retained container after boot."""
        for policy in ("on-failure", "unless-stopped", "always"):
            with self.subTest(policy=policy):
                self.write_inspection(policy=policy)
                result = self.run_recovery()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("CONTAINER_STATE_MISMATCH", result.stderr)
                self.assertFalse(self.trace.exists())

    def test_invalid_signature_and_changed_journal_do_not_start(self):
        """Refuse the launch if any confirmation is missing or changes."""
        for mode in ("invalid", "changed"):
            with self.subTest(mode=mode):
                self.trace.unlink(missing_ok=True)
                result = self.run_recovery(signature=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(
                    self.trace.read_text().splitlines(), ["verify"]
                )

    def test_health_failure_keeps_recovery_pending(self):
        """A reported start is incomplete until Docker confirms health."""
        result = self.run_recovery(health="unhealthy")
        self.assertEqual(result.returncode, 2)
        self.assertIn("health could not be verified", result.stderr)
        self.assertEqual(self.marker.read_text(), TOKEN)
