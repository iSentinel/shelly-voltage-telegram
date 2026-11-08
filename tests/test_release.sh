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

# A local credential backup must never enter a public release archive.
FIXTURE="$TMP/private-source"
mkdir -p "$FIXTURE/scripts" "$FIXTURE/secrets" "$FIXTURE/nested"
cp "$PROJECT/scripts/release.sh" "$FIXTURE/scripts/release.sh"
for file in VERSION RELEASE_VERSION LIVE_VERSION app.py advanced_features.py apartment_phases.py update_info.py telegram_i18n.py notification_features.py dtek_schedule.py outage_data_ua.py yasno_schedule.py yasno_cache.py telegram_channel_yasno.py telegram_widget_data.py telegram_widget_fallback.py Dockerfile compose.yaml requirements.txt README.md .env.example; do
    cp "$PROJECT/$file" "$FIXTURE/$file"
done
printf "%s\n" "SHELLY_HOST=private.invalid" > "$FIXTURE/.env"
printf "%s\n" "TELEGRAM_BOT_TOKEN=fake-private-value" > "$FIXTURE/.env.pre-ip-fix-20261104"
printf "%s\n" "SHELLY_PASSWORD=fake-private-value" > "$FIXTURE/.env.bak"
printf "%s\n" "PRIVATE_CREDENTIAL=fake-value" > "$FIXTURE/nested/.env.secret"
printf "%s\n" "fake-private-value" > "$FIXTURE/secrets/token"
"$FIXTURE/scripts/release.sh" --output-dir "$TMP/private-dist" --source-date-epoch "$EPOCH" > "$TMP/private-release.log"
tar -tzf "$TMP/private-dist/$NAME.tar.gz" > "$TMP/private-list.txt"
grep -q "/.env.example$" "$TMP/private-list.txt"
if grep -E "/\\.env" "$TMP/private-list.txt" | grep -v "/\\.env.example$"; then
    echo "ERROR: local .env backup leaked into release archive" >&2
    exit 1
fi
! grep -q "/secrets/" "$TMP/private-list.txt"
echo "RELEASE_PRIVATE_ENV_EXCLUSION PASS"
