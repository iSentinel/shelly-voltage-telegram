#!/usr/bin/env bash
set -euo pipefail

TARGET="${SVT_ROOT:-/opt/shelly-voltage}"
DB="${DATABASE_PATH:-/var/lib/shelly-voltage/voltage.sqlite3}"
SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-/var/backups/shelly-voltage/upgrades}"
RESCUE_ROOT="${RESCUE_ROOT:-/var/backups/shelly-voltage}"
SNAPSHOT=""
DRY_RUN=false
NO_START=false

usage() {
    cat <<'EOF'
Usage: rollback.sh [--snapshot DIR] [--dry-run] [--no-start] [-h|--help]

Restore source and SQLite from a pre-upgrade snapshot.
By default the newest snapshot under /var/backups/shelly-voltage/upgrades is used.

Options:
  --snapshot DIR  Restore this snapshot instead of the newest one.
  --dry-run       Validate and print planned actions only.
  --no-start      Restore files/database but do not start Docker/systemd.
  -h, --help      Show this help.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --snapshot) SNAPSHOT="$2"; shift ;;
            --dry-run) DRY_RUN=true ;;
            --no-start) NO_START=true ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        esac
        shift
    done
}

require_root() {
    [[ ${EUID} -eq 0 ]] || { echo "ERROR: run as root." >&2; exit 2; }
}

select_snapshot() {
    [[ -n "$SNAPSHOT" ]] && return
    SNAPSHOT="$(find "$SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
    [[ -n "$SNAPSHOT" ]] || { echo "ERROR: no upgrade snapshot found." >&2; exit 3; }
}

validate_snapshot() {
    [[ -f "$SNAPSHOT/source.tar.gz" ]] || { echo "ERROR: source archive missing." >&2; exit 3; }
    local backup
    backup="$(find "$SNAPSHOT" -maxdepth 1 -type f -name 'voltage-*.sqlite3.gz' | head -n1)"
    [[ -n "$backup" ]] || { echo "ERROR: SQLite backup missing." >&2; exit 3; }
    sha256sum -c "$SNAPSHOT/source.tar.gz.sha256"
    sha256sum -c "${backup}.sha256"
    python3 "$(dirname "${BASH_SOURCE[0]}")/validate-rollback-snapshot.py" \
        --snapshot "$SNAPSHOT"
}

rescue_current_db() {
    [[ -f "$DB" ]] || return 0
    local rescue
    rescue="$RESCUE_ROOT/rollback-rescue-$(date +%Y%m%d-%H%M%S)"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: backup current DB -> $rescue"
        return
    fi
    mkdir -p "$rescue"
    DATABASE_PATH="$DB" BACKUP_DIR="$rescue" BACKUP_KEEP=1 \
        "$TARGET/scripts/backup.sh" >/dev/null
    echo "Rescue backup: $rescue"
}

stop_service() {
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: stop Compose service"; return; }
    if [[ -f "$TARGET/compose.yaml" ]]; then
        docker compose -f "$TARGET/compose.yaml" stop || true
    fi
}

restore_source() {
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: restore source -> $TARGET"; return; }
    mkdir -p "$TARGET"
    find "$TARGET" -mindepth 1 -maxdepth 1 \
        ! -name '.env' ! -name 'secrets' -exec rm -rf -- {} +
    tar -xzf "$SNAPSHOT/source.tar.gz" -C "$TARGET"
}

restore_database() {
    local backup tmp
    backup="$(find "$SNAPSHOT" -maxdepth 1 -type f -name 'voltage-*.sqlite3.gz' | head -n1)"
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: restore DB $backup -> $DB"; return; }
    tmp="${DB}.restore.$$"
    gzip -dc "$backup" > "$tmp"
    python3 - "$tmp" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as db:
    result = db.execute("PRAGMA quick_check").fetchone()[0]
if result != "ok":
    raise SystemExit(f"restored DB quick_check failed: {result}")
PY
    chown 10001:10001 "$tmp"
    chmod 0640 "$tmp"
    rm -f "${DB}-wal" "${DB}-shm"
    mv -f "$tmp" "$DB"
}

start_service() {
    [[ "$NO_START" == true ]] && return
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: rebuild/start and wait for /health"; return; }
    cp "$TARGET/systemd/"*.service /etc/systemd/system/
    cp "$TARGET/systemd/"*.timer /etc/systemd/system/ 2>/dev/null || true
    systemctl daemon-reload
    docker compose -f "$TARGET/compose.yaml" up -d --build
    for _ in $(seq 1 20); do
        curl -fsS http://127.0.0.1:8080/health >/dev/null && return
        sleep 2
    done
    echo "ERROR: rollback service did not become healthy." >&2
    exit 1
}

main() {
    parse_args "$@"
    require_root
    select_snapshot
    validate_snapshot
    echo "Rollback snapshot: $SNAPSHOT"
    rescue_current_db
    stop_service
    restore_source
    restore_database
    start_service
    echo "ROLLBACK PASS"
}

main "$@"
