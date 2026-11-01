#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    printf '%b\n' 'Usage: backup.sh [-h|--help]\nCreate an online SQLite backup and retain the newest BACKUP_KEEP copies.'
    exit 0
fi

DB="${DATABASE_PATH:-/var/lib/shelly-voltage/voltage.sqlite3}"
DEST="${BACKUP_DIR:-/var/backups/shelly-voltage}"
KEEP="${BACKUP_KEEP:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
TMP="${DEST}/.voltage-${STAMP}.sqlite3"
OUT="${DEST}/voltage-${STAMP}.sqlite3.gz"

[[ -f "$DB" ]] || {
    echo "ERROR: SQLite database not found: $DB" >&2
    exit 3
}

mkdir -p "$DEST"
chmod 0700 "$DEST"

python3 - "$DB" "$TMP" <<'PY'
import sqlite3
import sys
from contextlib import closing

source, destination = sys.argv[1:3]
with (
    closing(sqlite3.connect(source)) as src, src,
    closing(sqlite3.connect(destination)) as dst, dst,
):
    src.backup(dst)
    result = dst.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise SystemExit(f"backup quick_check failed: {result}")
PY

gzip -9c "$TMP" > "$OUT"
rm -f "$TMP"
sha256sum "$OUT" > "${OUT}.sha256"

mapfile -t old < <(find "$DEST" -maxdepth 1 -type f -name 'voltage-*.sqlite3.gz' -printf '%T@ %p\n' | sort -nr | awk 'NR > '"$KEEP"' {print $2}')
for file in "${old[@]:-}"; do
    [[ -n "$file" ]] || continue
    rm -f "$file" "${file}.sha256"
done

printf 'backup=%s\n' "$OUT"
sha256sum "$OUT"
