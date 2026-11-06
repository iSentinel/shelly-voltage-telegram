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
printf 'snapshot-overwrite\n' > "$OLD_SRC/.env"
printf 'snapshot-overwrite\n' > "$OLD_SRC/.env.pre-upgrade"
mkdir -p "$OLD_SRC/validation" "$OLD_SRC/secrets"
printf 'snapshot-overwrite\n' > "$OLD_SRC/validation/acceptance.txt"
printf 'snapshot-overwrite\n' > "$OLD_SRC/secrets/token"
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

# Reject corrupt but checksum-consistent archives before touching the target.
for unsafe_type in traversal symlink; do
    unsafe="$TMP/unsafe-$unsafe_type"
    mkdir -p "$unsafe"
    cp "$SNAP/voltage-old.sqlite3.gz" "$unsafe/"
    sha256sum "$unsafe/voltage-old.sqlite3.gz" > "$unsafe/voltage-old.sqlite3.gz.sha256"
    python3 - "$unsafe/source.tar.gz" "$unsafe_type" <<'PY_UNSAFE'
import io
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as archive:
    if sys.argv[2] == "traversal":
        info = tarfile.TarInfo("../escape.txt")
        payload = b"should-not-extract"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    else:
        info = tarfile.TarInfo("./redirect")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
PY_UNSAFE
    sha256sum "$unsafe/source.tar.gz" > "$unsafe/source.tar.gz.sha256"
    if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
        SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$unsafe" \
            --dry-run --no-start > "$TMP/unsafe-$unsafe_type.log" 2>&1; then
        echo "ERROR: unsafe $unsafe_type archive was accepted" >&2
        exit 1
    fi
    grep -q "ERROR: invalid source archive" "$TMP/unsafe-$unsafe_type.log"
    test ! -e "$TMP/escape.txt"
    test "$(cat "$TARGET/VERSION")" = "0.4.2-hw"
    echo "ROLLBACK_UNSAFE_$unsafe_type=REJECTED"
done

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

# A SHA256-valid gzip must be verified as SQLite before any service stop.
INVALID_DB="$TMP/invalid-sqlite-snapshot"
mkdir -p "$INVALID_DB"
cp "$SNAP/source.tar.gz" "$SNAP/source.tar.gz.sha256" "$INVALID_DB/"
printf "%s" "a corrupt SQLite database" | gzip -c \
    > "$INVALID_DB/voltage-corrupt.sqlite3.gz"
sha256sum "$INVALID_DB/voltage-corrupt.sqlite3.gz" \
    > "$INVALID_DB/voltage-corrupt.sqlite3.gz.sha256"
set +e
SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$INVALID_DB" \
        --dry-run --no-start > "$TMP/corrupt-sqlite-rollback.log" 2>&1
invalid_status=$?
set -e
test "$invalid_status" -eq 2
! grep -q "DRY-RUN: stop Compose service" "$TMP/corrupt-sqlite-rollback.log"
echo "ROLLBACK_BAD_SQLITE_REJECTED_BEFORE_STOP=PASS"
