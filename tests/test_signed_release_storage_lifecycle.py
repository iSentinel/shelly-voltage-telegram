"""Exercise the actual release Storage on synthetic history only."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

RELEASE = Path(
    os.environ.get(
        "SVT_APP_PATH", Path(__file__).resolve().parents[1] / "app.py"
    )
)
SPEC = importlib.util.spec_from_file_location(
    "svt_storage_qa_release", RELEASE
)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("release source unavailable")
APP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = APP
SPEC.loader.exec_module(APP)

SYNTHETIC_SIZE = 65000
BASE_TIME = 1700000000.0


def rows_and_digest(path: Path) -> tuple[int, str]:
    """Hash a consistent full logical SQLite snapshot, including WAL."""
    with closing(
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        count = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
        digest = hashlib.sha256()
        for statement in db.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
        return count, digest.hexdigest()


class SignedReleaseStorageLifecycleTests(unittest.TestCase):
    """No real Shelly network, configuration or production database access."""

    def setUp(self) -> None:
        """Allocate private scratch and reject outbound socket connections."""
        temp = tempfile.TemporaryDirectory(
            prefix="svt-signed-storage-qa-", dir="/tmp"
        )
        self.addCleanup(temp.cleanup)
        network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("QA network connection prohibited"),
        )
        network.start()
        self.addCleanup(network.stop)
        self.db = Path(temp.name) / "synthetic.sqlite3"
        self.storage = APP.Storage(self.db, "Europe/Kyiv")
        self.assertEqual(APP.SCHEMA_VERSION, 3)

    def seed_v3(self, count: int) -> None:
        """Write a realistic but entirely invented multiday v3 sample set."""
        self.storage.initialize()
        with closing(sqlite3.connect(self.db)) as db:
            db.executemany(
                "INSERT INTO measurements(ts,voltage,frequency,phase)"
                " VALUES(?,?,?,?)",
                (
                    (BASE_TIME + i * 30, 211 + i % 28, 50, "L1")
                    for i in range(count)
                ),
            )
            db.execute(
                "INSERT INTO outages(started_at,ended_at,phase) VALUES(?,?,?)",
                (BASE_TIME + 100, BASE_TIME + 500, "L1"),
            )
            db.execute(
                "INSERT INTO meta(key,value) VALUES('qa_origin','synthetic')"
            )
            db.commit()

    def test_65000_records_survive_two_real_initializations(self) -> None:
        """Repeated v3 initialization must never discard current history."""
        self.seed_v3(SYNTHETIC_SIZE)
        before = rows_and_digest(self.db)
        self.storage.initialize()
        self.assertEqual(rows_and_digest(self.db), before)
        self.storage.initialize()
        self.assertEqual(rows_and_digest(self.db), before)
        self.assertEqual(self.storage.get_meta("schema_version"), "3")
        self.assertEqual(self.storage.get_meta("qa_origin"), "synthetic")

    def test_live_release_storage_adds_without_replacing_history(self) -> None:
        """The signed release's writer API preserves old rows and outages."""
        self.seed_v3(SYNTHETIC_SIZE)
        self.storage.initialize()
        self.storage.add(BASE_TIME + SYNTHETIC_SIZE * 30, 225.4, 50, "L1")
        with closing(sqlite3.connect(self.db)) as db:
            old = db.execute(
                "SELECT voltage FROM measurements WHERE id=1"
            ).fetchone()
            new = db.execute(
                "SELECT voltage FROM measurements ORDER BY id DESC LIMIT 1"
            ).fetchone()
            outages = db.execute("SELECT COUNT(*) FROM outages").fetchone()[0]
            count = db.execute("SELECT COUNT(*) FROM measurements").fetchone()[
                0
            ]
        self.assertEqual(
            (count, old, new, outages), (65001, (211.0,), (225.4,), 1)
        )
        self.assertEqual(self.storage.get_meta("schema_version"), "3")

    def test_future_schema_rejects_without_logical_rewind(self) -> None:
        """A v4 database must fail before modifying v3 release metadata."""
        self.seed_v3(120)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
            db.commit()
        before = rows_and_digest(self.db)
        with self.assertRaisesRegex(RuntimeError, "newer than 3"):
            self.storage.initialize()
        self.assertEqual(rows_and_digest(self.db), before)
        self.assertEqual(self.storage.get_meta("schema_version"), "4")

    def test_v2_migration_preserves_old_outage_and_sample(self) -> None:
        """Actual release migration retains L1 samples and outage edges."""
        with closing(sqlite3.connect(self.db)) as db:
            db.executescript(
                "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);"
                "INSERT INTO meta VALUES('schema_version','2');"
                "CREATE TABLE measurements(id INTEGER PRIMARY KEY,"
                "ts REAL,voltage REAL,frequency REAL);"
                "INSERT INTO measurements VALUES(1,1000,219.5,50);"
                "CREATE TABLE outages(id INTEGER PRIMARY KEY,"
                "started_at REAL,ended_at REAL);"
                "INSERT INTO outages VALUES(1,1001,1002);"
            )
        self.storage.initialize()
        with closing(sqlite3.connect(self.db)) as db:
            sample = db.execute(
                "SELECT ts,voltage,frequency,phase FROM measurements"
            ).fetchone()
            outage = db.execute(
                "SELECT started_at,ended_at,phase FROM outages"
            ).fetchone()
        self.assertEqual(sample, (1000, 219.5, 50, "L1"))
        self.assertEqual(outage, (1001, 1002, "L1"))
        self.assertEqual(self.storage.get_meta("schema_version"), "3")

    def crashed_writer(self) -> None:
        """SIGKILL a disposable SQLite writer with one uncommitted row."""
        ready = self.db.with_suffix(".writer-ready")
        child = (
            "import pathlib,sqlite3,time,sys;"
            "c=sqlite3.connect(sys.argv[1]);"
            "c.execute('PRAGMA journal_mode=WAL');"
            "c.execute('PRAGMA wal_autocheckpoint=0');"
            "c.execute('INSERT INTO measurements(ts,voltage,frequency,phase)'"
            " ' VALUES(1900000000,226.3,50,\\'L1\\')');"
            "c.commit();"
            "c.execute('INSERT INTO measurements(ts,voltage,frequency,phase)'"
            " ' VALUES(1900000030,201.1,50,\\'L1\\')');"
            "pathlib.Path(sys.argv[2]).write_text('committed');"
            "time.sleep(30)"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", child, str(self.db), str(ready)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
                if ready.exists() or proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.assertTrue(ready.exists(), "child writer failed")
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=4)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_sigkill_wal_then_reopen_with_real_release_storage(self) -> None:
        """Only committed WAL data survives and release can append safely."""
        self.seed_v3(120)
        self.crashed_writer()
        self.storage.initialize()
        with closing(sqlite3.connect(self.db)) as db:
            counts = db.execute(
                "SELECT voltage,COUNT(*) FROM measurements "
                "WHERE ts>=1900000000 GROUP BY voltage"
            ).fetchall()
            check = db.execute("PRAGMA quick_check").fetchone()[0]
        self.assertEqual(counts, [(226.3, 1)])
        self.assertEqual(check, "ok")
        self.storage.add(1900000060, 223.7, 50, "L1")
        with closing(sqlite3.connect(self.db)) as db:
            rows = db.execute(
                "SELECT voltage FROM measurements "
                "WHERE ts>=1900000000 ORDER BY ts"
            ).fetchall()
        self.assertEqual(rows, [(226.3,), (223.7,)])
        self.assertEqual(rows_and_digest(self.db)[0], 122)

    def test_release_storage_does_not_touch_network(self) -> None:
        """Starting Storage and inserting samples never connects to API."""
        self.storage.initialize()
        self.storage.add(BASE_TIME, 220, 50)
        self.assertEqual(rows_and_digest(self.db)[0], 1)


if __name__ == "__main__":
    unittest.main()
