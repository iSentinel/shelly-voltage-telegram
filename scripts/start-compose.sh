#!/usr/bin/env bash
# Preserve approved YASNO read-only opt-in across start/update modes.
set -euo pipefail

ROOT="$(printenv SVT_ROOT || printf /opt/shelly-voltage)"
OVERRIDE="$(printenv SVT_UPDATE_OVERRIDE || printf /var/lib/shelly-voltage-updater/override.yaml)"
YASNO_OVERLAY="$(printenv SVT_YASNO_READER_OVERLAY || printf /etc/shelly-voltage/yasno-reader.yaml)"

usage() {
    echo "Usage: start-compose.sh [--build|-h|--help]"
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
    if [[ "$pinned" == true && "$has_yasno" == true ]]; then
        docker compose -f "$ROOT/compose.yaml" -f "$OVERRIDE" -f "$YASNO_OVERLAY" "$@"
    elif [[ "$pinned" == true ]]; then
        docker compose -f "$ROOT/compose.yaml" -f "$OVERRIDE" "$@"
    elif [[ "$has_yasno" == true ]]; then
        docker compose -f "$ROOT/compose.yaml" -f "$YASNO_OVERLAY" "$@"
    else
        docker compose -f "$ROOT/compose.yaml" "$@"
    fi
}

compose_prepare() {
    # Compute the same secure layers for start, install and rollback.
    local build="$1"
    pinned=false
    has_yasno=false
    if [[ "$build" == false && -f "$OVERRIDE" ]]; then
        pinned=true
    fi
    if [[ -e "$YASNO_OVERLAY" || -L "$YASNO_OVERLAY" ]]; then
        validate_yasno_overlay "$YASNO_OVERLAY"
        has_yasno=true
    fi
}

start_compose_main() {
    local build=false
    if [[ $# -gt 0 ]]; then
        case "$1" in
            --build) build=true ;;
            -h|--help) usage; return 0 ;;
            *) usage >&2; return 2 ;;
        esac
    fi
    [[ $# -le 1 ]] || { usage >&2; return 2; }

    compose_prepare "$build"
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
