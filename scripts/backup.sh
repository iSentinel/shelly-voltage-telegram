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
STAGE=""
OUT=""
ARCHIVE_LINKED=false
SIDECAR_LINKED=false

cleanup_backup() {
    if [[ "$ARCHIVE_LINKED" == true && "$SIDECAR_LINKED" != true ]]; then
        rm -f -- "$OUT"
    fi
    [[ -z "$STAGE" ]] || rm -rf -- "$STAGE"
}
trap cleanup_backup EXIT

[[ "$KEEP" =~ ^[0-9]+$ && "$KEEP" -ge 1 ]] || {
    echo "ERROR: BACKUP_KEEP must be a positive integer." >&2
    exit 2
}
[[ -f "$DB" && ! -L "$DB" ]] || {
    echo "ERROR: regular SQLite database not found." >&2
    exit 3
}
[[ ! -L "$DEST" ]] || {
    echo "ERROR: backup destination must not be a link." >&2
    exit 3
}

umask 077
mkdir -p -- "$DEST"
chmod 0700 -- "$DEST"
STAGE="$(mktemp -d -- "$DEST/.voltage-${STAMP}.XXXXXXXX")"
SUFFIX="${STAGE##*.}"
TMP="$STAGE/snapshot.sqlite3"
OUT="$DEST/voltage-${STAMP}-${SUFFIX}.sqlite3.gz"

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

gzip -9c "$TMP" > "$STAGE/archive.sqlite3.gz"
digest="$(sha256sum -- "$STAGE/archive.sqlite3.gz" | awk '{print $1}')"
printf '%s  %s\n' "$digest" "$OUT" > "$STAGE/archive.sha256"

# Hard links publish completed private files exclusively, without clobbering.
ln -- "$STAGE/archive.sqlite3.gz" "$OUT"
ARCHIVE_LINKED=true
ln -- "$STAGE/archive.sha256" "${OUT}.sha256"
SIDECAR_LINKED=true

mapfile -t old < <(find "$DEST" -maxdepth 1 -type f -name 'voltage-*.sqlite3.gz' -printf '%T@ %p\n' | sort -nr | awk 'NR > '"$KEEP"' {print $2}')
for file in "${old[@]:-}"; do
    [[ -n "$file" ]] || continue
    rm -f "$file" "${file}.sha256"
done

printf 'backup=%s\n' "$OUT"
sha256sum "$OUT"
