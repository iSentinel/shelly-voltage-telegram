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
       apply-update.sh --recover  (systemd boot recovery only)
Root-only, explicitly approved update of a published, signed GHCR image.
Never runs automatically; refuse when Shelly is offline or an outage is open.
Requires cosign, docker compose, Python 3 and flock.
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
    exec 9>/run/lock/shelly-voltage-updater.lock
    flock -n 9 || { echo "FAIL: update already running" >&2; exit 3; }
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
    "$COSIGN_EXECUTABLE" verify \
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
    mkdir -p "$PUBLIC_STATE"
    if [[ -f "$STATE/deployed.json" ]]; then
        install -m 0644 "$STATE/deployed.json" "$PUBLIC_STATE/deployed.json"
    else
        rm -f "$PUBLIC_STATE/deployed.json"
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

recover_pending() {
    local token
    [[ -f "$PENDING_FILE" ]] || return 0
    token="$(cat "$PENDING_FILE")"
    if [[ ! "$token" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+\.[0-9]+\.[0-9]+-hw$ ]]; then
        echo "FAIL: invalid pending update marker" >&2
        return 1
    fi
    SNAPSHOT="$STATE/snapshots/$token"
    [[ -d "$SNAPSHOT" ]] || return 1
    echo "RECOVERY: interrupted update $token" >&2
    docker compose -f "$ROOT/compose.yaml" stop shelly-voltage || return 1
    if [[ -f "$SNAPSHOT/override.yaml" ]]; then
        cp -a "$SNAPSHOT/override.yaml" "$STATE/override.yaml"
    else
        rm -f "$STATE/override.yaml"
    fi
    if [[ -f "$SNAPSHOT/deployed.json" ]]; then
        cp -a "$SNAPSHOT/deployed.json" "$STATE/deployed.json"
    else
        rm -f "$STATE/deployed.json"
    fi
    restore_snapshot_database
    sync_public_version
    rm -f "$PENDING_FILE"
}

restore_snapshot_database() {
    local backup rescue temp
    backup="$(find "$SNAPSHOT" -maxdepth 1 -name 'voltage-*.sqlite3.gz' -print -quit)"
    [[ -n "$backup" ]] || return 1
    sha256sum -c "$backup.sha256" || return 1
    docker compose -f "$ROOT/compose.yaml" stop shelly-voltage || return 1
    rescue="$SNAPSHOT/failed-upgrade-database.sqlite3"
    if [[ -f "$DB" && ! -e "$rescue" ]]; then
        python3 - "$DB" "$rescue" <<'PY'
import sqlite3
import sys
from contextlib import closing

source = "file:" + sys.argv[1] + "?mode=ro"
with closing(sqlite3.connect(source, uri=True)) as original:
    with closing(sqlite3.connect(sys.argv[2])) as destination:
        original.backup(destination)
PY
        chmod 0600 "$rescue"
    fi
    temp="$(mktemp "${DB}.recovery.XXXXXXXX")"
    if ! gzip -dc "$backup" > "$temp"; then
        rm -f "$temp"
        return 1
    fi
    python3 - "$temp" <<'PY'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as db:
    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise SystemExit("invalid rollback database")
PY
    chown --reference="$DB" "$temp"
    chmod --reference="$DB" "$temp"
    rm -f "$DB-wal" "$DB-shm"
    mv -f "$temp" "$DB"
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

main() {
    if [[ "${1:-}" == "--recover" && $# -eq 1 ]]; then
        [[ $EUID -eq 0 ]] || return 2
        mkdir -p "$STATE"
        exec 9>/run/lock/shelly-voltage-updater.lock
        flock -w 120 9 || return 3
        recover_pending
        return
    fi
    parse_args "$@"
    requirements
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
    sync_public_version || echo "WARN: version status update failed" >&2
    echo "UPDATE PASS: $VERSION"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
