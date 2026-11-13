#!/usr/bin/env bash
set -euo pipefail

SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${SVT_ROOT:-/opt/shelly-voltage}"
DRY_RUN=false
NON_INTERACTIVE=false
SIGNED_DEPLOYMENT=false

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

require_valid_install_marker() {
    [[ -d "$TARGET" ]] || return 0
    local version="$TARGET/VERSION" unexpected
    if [[ -L "$version" ]]; then
        echo "ERROR: unsafe installed VERSION marker." >&2
        exit 5
    fi
    if [[ -e "$version" ]]; then
        if [[ ! -f "$version" ]] ||
           [[ "$(stat -c %h -- "$version")" -ne 1 ]]; then
            echo "ERROR: unsafe installed VERSION marker." >&2
            exit 5
        fi
        return 0
    fi
    # Only a preconfigured fresh install may lack VERSION.
    unexpected="$(find "$TARGET" -mindepth 1 -maxdepth 1 \
        ! -name .env ! -name .env.example ! -name secrets \
        -printf 'found' -quit)"
    if [[ -n "$unexpected" ]]; then
        echo "ERROR: existing project files missing VERSION." >&2
        exit 5
    fi
}

require_safe_fresh_database_state() {
    # New install roots must not reuse an existing shared SQLite history.
    [[ -d "$TARGET" && -f "$TARGET/VERSION" ]] && return 0
    local db="/var/lib/shelly-voltage/voltage.sqlite3" candidate
    for candidate in "$db" "$db-wal" "$db-shm" "$db-journal"; do
        if [[ -e "$candidate" || -L "$candidate" ]]; then
            echo "ERROR: existing Shelly SQLite state requires reviewed migration." >&2
            exit 5
        fi
    done
}

require_quiesced_upgrade() {
    local installed=false running
    if [[ -d "$TARGET" && -f "$TARGET/VERSION" ]]; then
        installed=true
    fi
    if [[ "$DRY_RUN" == true ]]; then
        if [[ "$installed" == true ]]; then
            echo "DRY-RUN: require stopped Shelly Docker writer before upgrade."
        fi
        return 0
    fi
    if ! command -v docker >/dev/null 2>&1; then
        [[ "$installed" == false ]] && return 0
        echo "ERROR: cannot verify stopped Shelly writer." >&2
        exit 5
    fi
    if ! running="$(docker inspect --format '{{.State.Running}}' \
        shelly-voltage-bot 2>/dev/null)"; then
        if [[ "$installed" == true ]]; then
            echo "ERROR: cannot verify stopped Shelly writer." >&2
            exit 5
        fi
        # A failed inspect is ambiguous: distinguish no named container
        # from an unreachable Docker daemon before a first install.
        local names
        if ! names="$(docker container ls --all --format '{{.Names}}' \
            2>/dev/null)"; then
            echo "ERROR: cannot confirm Shelly writer is absent." >&2
            exit 5
        fi
        if printf '%s\n' "$names" | grep -Fxq shelly-voltage-bot; then
            echo "ERROR: existing Shelly writer state is unknown." >&2
            exit 5
        fi
        return 0
    fi
    if [[ "$running" != false ]]; then
        echo "ERROR: Shelly writer is running; quiescence required." >&2
        exit 5
    fi
}

signed_image_preflight() {
    local state=/var/lib/shelly-voltage-updater
    local receipt="$state/deployed.json" override="$state/override.yaml"
    # Isolated alternate-target dry runs never alter the shared live host.
    if [[ "$DRY_RUN" == true && "$TARGET" != /opt/shelly-voltage ]]; then
        echo "DRY-RUN: skip live signed-state check for alternate target."
        return 0
    fi
    if [[ ! -e "$receipt" && ! -L "$receipt" &&
          ! -e "$override" && ! -L "$override" ]]; then
        local actual
        actual="$(docker inspect --format '{{.Config.Image}}' \
            shelly-voltage-bot 2>/dev/null || true)"
        if [[ "$actual" == ghcr.io/isentinel/shelly-voltage-telegram@sha256:* ]]; then
            echo "ERROR: signed Docker image exists without protected receipt." >&2
            exit 5
        fi
        return 0
    fi
    # Never fall back to local Docker builds with a signed marker.
    if [[ "$DRY_RUN" == true ]]; then
        python3 "$SOURCE/scripts/installer-signed-image-guard.py" \
            --source "$SOURCE" --state "$state" --dry-run
    else
        python3 "$SOURCE/scripts/installer-signed-image-guard.py" \
            --source "$SOURCE" --state "$state"
    fi
    SIGNED_DEPLOYMENT=true
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

snapshot_artifact_verified() {
    local file="$1" manifest="$1.sha256" digest
    [[ -f "$file" && -f "$manifest" &&
       ! -L "$file" && ! -L "$manifest" ]] || return 1
    [[ "$(stat -c %h -- "$file")" == 1 &&
       "$(stat -c %h -- "$manifest")" == 1 ]] || return 1
    digest="$(sha256sum -- "$file")" || return 1
    [[ "$(cat -- "$manifest")" == "$digest" ]] || return 1
    gzip -t -- "$file" >/dev/null 2>&1
}

prune_upgrade_snapshots() {
    local root="$1" index path name record
    local -a ordered=() snapshots=() sqlite_archives=()
    mapfile -d '' -t ordered < <(
        find "$root" -mindepth 1 -maxdepth 1 -type d \
            -printf '%T@ %p\0' | sort -zrn
    )
    for record in "${ordered[@]}"; do
        path="${record#* }"
        [[ "$path" == "$root/"* ]] || {
            echo "ERROR: unsafe upgrade snapshot rotation path." >&2
            exit 3
        }
        name="${path##*/}"
        [[ "$name" =~ ^[0-9]{8}-[0-9]{6}-[A-Za-z0-9][A-Za-z0-9._-]{0,63}-[A-Za-z0-9]{8}$ ]] || continue
        [[ -f "$path/source.tar.gz" && -f "$path/source.tar.gz.sha256" && -f "$path/manifest.env" ]] || continue
        mapfile -d '' -t sqlite_archives < <(
            find "$path" -maxdepth 1 -type f \
                -name 'voltage-*.sqlite3.gz' -print0
        )
        [[ ${#sqlite_archives[@]} -eq 1 ]] || continue
        snapshot_artifact_verified "$path/source.tar.gz" || continue
        snapshot_artifact_verified "${sqlite_archives[0]}" || continue
        python3 "$SOURCE/scripts/validate-source-archive.py" \
            "$path/source.tar.gz" >/dev/null 2>&1 || continue
        python3 "$SOURCE/scripts/validate-sqlite-archive.py" \
            --archive "${sqlite_archives[0]}" >/dev/null 2>&1 || continue
        snapshots+=("$path")
    done
    for index in "${!snapshots[@]}"; do
        (( index >= 5 )) || continue
        rm -rf -- "${snapshots[index]}"
    done
}

snapshot_upgrade() {
    [[ -d "$TARGET" && -f "$TARGET/VERSION" ]] || return 0
    # Even an in-place reinstall may rebuild the image and replace state.
    # Always preserve source and exact SQLite history before deployment.
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
    # Do not change Docker packages without a restorable source image.
    python3 "$SOURCE/scripts/validate-source-archive.py" \
        "$snapshot/source.tar.gz"
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

await_signed_ready() {
    local code consecutive=0
    # Never accept a merely responsive HTTP server as a ready meter.
    for _ in $(seq 1 60); do
        code="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:8080/ready 2>/dev/null || true)"
        if [[ "$code" == 200 ]]; then
            (( consecutive += 1 ))
        else
            consecutive=0
        fi
        if [[ "$consecutive" -ge 3 ]]; then
            python3 "$TARGET/scripts/deployment-state-audit.py" \
                --root "$TARGET"
            return
        fi
        sleep 2
    done
    echo "ERROR: signed image did not achieve stable Shelly readiness." >&2
    return 1
}

deploy() {
    if [[ "$DRY_RUN" == true ]]; then
        if [[ "$SIGNED_DEPLOYMENT" == true ]]; then
            echo "DRY-RUN: preserve exact signed image without Docker build"
        else
            echo "DRY-RUN: docker compose build/start and health verification"
        fi
        return
    fi
    cd "$TARGET"
    # Never rebuild or silently replace an immutable signed GHCR image.
    if [[ "$SIGNED_DEPLOYMENT" == true ]]; then
        bash "$TARGET/scripts/start-compose.sh"
    else
        bash "$TARGET/scripts/start-compose.sh" --build
    fi
    systemctl start shelly-voltage-compose.service shelly-voltage-backup.timer
    if [[ "$SIGNED_DEPLOYMENT" == true ]]; then
        docker update --restart=unless-stopped shelly-voltage-bot >/dev/null
        await_signed_ready
        return
    fi
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
    require_valid_install_marker
    require_safe_fresh_database_state
    require_quiesced_upgrade
    signed_image_preflight
    snapshot_upgrade
    install_docker
    copy_source
    prepare_state
    configure_project
    install_units
    deploy
    summary
}

main "$@"
