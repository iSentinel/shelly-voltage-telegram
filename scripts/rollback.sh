#!/usr/bin/env bash
set -euo pipefail

TARGET="${SVT_ROOT:-/opt/shelly-voltage}"
DB="${DATABASE_PATH:-/var/lib/shelly-voltage/voltage.sqlite3}"
SNAPSHOT_ROOT="${SNAPSHOT_ROOT:-/var/backups/shelly-voltage/upgrades}"
RESCUE_ROOT="${RESCUE_ROOT:-/var/backups/shelly-voltage}"
SNAPSHOT=""
DRY_RUN=false
NO_START=false
CONFIRM_DB_REPLACEMENT=false
WRITER_REQUIRED=false
RESTORE_TMP=""

cleanup_restore_tmp() {
    # Never leave partially extracted private Telegram/SQLite state behind.
    if [[ -n "$RESTORE_TMP" ]]; then
        rm -f -- "$RESTORE_TMP"
    fi
}

trap cleanup_restore_tmp EXIT

usage() {
    cat <<'EOF'
Usage: rollback.sh [--snapshot DIR] [--dry-run] [--no-start] [--confirm-db-replacement] [-h|--help]

Restore source and SQLite from a pre-upgrade snapshot.
By default the newest snapshot under /var/backups/shelly-voltage/upgrades is used.

Options:
  --snapshot DIR  Restore this snapshot instead of the newest one.
  --dry-run       Validate and print planned actions only.
  --no-start      Restore files/database but do not start Docker/systemd.
  --confirm-db-replacement  Explicitly approve replacing an existing live SQLite DB.
  -h, --help      Show this help.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --snapshot) SNAPSHOT="$2"; shift ;;
            --dry-run) DRY_RUN=true ;;
            --no-start) NO_START=true ;;
            --confirm-db-replacement) CONFIRM_DB_REPLACEMENT=true ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        esac
        shift
    done
}

require_root() {
    [[ ${EUID} -eq 0 ]] || { echo "ERROR: run as root." >&2; exit 2; }
}

assert_unlinked_snapshot_path() {
    local part="$1" parent
    while [[ "$part" != / && "$part" != . ]]; do
        if [[ -L "$part" ]]; then
            echo "ERROR: rollback snapshot path contains a symlink." >&2
            exit 3
        fi
        parent="$(dirname -- "$part")"
        [[ "$part" != "$parent" ]] || break
        part="$parent"
    done
}

select_snapshot() {
    [[ -n "$SNAPSHOT" ]] && return
    assert_unlinked_snapshot_path "$SNAPSHOT_ROOT"
    SNAPSHOT="$(find "$SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -n1 | cut -d' ' -f2-)"
    [[ -n "$SNAPSHOT" ]] || { echo "ERROR: no upgrade snapshot found." >&2; exit 3; }
}

verify_archive_manifest() {
    local selected="$1" manifest="$1.sha256" computed recorded
    [[ -f "$manifest" && ! -L "$manifest" ]] || {
        echo "ERROR: archive manifest missing or linked." >&2
        exit 3
    }
    # Trusted rollback archives and SHA256 sidecars must be independent.
    if [[ "$(stat -c %h -- "$selected")" != 1 ]] ||
        [[ "$(stat -c %h -- "$manifest")" != 1 ]]; then
        echo "ERROR: rollback snapshot artifact has multiple links." >&2
        exit 3
    fi
    # Do not accept sha256sum -c success for a different pathname.
    computed="$(sha256sum -- "$selected")"
    recorded="$(cat -- "$manifest")"
    if [[ "$recorded" != "$computed" ]]; then
        echo "ERROR: archive manifest not bound to selected file." >&2
        exit 3
    fi
}

unique_snapshot_backup() {
    local directory="${1:-$SNAPSHOT}"
    local -a candidates=()
    mapfile -d '' -t candidates < <(
        find "$directory" -maxdepth 1 -type f -name 'voltage-*.sqlite3.gz' -print0
    )
    if [[ ${#candidates[@]} -ne 1 ]]; then
        echo "ERROR: snapshot must contain exactly one SQLite backup." >&2
        return 3
    fi
    printf '%s\n' "${candidates[0]}"
}

validate_snapshot() {
    assert_unlinked_snapshot_path "$SNAPSHOT"
    [[ -d "$SNAPSHOT" && ! -L "$SNAPSHOT" ]] || {
        echo "ERROR: snapshot directory is missing or linked." >&2
        exit 3
    }
    [[ -f "$SNAPSHOT/source.tar.gz" && ! -L "$SNAPSHOT/source.tar.gz" ]] || {
        echo "ERROR: source archive must be a regular nonlinked file." >&2
        exit 3
    }
    local backup
    backup="$(unique_snapshot_backup)"
    [[ -n "$backup" ]] || { echo "ERROR: SQLite backup missing." >&2; exit 3; }
    verify_archive_manifest "$SNAPSHOT/source.tar.gz"
    verify_archive_manifest "$backup"
    python3 "$(dirname "${BASH_SOURCE[0]}")/validate-sqlite-archive.py" \
        --archive "$backup"
    python3 "$(dirname "${BASH_SOURCE[0]}")/validate-source-archive.py" \
        "$SNAPSHOT/source.tar.gz"
}

require_db_replacement_approval() {
    [[ -f "$DB" ]] || return 0
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN WARNING: restore will replace the current SQLite DB."
        echo "DRY-RUN: real rollback requires --confirm-db-replacement."
        return
    fi
    if [[ "$CONFIRM_DB_REPLACEMENT" != true ]]; then
        echo "ERROR: would overwrite existing SQLite history." >&2
        echo "Run --dry-run; then explicitly pass --confirm-db-replacement." >&2
        exit 4
    fi
    echo "WARNING: explicitly approved replacing existing SQLite history."
}

assert_backup_path_unlinked() {
    local part="$1" parent
    while [[ "$part" != / && "$part" != . ]]; do
        if [[ -L "$part" ]]; then
            echo "ERROR: rollback unsafe backup path: symlink." >&2
            exit 3
        fi
        parent="$(dirname -- "$part")"
        [[ "$parent" != "$part" ]] || break
        part="$parent"
    done
}

preflight_source_target() {
    local part parent
    case "$TARGET" in
        /*) ;;
        *) echo "ERROR: rollback unsafe application target." >&2; exit 3 ;;
    esac
    case "$TARGET" in
        /|*/|*//*|*/./*|*/.|*/../*|*/..)
            echo "ERROR: rollback unsafe application target." >&2
            exit 3 ;;
    esac
    part="$TARGET"
    while [[ "$part" != / ]]; do
        if [[ -L "$part" ]]; then
            echo "ERROR: rollback unsafe application target." >&2
            exit 3
        fi
        parent="$(dirname -- "$part")"
        [[ "$part" != "$parent" ]] || break
        part="$parent"
    done
    if [[ ! -d "$TARGET" || ! -O "$TARGET" ]]; then
        echo "ERROR: rollback unsafe application target." >&2
        exit 3
    fi
}

preflight_backup_paths() {
    local sqlite_directory
    case "$DB" in
        /*) ;;
        *) echo "ERROR: rollback unsafe backup path: SQLite directory." >&2
           exit 3 ;;
    esac
    case "$DB" in
        */|*//*|*/./*|*/.|*/../*|*/..)
            echo "ERROR: rollback unsafe backup path: SQLite directory." >&2
            exit 3 ;;
    esac
    assert_backup_path_unlinked "$DB"
    sqlite_directory="$(dirname -- "$DB")"
    if [[ ! -d "$sqlite_directory" ||
          ! -w "$sqlite_directory" || ! -x "$sqlite_directory" ]]; then
        echo "ERROR: rollback unsafe backup path: SQLite directory." >&2
        exit 3
    fi
    assert_backup_path_unlinked "$RESCUE_ROOT"
    if [[ -e "$DB" && ! -f "$DB" ]]; then
        echo "ERROR: rollback unsafe backup path: SQLite type." >&2
        exit 3
    fi
    if [[ -f "$DB" && "$(stat -c %h -- "$DB")" != 1 ]]; then
        echo "ERROR: rollback unsafe backup path: SQLite links." >&2
        exit 3
    fi
    if [[ -e "$RESCUE_ROOT" ]] &&
        { [[ ! -d "$RESCUE_ROOT" || ! -O "$RESCUE_ROOT" ]] ||
          [[ "$(stat -c %a -- "$RESCUE_ROOT")" != 700 ]]; }; then
        echo "ERROR: rollback unsafe backup path: destination." >&2
        exit 3
    fi
}

rescue_current_db() {
    [[ -f "$DB" ]] || return 0
    local rescue label
    label="rollback-rescue-$(date +%Y%m%d-%H%M%S)"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: backup current DB -> $RESCUE_ROOT/$label-XXXXXXXX"
        return
    fi
    # Each approved rollback owns a separate 0700 rescue directory.
    mkdir -m 0700 -p -- "$RESCUE_ROOT"
    if [[ ! -O "$RESCUE_ROOT" ]] ||
        [[ "$(stat -c %a -- "$RESCUE_ROOT")" != 700 ]]; then
        echo "ERROR: rollback rescue root must be owner-owned 0700." >&2
        exit 3
    fi
    rescue="$(mktemp -d -- "$RESCUE_ROOT/$label-XXXXXXXX")"
    DATABASE_PATH="$DB" BACKUP_DIR="$rescue" BACKUP_KEEP=1 \
        "$TARGET/scripts/backup.sh" >/dev/null
    # A successful writer exit does not prove its gzip can restore.
    local archive
    archive="$(unique_snapshot_backup "$rescue")"
    verify_archive_manifest "$archive"
    python3 "$(dirname "${BASH_SOURCE[0]}")/validate-sqlite-archive.py" \
        --archive "$archive" --compare-db "$DB" >/dev/null
    echo "Rescue backup: $rescue"
}

assert_writer_stopped() {
    [[ "$WRITER_REQUIRED" == true ]] || return 0
    local running
    if ! running="$(docker inspect --format '{{.State.Running}}' shelly-voltage-bot 2>/dev/null)"; then
        echo "ERROR: Docker writer state could not be verified." >&2
        exit 5
    fi
    if [[ "$running" != false ]]; then
        echo "ERROR: Docker writer is still running or state is unknown." >&2
        exit 5
    fi
}

stop_service() {
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: stop Compose service"; return; }
    # A missing Compose file is not proof that the Docker SQLite writer stopped.
    WRITER_REQUIRED=true
    if [[ -f "$TARGET/compose.yaml" ]]; then
        if ! docker compose -f "$TARGET/compose.yaml" stop; then
            echo "ERROR: Docker Compose failed to stop the writer." >&2
            exit 5
        fi
    fi
    assert_writer_stopped
}

restore_source() {
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: restore source -> $TARGET"; return; }
    mkdir -p "$TARGET"
    # Keep locally created checkpoints/locks that do not belong to releases.
    find "$TARGET" -mindepth 1 -maxdepth 1 \
        ! -name '.env*' ! -name 'secrets' ! -name 'validation' \
        ! -name 'ROADMAP.md' ! -name 'ROADMAP.md.pre-*' \
        ! -name '.work-state' ! -name '.roadmap-update.lock' \
        ! -name '.telegram-private-acceptance.lock' \
        -exec rm -rf -- {} +
    tar -xzf "$SNAPSHOT/source.tar.gz" -C "$TARGET" \
        --exclude="./.env" --exclude="./.env.*" \
        --exclude="./secrets" --exclude="./validation" \
        --exclude="./ROADMAP.md" --exclude="./ROADMAP.md.pre-*" \
        --exclude="./.work-state" --exclude="./.roadmap-update.lock" \
        --exclude="./.telegram-private-acceptance.lock"
}

restore_database() {
    local backup
    backup="$(unique_snapshot_backup)"
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: restore DB $backup -> $DB"; return; }
    # mktemp uses an unpredictable, exclusive mode-0600 file on this filesystem.
    RESTORE_TMP="$(mktemp -- "${DB}.restore.XXXXXXXX")"
    gzip -dc "$backup" > "$RESTORE_TMP"
    python3 - "$RESTORE_TMP" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as db:
    result = db.execute("PRAGMA quick_check").fetchone()[0]
if result != "ok":
    raise SystemExit(f"restored DB quick_check failed: {result}")
PY
    chown 10001:10001 "$RESTORE_TMP"
    chmod 0640 "$RESTORE_TMP"
    assert_writer_stopped
    rm -f "${DB}-wal" "${DB}-shm"
    mv -f "$RESTORE_TMP" "$DB"
    RESTORE_TMP=""
}

start_service() {
    [[ "$NO_START" == true ]] && return
    [[ "$DRY_RUN" == true ]] && { echo "DRY-RUN: rebuild/start and wait for /health"; return; }
    cp "$TARGET/systemd/"*.service /etc/systemd/system/
    cp "$TARGET/systemd/"*.timer /etc/systemd/system/ 2>/dev/null || true
    systemctl daemon-reload
    # Use the validated plan saved before restoring the old source.
    # This preserves a separately approved, read-only YASNO overlay.
    compose_cmd config -q
    compose_cmd up -d --build
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
    preflight_source_target
    select_snapshot
    validate_snapshot
    echo "Rollback snapshot: $SNAPSHOT"
    require_db_replacement_approval
    preflight_backup_paths
    python3 "$(dirname -- "${BASH_SOURCE[0]}")/graphic-rollback-media-guard.py" \
        --database "$DB"
    if [[ "$NO_START" == false ]]; then
        # Load secure layer checks BEFORE restore_source replaces scripts.
        # Keep the functions in memory for starting the restored version.
        source "$(dirname -- "${BASH_SOURCE[0]}")/start-compose.sh"
        compose_prepare true
    fi
    stop_service
    # A Telegram callback may change status media while Docker is stopping.
    # No source or SQLite restore is permitted after an identity race.
    if ! python3 "$(dirname -- "${BASH_SOURCE[0]}")/graphic-rollback-media-guard.py" \
        --database "$DB"; then
        echo "ERROR: Telegram status changed during writer stop; rollback blocked." >&2
        if [[ "$NO_START" == false && "$DRY_RUN" == false ]]; then
            echo "NOTICE: restoring the still-installed service, not old SQLite." >&2
            start_service
        else
            echo "NOTICE: writer remains stopped by operator request." >&2
        fi
        exit 6
    fi
    rescue_current_db
    assert_writer_stopped
    restore_source
    restore_database
    start_service
    echo "ROLLBACK PASS"
}

main "$@"
