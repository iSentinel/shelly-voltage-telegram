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
    # Do not accept sha256sum -c success for a different pathname.
    computed="$(sha256sum -- "$selected")"
    recorded="$(cat -- "$manifest")"
    if [[ "$recorded" != "$computed" ]]; then
        echo "ERROR: archive manifest not bound to selected file." >&2
        exit 3
    fi
}

unique_snapshot_backup() {
    local -a candidates=()
    mapfile -d '' -t candidates < <(
        find "$SNAPSHOT" -maxdepth 1 -type f -name 'voltage-*.sqlite3.gz' -print0
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
    python3 - "$SNAPSHOT/source.tar.gz" <<'PY_CHECK'
import sys
import tarfile
from pathlib import PurePosixPath

try:
    count = 0
    size = 0
    required = {
        "VERSION", "app.py", "Dockerfile", "compose.yaml",
        "requirements.txt", "scripts/backup.sh",
        "systemd/shelly-voltage-compose.service",
        "systemd/shelly-voltage-backup.service",
    }
    present = set()
    seen = set()
    with tarfile.open(sys.argv[1], "r:gz") as archive:
        for entry in archive:
            count += 1
            size += entry.size
            member_path = PurePosixPath(entry.name)
            parts = member_path.parts
            safe_type = entry.isfile() or entry.isdir()
            if (
                entry.name.startswith("/")
                or ".." in parts
                or not safe_type
                or entry.mode & 0o6000
                or count > 20000
                or size > 512 * 1024 * 1024
            ):
                raise ValueError("unsafe source archive member")
            if str(member_path) in seen:
                raise ValueError("duplicate source archive member")
            seen.add(str(member_path))
            if entry.isfile() and entry.size > 0:
                present.add(str(member_path))
    if not required.issubset(present):
        raise ValueError("source archive missing required application files")
except (OSError, ValueError, tarfile.TarError) as error:
    print(f"ERROR: invalid source archive: {error}", file=sys.stderr)
    raise SystemExit(3) from error
PY_CHECK
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

rescue_current_db() {
    [[ -f "$DB" ]] || return 0
    local rescue
    rescue="$RESCUE_ROOT/rollback-rescue-$(date +%Y%m%d-%H%M%S)"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: backup current DB -> $rescue"
        return
    fi
    # The backup writer refuses existing directories not already mode 0700.
    mkdir -m 0700 -p -- "$rescue"
    DATABASE_PATH="$DB" BACKUP_DIR="$rescue" BACKUP_KEEP=1 \
        "$TARGET/scripts/backup.sh" >/dev/null
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
    require_db_replacement_approval
    stop_service
    rescue_current_db
    assert_writer_stopped
    restore_source
    restore_database
    start_service
    echo "ROLLBACK PASS"
}

main "$@"
