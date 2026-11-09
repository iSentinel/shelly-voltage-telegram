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

validate_yasno_overlay() {
    local file="$1" parent
    [[ "$file" == /* && "$file" != */.. && "$file" != */. ]] || {
        echo "ERROR: YASNO overlay path is not absolute and safe." >&2
        exit 3
    }
    parent="$(dirname -- "$file")"
    if [[ -L "$file" || ! -f "$file" || -L "$parent" ]] ||
       [[ "$(stat -c %h "$file")" -ne 1 ]] ||
       [[ "$(stat -c %u "$file")" -ne "$EUID" ]] ||
       (( (8#$(stat -c %a "$file") & 0022) != 0 )) ||
       (( (8#$(stat -c %a "$parent") & 0022) != 0 )); then
        echo "ERROR: unsafe YASNO overlay permissions or file type." >&2
        exit 3
    fi
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

build=false
if [[ $# -gt 0 ]]; then
    case "$1" in
        --build) build=true ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
fi
[[ $# -le 1 ]] || { usage >&2; exit 2; }

pinned=false
has_yasno=false
if [[ "$build" == false && -f "$OVERRIDE" ]]; then
    pinned=true
fi
if [[ -e "$YASNO_OVERLAY" || -L "$YASNO_OVERLAY" ]]; then
    validate_yasno_overlay "$YASNO_OVERLAY"
    has_yasno=true
fi

compose_cmd config -q
if [[ "$build" == true ]]; then
    compose_cmd up -d --build
elif [[ "$pinned" == true ]]; then
    compose_cmd up -d --no-build
else
    compose_cmd up -d
fi
