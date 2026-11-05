#!/usr/bin/env bash
set -euo pipefail

ROOT="${SVT_ROOT:-/opt/shelly-voltage}"
OVERRIDE="${SVT_UPDATE_OVERRIDE:-/var/lib/shelly-voltage-updater/override.yaml}"

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
    echo "Usage: start-compose.sh [-h|--help]"
    echo "Start the pinned update image when configured, otherwise local Compose."
    exit 0
fi

if [[ -f "$OVERRIDE" ]]; then
    exec docker compose -f "$ROOT/compose.yaml" -f "$OVERRIDE" up -d --no-build
fi
exec docker compose -f "$ROOT/compose.yaml" up -d
