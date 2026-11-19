#!/usr/bin/env bash
# Preserve approved YASNO read-only opt-in across start/update modes.
set -euo pipefail

ROOT="$(printenv SVT_ROOT || printf /opt/shelly-voltage)"
OVERRIDE="$(printenv SVT_UPDATE_OVERRIDE || printf /var/lib/shelly-voltage-updater/override.yaml)"
YASNO_OVERLAY="$(printenv SVT_YASNO_READER_OVERLAY || printf /etc/shelly-voltage/yasno-reader.yaml)"
SUPERVISOR_OVERLAY="$(printenv SVT_SUPERVISOR_OVERLAY || true)"

usage() {
    echo "Usage: start-compose.sh [--build|--create-only|-h|--help]"
    echo "Optional YASNO overlay must be an owner-controlled private file."
}

fail_overlay() {
    echo "ERROR: unsafe YASNO overlay path, ownership or permissions." >&2
    exit 3
}

validate_yasno_overlay() {
    local file="$1" part owner mode
    [[ "$file" == /* ]] || fail_overlay
    case "$file" in
        *"//"*|*"/./"*|*"/../"*|*"/."|*"/..") fail_overlay ;;
    esac
    [[ -f "$file" && ! -L "$file" ]] || fail_overlay
    [[ "$(stat -c %h -- "$file")" -eq 1 ]] || fail_overlay

    part="$file"
    while [[ "$part" != "/" ]]; do
        [[ ! -L "$part" ]] || fail_overlay
        owner="$(stat -c %u -- "$part")"
        mode="$(stat -c %a -- "$part")"
        if [[ "$part" == "$file" ]]; then
            [[ "$owner" -eq "$EUID" ]] || fail_overlay
        else
            [[ "$owner" -eq "$EUID" || "$owner" -eq 0 ]] || fail_overlay
        fi
        if (( (8#$mode & 0022) != 0 )); then
            # Root-owned /tmp is sticky; other writable parents fail.
            if [[ "$part" != /tmp || "$owner" -ne 0 ]] ||
               (( (8#$mode & 01000) == 0 )); then
                fail_overlay
            fi
        fi
        part="$(dirname -- "$part")"
    done
}

compose_cmd() {
    local -a layers=(-f "$ROOT/compose.yaml")
    [[ "$pinned" == true ]] && layers+=(-f "$OVERRIDE")
    [[ "$has_supervisor" == true ]] && layers+=(-f "$SUPERVISOR_OVERLAY")
    [[ "$has_yasno" == true ]] && layers+=(-f "$YASNO_OVERLAY")
    docker compose "${layers[@]}" "$@"
}

compose_prepare() {
    # Compute the same secure layers for start, install and rollback.
    local build="$1"
    pinned=false
    has_yasno=false
    has_supervisor=false
    if [[ "$build" == false && -f "$OVERRIDE" ]]; then
        pinned=true
    fi
    if [[ -n "$SUPERVISOR_OVERLAY" ]]; then
        validate_yasno_overlay "$SUPERVISOR_OVERLAY"
        has_supervisor=true
    fi
    if [[ -e "$YASNO_OVERLAY" || -L "$YASNO_OVERLAY" ]]; then
        validate_yasno_overlay "$YASNO_OVERLAY"
        has_yasno=true
    fi
}

start_compose_main() {
    local build=false create_only=false
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --build) build=true ;;
            --create-only) create_only=true ;;
            -h|--help) usage; return 0 ;;
            *) usage >&2; return 2 ;;
        esac
    fi
    [[ $# -le 1 ]] || { usage >&2; return 2; }

    compose_prepare "$build"
    if [[ "$has_supervisor" == true && "$create_only" != true ]]; then
        echo "FAIL: supervisor must own startup; use --create-only" >&2
        return 3
    fi
    if [[ "$create_only" == true ]]; then
        if [[ "$pinned" != true || "$has_supervisor" != true ]]; then
            echo "FAIL: create-only needs a signed pin and supervisor overlay" >&2
            return 3
        fi
        compose_cmd config -q
        compose_cmd create --no-build --no-recreate
        return
    fi
    compose_cmd config -q
    if [[ "$build" == true ]]; then
        compose_cmd up -d --build
    elif [[ "$pinned" == true ]]; then
        compose_cmd up -d --no-build
    else
        compose_cmd up -d
    fi
}

# When rollback sources this file, retain the checked Compose plan
# in shell memory while old release sources are being restored.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    start_compose_main "$@"
fi
