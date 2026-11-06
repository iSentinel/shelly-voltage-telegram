#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TARGET="$TMP/target"
DB_DIR="$TMP/data"
SNAP_ROOT="$TMP/snapshots"
SNAP="$SNAP_ROOT/001-0.4.1-hw"
RESCUE="$TMP/rescue"
OLD_SRC="$TMP/old-source"

mkdir -p "$TARGET/scripts" "$TARGET/secrets" "$DB_DIR" "$SNAP" "$OLD_SRC"
cp "$PROJECT/scripts/backup.sh" "$TARGET/scripts/backup.sh"
chmod 0755 "$TARGET/scripts/backup.sh"
printf '0.4.2-hw\n' > "$TARGET/VERSION"
printf 'new\n' > "$TARGET/NEW_ONLY"
printf 'KEEP_ME=1\n' > "$TARGET/.env"
printf 'BACKUP_KEEP_ME=1\n' > "$TARGET/.env.pre-upgrade"
mkdir -p "$TARGET/validation"
printf 'keep private QA results\n' > "$TARGET/validation/acceptance.txt"
printf 'secret-preserved\n' > "$TARGET/secrets/token"

python3 - "$DB_DIR/voltage.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    db.executescript("""
    CREATE TABLE measurements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        voltage REAL NOT NULL,
        frequency REAL NOT NULL
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE outages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at REAL NOT NULL,
        ended_at REAL
    );
    INSERT INTO measurements(ts, voltage, frequency) VALUES(200, 222.0, 50.0);
    INSERT INTO meta(key, value) VALUES('schema_version', '2');
    """)
PY

printf '0.4.1-hw\n' > "$OLD_SRC/VERSION"
printf 'old\n' > "$OLD_SRC/OLD_ONLY"
tar -C "$OLD_SRC" -czf "$SNAP/source.tar.gz" .
sha256sum "$SNAP/source.tar.gz" > "$SNAP/source.tar.gz.sha256"

python3 - "$TMP/old.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    db.executescript("""
    CREATE TABLE measurements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        voltage REAL NOT NULL,
        frequency REAL NOT NULL
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO measurements(ts, voltage, frequency) VALUES(100, 111.0, 49.9);
    INSERT INTO meta(key, value) VALUES('schema_version', '1');
    """)
PY

gzip -c "$TMP/old.sqlite3" > "$SNAP/voltage-old.sqlite3.gz"
sha256sum "$SNAP/voltage-old.sqlite3.gz" > "$SNAP/voltage-old.sqlite3.gz.sha256"

MISSING_DB="$DB_DIR/missing.sqlite3"
SVT_ROOT="$TARGET" \
DATABASE_PATH="$MISSING_DB" \
SNAPSHOT_ROOT="$SNAP_ROOT" \
RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --dry-run --no-start \
    > "$TMP/missing-db-dry-run.log"
grep -q "ROLLBACK PASS" "$TMP/missing-db-dry-run.log"
test ! -e "$MISSING_DB"
echo "ROLLBACK_MISSING_DB_DRY_RUN=PASS"

SVT_ROOT="$TARGET" \
DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
SNAPSHOT_ROOT="$SNAP_ROOT" \
RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --no-start

test "$(cat "$TARGET/VERSION")" = "0.4.1-hw"
test -f "$TARGET/OLD_ONLY"
test ! -e "$TARGET/NEW_ONLY"
grep -qx 'KEEP_ME=1' "$TARGET/.env"
grep -qx 'BACKUP_KEEP_ME=1' "$TARGET/.env.pre-upgrade"
grep -qx 'keep private QA results' "$TARGET/validation/acceptance.txt"
grep -qx 'secret-preserved' "$TARGET/secrets/token"

python3 - "$DB_DIR/voltage.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0] == "1"
    assert db.execute("SELECT voltage FROM measurements").fetchone()[0] == 111.0
PY

RESCUE_DB="$(find "$RESCUE" -type f -name 'voltage-*.sqlite3.gz' | head -n1)"
test -n "$RESCUE_DB"
gzip -dc "$RESCUE_DB" > "$TMP/rescue.sqlite3"
python3 - "$TMP/rescue.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("SELECT voltage FROM measurements").fetchone()[0] == 222.0
PY

echo "ROLLBACK_FIXTURE PASS"
