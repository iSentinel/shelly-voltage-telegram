#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
A="$TMP/a"
B="$TMP/b"
EXTRACT="$TMP/extract"
EPOCH=1793440000
NAME="shelly-voltage-telegram-$(tr -d '\r\n' < "$PROJECT/VERSION")"

"$PROJECT/scripts/release.sh" \
    --output-dir "$A" --source-date-epoch "$EPOCH"
"$PROJECT/scripts/release.sh" \
    --output-dir "$B" --source-date-epoch "$EPOCH"

ARCHIVE_A="$A/$NAME.tar.gz"
ARCHIVE_B="$B/$NAME.tar.gz"
cmp -s "$ARCHIVE_A" "$ARCHIVE_B"
(
    cd "$A"
    sha256sum -c SHA256SUMS
)

LIST="$TMP/list.txt"
tar -tzf "$ARCHIVE_A" > "$LIST"
FORBIDDEN="$(
    grep -E \
        '(^|/)\.env$|(^|/)\.github-main-sha$|(^|/)secrets/|(^|/)__pycache__/|(^|/)\.(ruff_cache|pytest_cache|mypy_cache)/|(^|/)dist-publish/|(^|/)validation/|(^|/)ROADMAP\.md\.pre-|\.py[co]$|\.sqlite(3)?([.-]|$)|\.log$' \
        "$LIST" || true
)"
[[ -z "$FORBIDDEN" ]] || {
    echo "ERROR: forbidden release entries:" >&2
    printf '%s\n' "$FORBIDDEN" >&2
    exit 1
}
grep -q '/.env.example$' "$LIST"
! grep -q '/ROADMAP.md.pre-' "$LIST"
! grep -q '/validation/' "$LIST"
! grep -q '/dist-publish/' "$LIST"
! grep -q '/.ruff_cache/' "$LIST"
! grep -q '/.pytest_cache/' "$LIST"
! grep -q '/.mypy_cache/' "$LIST"
! grep -q '/.github-main-sha$' "$LIST"

mkdir -p "$EXTRACT"
tar -xzf "$ARCHIVE_A" -C "$EXTRACT"
RELEASE="$EXTRACT/$NAME"
(
    cd "$RELEASE"
    sha256sum -c FILES.SHA256
    test "$(cat VERSION)" = "$(cat "$PROJECT/VERSION")"
    test "$(cat RELEASE_VERSION)" = "$(cat "$PROJECT/RELEASE_VERSION")"
    test -f .env.example
    test ! -e .env
    test ! -e secrets
)

echo "RELEASE_REPRODUCIBILITY PASS"
