#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

[[ ${EUID} -eq 0 ]] || {
    echo "ERROR: test_install_dry_run.sh must run as root." >&2
    exit 2
}

fresh="$TMP/fresh-root"
if [[ -e /var/lib/shelly-voltage/voltage.sqlite3 ||
      -L /var/lib/shelly-voltage/voltage.sqlite3 ||
      -e /var/lib/shelly-voltage/voltage.sqlite3-wal ||
      -L /var/lib/shelly-voltage/voltage.sqlite3-wal ||
      -e /var/lib/shelly-voltage/voltage.sqlite3-shm ||
      -L /var/lib/shelly-voltage/voltage.sqlite3-shm ||
      -e /var/lib/shelly-voltage/voltage.sqlite3-journal ||
      -L /var/lib/shelly-voltage/voltage.sqlite3-journal ]]; then
    # On a live host, a fresh root must not silently reuse SQLite history.
    if SVT_ROOT="$fresh" "$PROJECT/scripts/install.sh"         --dry-run --non-interactive > "$TMP/fresh.log" 2>&1; then
        echo "ERROR: shared SQLite history accepted by fresh installer." >&2
        exit 1
    fi
    grep -q "existing Shelly SQLite state" "$TMP/fresh.log"
    echo "FRESH_INSTALL_WITH_EXISTING_DB=BLOCKED"
else
    SVT_ROOT="$fresh" "$PROJECT/scripts/install.sh"         --dry-run --non-interactive > "$TMP/fresh.log" 2>&1
    grep -q "DRY-RUN PASS: no changes applied." "$TMP/fresh.log"
    echo "FRESH_INSTALL_DRY_RUN=PASS"
fi
[[ ! -e "$fresh" ]]

# GitHub checkout belongs to runner, not root: use an isolated
# root-owned in-place tree instead of weakening the safety guard.
in_place="$TMP/in-place-root"
mkdir -p "$in_place/scripts"
cp "$PROJECT/scripts/install.sh" "$in_place/scripts/install.sh"
cp "$PROJECT/VERSION" "$in_place/VERSION"
SVT_ROOT="$in_place" "$in_place/scripts/install.sh" \
    --dry-run --non-interactive > "$TMP/in-place.log" 2>&1
grep -q "DRY-RUN PASS: no changes applied." "$TMP/in-place.log"
echo "IN_PLACE_INSTALL_DRY_RUN=PASS"


missing_meter="$TMP/missing-meter"
mkdir -p "$missing_meter"
cp "$PROJECT/.env.example" "$missing_meter/.env.example"
grep -v '^SHELLY_METER_TYPE=' "$PROJECT/.env.example" > "$missing_meter/.env"
SVT_ROOT="$missing_meter" "$PROJECT/scripts/configure.sh" \
    --non-interactive > "$TMP/missing-meter.log" 2>&1
grep -q '^SHELLY_METER_TYPE=pro_em50$' "$missing_meter/.env"
echo "MISSING_METER_TYPE_FALLBACK=PASS"

blank_meter="$TMP/blank-meter"
mkdir -p "$blank_meter"
cp "$PROJECT/.env.example" "$blank_meter/.env.example"
sed 's/^SHELLY_METER_TYPE=.*/SHELLY_METER_TYPE=/' \
    "$PROJECT/.env.example" > "$blank_meter/.env"
SVT_ROOT="$blank_meter" "$PROJECT/scripts/configure.sh" \
    --non-interactive > "$TMP/blank-meter.log" 2>&1
grep -q '^SHELLY_METER_TYPE=pro_em50$' "$blank_meter/.env"
echo "BLANK_METER_TYPE_FALLBACK=PASS"
