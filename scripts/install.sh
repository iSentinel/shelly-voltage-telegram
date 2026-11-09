#!/usr/bin/env bash
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${SVT_ROOT:-/opt/shelly-voltage}"
DRY_RUN=false
NON_INTERACTIVE=false

usage() {
    cat <<'EOF'
Usage: install.sh [--dry-run] [--non-interactive] [-h|--help]

Install or update Shelly Voltage Telegram on Debian 13.
Existing .env, secret files, SQLite data and backups are preserved.
Existing deployments also get a versioned pre-upgrade source + SQLite snapshot.

Options:
  --dry-run          Print planned actions without changing the system.
  --non-interactive  Do not prompt; use existing config or SVT_* variables.
  -h, --help         Show this help.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run) DRY_RUN=true ;;
            --non-interactive) NON_INTERACTIVE=true ;;
            -h|--help) usage; exit 0 ;;
            *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
        esac
        shift
    done
}

run() {
    if [[ "$DRY_RUN" == true ]]; then
        printf 'DRY-RUN:'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

require_root() {
    if [[ ${EUID} -ne 0 ]]; then
        echo "ERROR: run with sudo/root." >&2
        exit 2
    fi
}

require_safe_install_target() {
    local part parent
    case "$TARGET" in
        /*) ;;
        *) echo "ERROR: unsafe application target." >&2; exit 3 ;;
    esac
    case "$TARGET" in
        /|*/|*//*|*/./*|*/.|*/../*|*/..)
            echo "ERROR: unsafe application target." >&2
            exit 3 ;;
    esac
    part="$TARGET"
    while [[ "$part" != / ]]; do
        if [[ -L "$part" ]]; then
            echo "ERROR: unsafe application target." >&2
            exit 3
        fi
        parent="$(dirname -- "$part")"
        [[ "$part" != "$parent" ]] || break
        part="$parent"
    done
    if [[ -e "$TARGET" && ! -d "$TARGET" ]] ||
       [[ -d "$TARGET" && ! -O "$TARGET" ]]; then
        echo "ERROR: unsafe application target." >&2
        exit 3
    fi
}

require_private_regular_file() {
    local path="$1"
    if [[ -L "$path" ]] ||
       [[ -e "$path" && ! -f "$path" ]]; then
        echo "ERROR: unsafe protected installation file." >&2
        exit 3
    fi
    if [[ -f "$path" && "$(stat -c %h -- "$path")" -ne 1 ]]; then
        echo "ERROR: shared protected installation file." >&2
        exit 3
    fi
}

require_safe_install_files() {
    [[ -d "$TARGET" ]] || return 0
    if [[ -L "$TARGET/secrets" ]] ||
       [[ -e "$TARGET/secrets" && ! -d "$TARGET/secrets" ]]; then
        echo "ERROR: unsafe installation secrets directory." >&2
        exit 3
    fi
    local file
    require_private_regular_file "$TARGET/.env"
    for file in telegram_token telegram_chat_id shelly_password \
                shelly_l2_password shelly_l3_password; do
        require_private_regular_file "$TARGET/secrets/$file"
    done
}

require_quiesced_upgrade() {
    [[ -d "$TARGET" && -f "$TARGET/VERSION" ]] || return 0
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: require stopped Shelly Docker writer before upgrade."
        return 0
    fi
    local running
    if ! running="$(docker inspect --format '{{.State.Running}}' \
        shelly-voltage-bot 2>/dev/null)"; then
        echo "ERROR: cannot verify stopped Shelly writer." >&2
        exit 5
    fi
    if [[ "$running" != false ]]; then
        echo "ERROR: Shelly writer is running; quiescence required." >&2
        exit 5
    fi
}

install_docker() {
    if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
        return
    fi
    command -v apt-get >/dev/null || {
        echo "ERROR: automatic Docker install currently supports apt/Debian." >&2
        exit 2
    }
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: install Docker Engine and Compose from OS packages"
        return
    fi
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io ca-certificates curl
    command -v docker >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y docker-cli
    if ! docker compose version >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose || \
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
    fi
    systemctl enable --now docker
}

prepare_upgrade_root() {
    local root="$1" part parent
    [[ "$root" == /* && "$root" != / ]] || {
        echo "ERROR: unsafe upgrade snapshot root." >&2
        exit 3
    }
    part="$root"
    while [[ "$part" != / ]]; do
        [[ ! -L "$part" ]] || {
            echo "ERROR: linked upgrade snapshot root." >&2
            exit 3
        }
        parent="$(dirname -- "$part")"
        [[ "$part" != "$parent" ]] || break
        part="$parent"
    done
    umask 077
    mkdir -m 0700 -p -- "$root"
    [[ -d "$root" && -O "$root" ]] || {
        echo "ERROR: upgrade snapshot root is not owner-owned." >&2
        exit 3
    }
    chmod 0700 -- "$root"
}

prune_upgrade_snapshots() {
    local root="$1" index path
    local -a ordered=()
    mapfile -d '' -t ordered < <(
        find "$root" -mindepth 1 -maxdepth 1 -type d \
            -printf '%T@ %p\0' | sort -zrn
    )
    for index in "${!ordered[@]}"; do
        (( index >= 5 )) || continue
        path="${ordered[index]#* }"
        [[ "$path" == "$root/"* ]] || {
            echo "ERROR: unsafe upgrade snapshot rotation path." >&2
            exit 3
        }
        rm -rf -- "$path"
    done
}

snapshot_upgrade() {
    [[ -d "$TARGET" && -f "$TARGET/VERSION" ]] || return 0
    [[ "$SOURCE" != "$TARGET" ]] || return 0
    local stamp old_version root snapshot sqlite_archive
    local sqlite_db=/var/lib/shelly-voltage/voltage.sqlite3
    stamp="$(date +%Y%m%d-%H%M%S)"
    old_version="$(tr -d '\n' < "$TARGET/VERSION")"
    [[ "$old_version" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || {
        echo "ERROR: invalid installed version for snapshot." >&2
        exit 3
    }
    root="${UPGRADE_SNAPSHOT_ROOT:-/var/backups/shelly-voltage/upgrades}"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: snapshot current source and SQLite -> $root"
        return
    fi
    prepare_upgrade_root "$root"
    snapshot="$(mktemp -d -- "$root/${stamp}-${old_version}-XXXXXXXX")"
    bash "$SOURCE/scripts/source-tar.sh" "$TARGET" |
        gzip -c > "$snapshot/source.tar.gz"
    DATABASE_PATH="$sqlite_db" BACKUP_DIR="$snapshot" BACKUP_KEEP=1 \
        "$SOURCE/scripts/backup.sh" > "$snapshot/backup.log"
    printf 'from_version=%s\nto_version=%s\n' \
        "$old_version" "$(tr -d '\n' < "$SOURCE/VERSION")" \
        > "$snapshot/manifest.env"
    sha256sum "$snapshot/source.tar.gz" > "$snapshot/source.tar.gz.sha256"
    sqlite_archive="$(find "$snapshot" -maxdepth 1 -type f \
        -name "voltage-*.sqlite3.gz" -print -quit)"
    [[ -n "$sqlite_archive" ]] || {
        echo "ERROR: upgrade SQLite backup missing." >&2
        exit 3
    }
    # A valid SQLite archive may still contain older measurements.
    # Compare every table and persistent header before source replacement.
    python3 "$SOURCE/scripts/validate-sqlite-archive.py" \
        --archive "$sqlite_archive" --compare-db "$sqlite_db"
    prune_upgrade_snapshots "$root"
    echo "Upgrade snapshot: $snapshot"
}

copy_source() {
    [[ "$SOURCE" == "$TARGET" ]] && return
    run mkdir -p "$TARGET"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: copy project files $SOURCE -> $TARGET, preserving .env/secrets"
        return
    fi
    bash "$SOURCE/scripts/source-tar.sh" "$SOURCE" |
        tar -C "$TARGET" -xf -
}

prepare_state() {
    run mkdir -p /var/lib/shelly-voltage /var/backups/shelly-voltage "$TARGET/secrets"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: set secure modes/ownership for state and secrets"
        return
    fi
    [[ -f "$TARGET/.env" ]] || cp "$TARGET/.env.example" "$TARGET/.env"
    chmod 0600 "$TARGET/.env"
    chmod 0700 "$TARGET/secrets" /var/backups/shelly-voltage
    chown 10001:10001 /var/lib/shelly-voltage
    chmod 0750 /var/lib/shelly-voltage
    for file in telegram_token telegram_chat_id shelly_password shelly_l2_password shelly_l3_password; do
        touch "$TARGET/secrets/$file"
        chown 10001:10001 "$TARGET/secrets/$file"
        chmod 0600 "$TARGET/secrets/$file"
    done
}

configure_project() {
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: configure Shelly host and optional Telegram secrets"
        return
    fi
    if [[ "$NON_INTERACTIVE" == true ]]; then
        "$TARGET/scripts/configure.sh" --non-interactive
    else
        "$TARGET/scripts/configure.sh"
    fi
}

install_units() {
    local unit
    for unit in shelly-voltage-compose.service shelly-voltage-backup.service shelly-voltage-backup.timer shelly-voltage-update-check.service shelly-voltage-update-check.timer shelly-voltage-updater-apply@.service; do
        run cp "$TARGET/systemd/$unit" "/etc/systemd/system/$unit"
    done
    run systemctl daemon-reload
    run systemctl enable shelly-voltage-compose.service shelly-voltage-backup.timer shelly-voltage-update-check.timer
}

deploy() {
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: docker compose build/start and health verification"
        return
    fi
    cd "$TARGET"
    docker compose config -q
    docker compose up -d --build
    systemctl start shelly-voltage-compose.service shelly-voltage-backup.timer
    for _ in $(seq 1 20); do
        if curl -fsS http://127.0.0.1:8080/health >/dev/null; then
            local ready_code
            ready_code="$(
                curl -sS -o /dev/null -w '%{http_code}' \
                    http://127.0.0.1:8080/ready || true
            )"
            if [[ "$ready_code" != 200 ]]; then
                echo "NOTICE: service is healthy; Shelly data is not ready ($ready_code)."
            fi
            return
        fi
        sleep 2
    done
    echo "ERROR: service health endpoint did not become available." >&2
    docker compose ps >&2
    exit 1
}

summary() {
    echo
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN PASS: no changes applied."
        return
    fi
    echo "INSTALL PASS"
    docker compose -f "$TARGET/compose.yaml" ps
    echo "Health: http://127.0.0.1:8080/health"
    echo "Logs:   docker compose -f $TARGET/compose.yaml logs -f"
}

main() {
    parse_args "$@"
    require_root
    require_safe_install_target
    require_safe_install_files
    require_quiesced_upgrade
    install_docker
    snapshot_upgrade
    copy_source
    prepare_state
    configure_project
    install_units
    deploy
    summary
}

main "$@"
