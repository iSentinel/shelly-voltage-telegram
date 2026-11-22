#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="${SVT_ROOT:-/opt/shelly-voltage}"
STATE="${SVT_UPDATE_STATE:-/var/lib/shelly-voltage-updater}"
DB="${DATABASE_PATH:-/var/lib/shelly-voltage/voltage.sqlite3}"
IMAGE="ghcr.io/isentinel/shelly-voltage-telegram"
VERSION=""
COSIGN_EXECUTABLE=""
DRY_RUN=false
SNAPSHOT=""
CHANGED=false
PENDING_FILE="$STATE/pending-update"
UPDATE_LOCK="/run/lock/shelly-voltage-updater.lock"
PUBLIC_STATE="${SVT_PUBLIC_STATE:-/var/lib/shelly-voltage-update-check}"
UPDATE_WORKSPACE=""

cleanup_workspace() {
    if [[ -n "$UPDATE_WORKSPACE" && -d "$UPDATE_WORKSPACE" ]]; then
        rm -rf -- "$UPDATE_WORKSPACE"
    fi
}

usage() {
    cat <<'EOF'
Usage: apply-update.sh --version 0.5.13-hw [--dry-run] [-h|--help]
       apply-update.sh --recover-and-start  (systemd service entry point)
       apply-update.sh --recover
       apply-update.sh --sync-public-version
Root-only, explicitly approved update of a published, signed GHCR image.
Never runs automatically; refuse when Shelly is offline or an outage is open.
Requires cosign, docker compose, Python 3 and flock.
Exit 4 after activation means the new image is active but its version status
could not be confirmed. Retry only the version status with --sync-public-version.
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version) VERSION="${2:-}"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            -h|--help) usage; exit 0 ;;
            *) echo "FAIL: unknown argument $1" >&2; exit 2 ;;
        esac
    done
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+-hw$ ]] || {
        echo "FAIL: unsupported version" >&2; exit 2;
    }
}

check_cosign_compatibility() {
    local version major executable="${1:-cosign}"
    version="$("$executable" version 2>/dev/null | awk '/^GitVersion:/ {print $2}')"
    if [[ "$version" =~ ^v?([0-9]+)\. ]]; then
        major="${BASH_REMATCH[1]}"
    else
        echo "FAIL: unsupported Cosign version ($version); require v3+" >&2
        return 1
    fi
    if (( major < 3 )); then
        echo "FAIL: Cosign v3+ required for OCI signature verification" >&2
        return 1
    fi
}

select_cosign() {
    local candidate
    local -a choices=("$@")
    if [[ -n "${SVT_COSIGN_BIN:-}" ]]; then
        choices=("$SVT_COSIGN_BIN")
    elif [[ ${#choices[@]} -eq 0 ]]; then
        choices=(/usr/local/bin/cosign /usr/bin/cosign)
    fi
    for candidate in "${choices[@]}"; do
        if [[ "$candidate" == /* && -f "$candidate" && -x "$candidate" ]] &&
            check_cosign_compatibility "$candidate" >/dev/null 2>&1; then
            COSIGN_EXECUTABLE="$candidate"
            return 0
        fi
    done
    echo "FAIL: no supported Cosign v3 binary in trusted paths" >&2
    return 1
}

acquire_update_lock() {
    # Hold this descriptor until the caller's recovery or activation ends.
    exec 9>>"$UPDATE_LOCK" || return 3
    if ! flock "$@" 9; then
        echo "FAIL: updater or Compose start already running" >&2
        return 3
    fi
}

requirements() {
    [[ $EUID -eq 0 ]] || { echo "FAIL: root required" >&2; exit 2; }
    for name in python3 docker flock curl tar sha256sum gzip; do
        command -v "$name" >/dev/null || {
            echo "FAIL: missing $name" >&2; exit 2;
        }
    done
    docker compose version >/dev/null
    select_cosign
    mkdir -p "$STATE/snapshots"
    chmod 0700 "$STATE" "$STATE/snapshots"
    acquire_update_lock -n || exit 3
}

preflight() {
    python3 "$ROOT/scripts/github_update.py" preflight --database "$DB"
}

storage_preflight() {
    local docker_root
    docker_root="$(docker info --format '{{.DockerRootDir}}')"
    python3 "$ROOT/scripts/github_update.py" storage \
        --database "$DB" --state "$STATE" --docker-root "$docker_root"
}

verify_image() {
    local target="$IMAGE@$1"
    local -a verifier=("$COSIGN_EXECUTABLE")
    if [[ $# == 2 ]]; then
        verifier=(timeout "$2" "$COSIGN_EXECUTABLE")
    fi
    "${verifier[@]}" verify \
        --certificate-identity "https://github.com/iSentinel/shelly-voltage-telegram/.github/workflows/image.yml@refs/tags/v$VERSION" \
        --certificate-oidc-issuer https://token.actions.githubusercontent.com \
        "$target" >/dev/null
}

verify_source_archive() {
    local dir="$1" archive="shelly-voltage-telegram-$VERSION.tar.gz"
    local base="https://github.com/iSentinel/shelly-voltage-telegram/releases/download/v$VERSION"
    curl -fsSL --retry 2 --max-time 75 "$base/$archive" -o "$dir/$archive"
    curl -fsSL --retry 2 --max-time 30 "$base/SHA256SUMS" -o "$dir/SHA256SUMS"
    awk -v file="$archive" '$2 == file {print $1 "  " $2}' \
        "$dir/SHA256SUMS" > "$dir/SELECTED.SHA256"
    [[ $(wc -l < "$dir/SELECTED.SHA256") -eq 1 ]] || {
        echo "FAIL: release archive checksum missing or repeated" >&2
        return 1
    }
    (cd "$dir" && sha256sum -c SELECTED.SHA256)
}

snapshot() {
    SNAPSHOT="$STATE/snapshots/$(date -u +%Y%m%dT%H%M%SZ)-$VERSION"
    mkdir -m 0700 "$SNAPSHOT"
    cp -a "$ROOT/.env" "$SNAPSHOT/env"
    tar -C "$ROOT" -czf "$SNAPSHOT/secrets.tar.gz" secrets
    if [[ -f "$STATE/override.yaml" ]]; then
        cp -a "$STATE/override.yaml" "$SNAPSHOT/override.yaml"
    fi
    if [[ -f "$STATE/deployed.json" ]]; then
        cp -a "$STATE/deployed.json" "$SNAPSHOT/deployed.json"
    fi
    local container
    if container="$(docker inspect --format '{{.Id}}' shelly-voltage-bot 2>/dev/null)" &&
        [[ "$container" =~ ^[0-9a-f]{64}$ ]]; then
        printf '%s\n' "$container" > "$SNAPSHOT/container-id"
    fi
    DATABASE_PATH="$DB" BACKUP_DIR="$SNAPSHOT" BACKUP_KEEP=1 \
        /bin/bash "$ROOT/scripts/backup.sh" >"$SNAPSHOT/backup.log"
    echo "SNAPSHOT: $SNAPSHOT"
}

wait_healthy() {
    local code consecutive=0
    # A healthy HTTP server may still have stale Shelly data at startup.
    # Demand three consecutive /ready successes before marking update safe.
    for _ in $(seq 1 60); do
        code="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:8080/ready 2>/dev/null || true)"
        if [[ "$code" == 200 ]]; then
            (( consecutive += 1 ))
            [[ "$consecutive" -ge 3 ]] && return 0
        else
            consecutive=0
        fi
        sleep 2
    done
    echo "FAIL: Shelly /ready was not stable after the startup window" >&2
    return 1
}

start_stack() {
    /bin/bash "$ROOT/scripts/start-compose.sh"
}

sync_public_version() {
    # The private deployment receipt is authoritative. Readers always see
    # either the complete old receipt or the complete new receipt.
    /usr/bin/python3 -I - "$STATE/deployed.json" "$PUBLIC_STATE" <<'PY'
import os
import sys
import tempfile
from pathlib import Path


def sync_directory(path):
    """Persist file name changes in the containing directory."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


source, folder = map(Path, sys.argv[1:])
destination = folder / "deployed.json"
if not folder.is_dir():
    folder.mkdir(parents=True, exist_ok=True)
    sync_directory(folder.parent)
if source.exists():
    content = source.read_bytes()
    with tempfile.NamedTemporaryFile(dir=folder, prefix=".deployed-",
                                     delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            os.fchmod(file.fileno(), 0o644)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
else:
    destination.unlink(missing_ok=True)
sync_directory(folder)
PY
}

publish_committed_version() {
    if ! sync_public_version; then
        echo "FAIL: deployed version status could not be synchronized; retry --sync-public-version" >&2
        return 4
    fi
}

mark_pending() {
    local token temp
    token="$(basename "$SNAPSHOT")"
    temp="$(mktemp "$STATE/.pending.XXXXXXXX")"
    printf '%s\n' "$token" > "$temp"
    chmod 0600 "$temp"
    mv -f "$temp" "$PENDING_FILE"
    sync -f "$PENDING_FILE"
    sync -f "$STATE"
}

recovery_file_is_safe() {
    local mode
    [[ -f "$1" && ! -L "$1" && -O "$1" ]] || return 1
    [[ "$(stat -c %h -- "$1")" == 1 ]] || return 1
    mode="$(stat -c %a -- "$1")" || return 1
    (( (8#$mode & 022) == 0 ))
}

verify_recovery_history() {
    local archive manifest recorded actual
    local -a archives=("$SNAPSHOT"/voltage-*.sqlite3.gz)
    [[ ${#archives[@]} == 1 ]] || return 1
    archive="${archives[0]}"
    manifest="$archive.sha256"
    recovery_file_is_safe "$archive" || return 1
    recovery_file_is_safe "$manifest" || return 1
    [[ "$(stat -c %s -- "$manifest")" -le 8192 ]] || return 1
    [[ "$(stat -c %s -- "$archive")" -le 2147483648 ]] || return 1
    recorded="$(cat -- "$manifest")" || return 1
    actual="$(sha256sum -- "$archive")" || return 1
    [[ "$recorded" == "$actual" ]] || return 1
    /usr/bin/python3 -I -B "$ROOT/scripts/validate-sqlite-archive.py" \
        --archive "$archive" --compare-db "$DB" "$@"
}

require_recovery_history() {
    local result=0
    verify_recovery_history "$@" || result=$?
    if [[ "$result" != 0 ]]; then
        echo "FAIL: recovery archive does not match live SQLite or could not be verified; inspect pending update" >&2
    fi
    return "$result"
}

validate_recovery_snapshot() {
    local directory file
    for directory in "$STATE" "$STATE/snapshots" "$SNAPSHOT"; do
        if [[ ! -d "$directory" || -L "$directory" || ! -O "$directory" ]] ||
            [[ "$(stat -c %a -- "$directory")" != 700 ]]; then
            echo "FAIL: recovery snapshot directory is unsafe" >&2
            return 1
        fi
    done
    for file in override.yaml deployed.json; do
        if [[ -e "$SNAPSHOT/$file" || -L "$SNAPSHOT/$file" ]]; then
            recovery_file_is_safe "$SNAPSHOT/$file" || return 1
        fi
    done
}

restore_recovery_receipt() {
    local name="$1" temporary
    if [[ ! -f "$SNAPSHOT/$name" ]]; then
        rm -f -- "$STATE/$name"
        return
    fi
    temporary="$(mktemp "$STATE/.receipt.XXXXXXXX")" || return 1
    if ! install -m 0600 -- "$SNAPSHOT/$name" "$temporary" ||
        ! mv -fT -- "$temporary" "$STATE/$name"; then
        rm -f -- "$temporary"
        return 1
    fi
}

recover_pending() {
    local token
    if [[ ! -e "$PENDING_FILE" && ! -L "$PENDING_FILE" ]]; then
        # A previous update may have committed immediately before a shutdown.
        publish_committed_version || true
        return 0
    fi
    if ! recovery_file_is_safe "$PENDING_FILE" ||
        [[ "$(stat -c %s -- "$PENDING_FILE")" -gt 128 ]]; then
        echo "FAIL: unsafe pending update marker" >&2
        return 1
    fi
    token="$(cat -- "$PENDING_FILE")" || return 1
    if [[ ! "$token" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+\.[0-9]+\.[0-9]+-hw$ ]]; then
        echo "FAIL: invalid pending update marker" >&2
        return 1
    fi
    SNAPSHOT="$STATE/snapshots/$token"
    validate_recovery_snapshot || return 1
    echo "RECOVERY: interrupted update $token" >&2
    # Compare before stopping Docker and recheck any commits made during stop.
    require_recovery_history --retained-boot || return $?
    docker compose -f "$ROOT/compose.yaml" stop shelly-voltage || return 1
    require_recovery_history || return 1
    restore_recovery_receipt override.yaml || return 1
    restore_recovery_receipt deployed.json || return 1
    require_recovery_history || return 1
    # Persist the previous image selection before clearing the recovery journal.
    sync -f "$STATE" || return 1
    rm -f -- "$PENDING_FILE" || return 1
    sync -f "$STATE" || return 1
    # The committed receipt can be published again independently.
    publish_committed_version || true
}

require_no_pending_recovery() {
    if [[ -e "$PENDING_FILE" || -L "$PENDING_FILE" ]]; then
        echo "FAIL: update recovery is pending" >&2
        return 3
    fi
}

rollback() {
    local exit_code=$?
    [[ "$exit_code" -ne 0 ]] || exit_code=1
    trap - ERR INT TERM
    if [[ "$CHANGED" == true ]]; then
        if recover_pending; then
            start_stack || echo "FAIL: manual intervention required" >&2
        else
            echo "FAIL: recovery remains pending; do not start stack" >&2
        fi
    fi
    echo "FAIL: update aborted (exit $exit_code)" >&2
    exit "$exit_code"
}

activate() {
    local digest="$1" tmp
    tmp="$(mktemp "$STATE/.override.XXXXXX")"
    printf 'services:\n  shelly-voltage:\n    image: %s@%s\n' \
        "$IMAGE" "$digest" >"$tmp"
    chmod 0600 "$tmp"
    mark_pending
    CHANGED=true
    mv -f "$tmp" "$STATE/override.yaml"
    start_stack
    wait_healthy
    preflight
    python3 - "$STATE/deployed.json" "$VERSION" "$digest" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

destination = Path(sys.argv[1])
with tempfile.NamedTemporaryFile(
    mode="w", dir=destination.parent, delete=False
) as file:
    json.dump({"version": sys.argv[2], "digest": sys.argv[3]}, file)
    name = file.name
os.chmod(name, 0o600)
os.replace(name, destination)
PY
}

retained_boot_identity() {
    /usr/bin/python3 -I -B "$ROOT/scripts/retained-boot-preflight.py" \
        --root "$ROOT" --state "$STATE" --snapshot "$SNAPSHOT" --db "$DB"
}

wait_retained_health() {
    # The container health check accepts an offline Shelly during boot.
    local health
    for _ in $(seq 1 30); do
        health="$(docker inspect --format '{{.State.Health.Status}}' "$1")" ||
            return 2
        if [[ "$health" == healthy ]]; then
            return 0
        fi
        [[ "$health" == starting ]] || return 2
        sleep 2
    done
    return 2
}

start_retained_stack() {
    local identity version digest container result=0
    identity="$(retained_boot_identity)" || return 2
    read -r version digest container <<< "$identity"
    VERSION="$version"
    select_cosign || return 2
    verify_image "$digest" 60 || return 2
    # Repeat the checks after verification while the updater lock is held.
    verify_recovery_history --retained-boot || result=$?
    if [[ "$result" != 5 || "$(retained_boot_identity)" != "$identity" ]]; then
        echo "RETAINED BOOT BLOCKED: evidence changed" >&2
        return 2
    fi
    docker start "$container" >/dev/null || return 2
    if ! wait_retained_health "$container"; then
        echo "RECOVERY PENDING: retained container health could not be verified" >&2
        return 2
    fi
    echo "RECOVERY PENDING: started previously verified container with current SQLite"
}

recover_and_start() {
    local result
    if recover_pending; then
        require_no_pending_recovery || return $?
        start_stack
    else
        result=$?
        if [[ "$result" == 5 ]]; then
            start_retained_stack
        else
            return "$result"
        fi
    fi
}

run_recovery_command() {
    acquire_update_lock -w 120 || return $?
    mkdir -p "$STATE" || return 1
    case "$1" in
        --recover)
            recover_pending
            ;;
        --recover-and-start)
            recover_and_start
            ;;
        --sync-public-version)
            require_no_pending_recovery || return $?
            publish_committed_version
            ;;
    esac
}

apply_release() {
    parse_args "$@"
    requirements
    if [[ "$DRY_RUN" == false ]]; then
        require_no_pending_recovery || return $?
    fi
    preflight
    python3 "$ROOT/scripts/github_update.py" guard \
        --root "$ROOT" --state "$STATE" --version "$VERSION"
    local digest
    digest="$(python3 "$ROOT/scripts/github_update.py" digest \
        --version "$VERSION")"
    verify_image "$digest"
    UPDATE_WORKSPACE="$(mktemp -d)"
    trap cleanup_workspace EXIT
    verify_source_archive "$UPDATE_WORKSPACE"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN PASS: signed $IMAGE@$digest, no changes"
        return
    fi
    storage_preflight
    docker pull "$IMAGE@$digest"
    preflight
    storage_preflight
    snapshot
    trap rollback ERR INT TERM
    activate "$digest"
    rm -f "$PENDING_FILE"
    trap - ERR INT TERM
    if ! sync -f "$STATE"; then
        echo "FAIL: could not confirm the committed update state on disk" >&2
        return 4
    fi
    publish_committed_version || return $?
    echo "UPDATE PASS: $VERSION"
}

main() {
    if [[ $# -eq 1 ]]; then
        case "$1" in
            --recover|--recover-and-start|--sync-public-version)
                [[ $EUID -eq 0 ]] || return 2
                run_recovery_command "$1"
                return
                ;;
        esac
    fi
    apply_release "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
