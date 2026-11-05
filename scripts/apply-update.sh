#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="${SVT_ROOT:-/opt/shelly-voltage}"
STATE="${SVT_UPDATE_STATE:-/var/lib/shelly-voltage-updater}"
DB="${DATABASE_PATH:-/var/lib/shelly-voltage/voltage.sqlite3}"
IMAGE="ghcr.io/isentinel/shelly-voltage-telegram"
VERSION=""
DRY_RUN=false
SNAPSHOT=""
CHANGED=false

usage() {
    cat <<'EOF'
Usage: apply-update.sh --version 0.5.13-hw [--dry-run] [-h|--help]
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

requirements() {
    [[ $EUID -eq 0 ]] || { echo "FAIL: root required" >&2; exit 2; }
    for name in python3 cosign docker flock curl tar; do
        command -v "$name" >/dev/null || {
            echo "FAIL: missing $name" >&2; exit 2;
        }
    done
    docker compose version >/dev/null
    mkdir -p "$STATE/snapshots"
    chmod 0700 "$STATE" "$STATE/snapshots"
    exec 9>/run/lock/shelly-voltage-updater.lock
    flock -n 9 || { echo "FAIL: update already running" >&2; exit 3; }
}

preflight() {
    python3 "$ROOT/scripts/github_update.py" preflight --database "$DB"
}

verify_image() {
    local target="$IMAGE@$1"
    cosign verify \
        --certificate-identity "https://github.com/iSentinel/shelly-voltage-telegram/.github/workflows/image.yml@refs/tags/v$VERSION" \
        --certificate-oidc-issuer https://token.actions.githubusercontent.com \
        "$target" >/dev/null
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
    local code
    for _ in $(seq 1 25); do
        code="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:8080/health || true)"
        [[ "$code" == 200 ]] && return 0
        sleep 2
    done
    return 1
}

start_stack() {
    "$ROOT/scripts/start-compose.sh"
}

rollback() {
    local exit_code=$?
    trap - ERR
    if [[ "$CHANGED" == true && -n "$SNAPSHOT" ]]; then
        echo "ROLLBACK: restoring previous image selection" >&2
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
        start_stack || echo "FAIL: manual intervention required" >&2
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
    mv -f "$tmp" "$STATE/override.yaml"
    CHANGED=true
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
    parse_args "$@"
    requirements
    preflight
    local digest
    digest="$(python3 "$ROOT/scripts/github_update.py" digest \
        --version "$VERSION")"
    verify_image "$digest"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN PASS: signed $IMAGE@$digest, no changes"
        return
    fi
    docker pull "$IMAGE@$digest"
    preflight
    snapshot
    trap rollback ERR
    activate "$digest"
    trap - ERR
    echo "UPDATE PASS: $VERSION"
}

main "$@"
