#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
FIXTURE="$TMP/fixture"
OUT="$TMP/out"
EXTRACT="$TMP/extract"
MARKER="redaction-marker-12345"
mkdir -p "$FIXTURE/scripts" "$FIXTURE/secrets" "$OUT" "$EXTRACT"
cp "$PROJECT/scripts/soak-report.py" "$FIXTURE/scripts/soak-report.py"
cp "$PROJECT/compose.yaml" "$FIXTURE/compose.yaml"
printf '%s\n' 'SHELLY_HOST=192.0.2.10' "SHELLY_PASSWORD=$MARKER" \
    > "$FIXTURE/.env"
printf '%s\n' "$MARKER" > "$FIXTURE/secrets/shelly_password"
: > "$FIXTURE/secrets/telegram_token"
: > "$FIXTURE/secrets/telegram_chat_id"

python3 - "$FIXTURE/data.sqlite3" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as db, db:
    db.executescript("""
    CREATE TABLE measurements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL, voltage REAL NOT NULL, frequency REAL NOT NULL
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE outages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at REAL NOT NULL, ended_at REAL
    );
    INSERT INTO measurements(ts,voltage,frequency)
    VALUES(1793446000,230.0,50.0);
    INSERT INTO meta(key,value) VALUES('schema_version','2');
    INSERT INTO outages(started_at,ended_at) VALUES(1793445900,NULL);
    """)
PY

SVT_ROOT="$FIXTURE" \
DATABASE_PATH="$FIXTURE/data.sqlite3" \
SUPPORT_BUNDLE_DIR="$OUT" \
CONTAINER_NAME="missing-$MARKER" \
    "$PROJECT/scripts/support-bundle.sh" > "$TMP/create.log"

BUNDLE="$(find "$OUT" -maxdepth 1 -type f -name '*.tar.gz' | head -n1)"
test -n "$BUNDLE"
sha256sum -c "${BUNDLE}.sha256"
tar -xzf "$BUNDLE" -C "$EXTRACT"
if grep -R -F "$MARKER" "$EXTRACT"; then
    echo "ERROR: redaction marker leaked into support bundle" >&2
    exit 1
fi
grep -R -q '<redacted>' "$EXTRACT"
grep -q '"schema_version": 2' "$EXTRACT/soak-24h.json"
grep -q '"confirmed_outages": 1' "$EXTRACT/soak-24h.json"
grep -q '"confirmed_open_outages": 1' "$EXTRACT/soak-24h.json"
grep -q 'outages= (1, 1,' "$EXTRACT/database.txt"
echo "SUPPORT_BUNDLE_REDACTION PASS"
