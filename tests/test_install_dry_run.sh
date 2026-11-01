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
SVT_ROOT="$fresh" "$PROJECT/scripts/install.sh"     --dry-run --non-interactive > "$TMP/fresh.log" 2>&1
grep -q "DRY-RUN PASS: no changes applied." "$TMP/fresh.log"
[[ ! -e "$fresh" ]]
echo "FRESH_INSTALL_DRY_RUN=PASS"

SVT_ROOT="$PROJECT" "$PROJECT/scripts/install.sh"     --dry-run --non-interactive > "$TMP/in-place.log" 2>&1
grep -q "DRY-RUN PASS: no changes applied." "$TMP/in-place.log"
echo "IN_PLACE_INSTALL_DRY_RUN=PASS"
