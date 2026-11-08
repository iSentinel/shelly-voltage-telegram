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
! grep -q '/ROADMAP.md$' "$LIST"
! grep -q '/.work-state/' "$LIST"
! grep -q '/.roadmap-update.lock$' "$LIST"
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
for file in VERSION RELEASE_VERSION LIVE_VERSION app.py advanced_features.py apartment_phases.py update_info.py telegram_i18n.py notification_features.py dtek_schedule.py outage_data_ua.py yasno_schedule.py yasno_cache.py yasno_handoff.py telegram_channel_yasno.py telegram_widget_data.py telegram_widget_fallback.py Dockerfile compose.yaml requirements.txt README.md .env.example; do
    cp "$PROJECT/$file" "$FIXTURE/$file"
done
printf "%s\n" "SHELLY_HOST=private.invalid" > "$FIXTURE/.env"
printf "%s\n" "TELEGRAM_BOT_TOKEN=fake-private-value" > "$FIXTURE/.env.pre-ip-fix-20261104"
printf "%s\n" "SHELLY_PASSWORD=fake-private-value" > "$FIXTURE/.env.bak"
printf "%s\n" "PRIVATE_CREDENTIAL=fake-value" > "$FIXTURE/nested/.env.secret"
printf "%s\n" "fake-private-value" > "$FIXTURE/secrets/token"
printf "%s\n" "sensitive incident timeline" > "$FIXTURE/ROADMAP.md"
printf "%s\n" "private lock" > "$FIXTURE/.roadmap-update.lock"
printf "%s\n" "private lock" > "$FIXTURE/.telegram-private-acceptance.lock"
mkdir -p "$FIXTURE/.work-state"
printf "%s\n" "synthetic_private" > "$FIXTURE/.work-state/firmware-progress.json"
printf "%s\n" "synthetic_private" > "$FIXTURE/nested/identity.pem"
printf "%s\n" "synthetic_private" > "$FIXTURE/nested/owner.key"
printf "%s\n" "synthetic_private" > "$FIXTURE/nested/private.token"
printf "%s\n" "synthetic_private" > "$FIXTURE/nested/private.secret"
"$FIXTURE/scripts/release.sh" --output-dir "$TMP/private-dist" --source-date-epoch "$EPOCH" > "$TMP/private-release.log"
tar -tzf "$TMP/private-dist/$NAME.tar.gz" > "$TMP/private-list.txt"
grep -q "/.env.example$" "$TMP/private-list.txt"
if grep -E "/\\.env" "$TMP/private-list.txt" | grep -v "/\\.env.example$"; then
    echo "ERROR: local .env backup leaked into release archive" >&2
    exit 1
fi
! grep -q "/secrets/" "$TMP/private-list.txt"
if grep -E '(^|/)(ROADMAP[.]md|[.]roadmap-update[.]lock|[.]telegram-private-acceptance[.]lock)(/|$)|(^|/)[.]work-state/|[.](pem|key|token|secret)$' "$TMP/private-list.txt"; then
    echo "ERROR: local operator evidence entered release archive" >&2
    exit 1
fi
echo "RELEASE_PRIVATE_ENV_EXCLUSION PASS"

# The final release must not bundle a link into private runtime files.
ln -s '.env' "$FIXTURE/linked-env"
ln -s 'secrets' "$FIXTURE/linked-private-directory"
if "$FIXTURE/scripts/release.sh" --output-dir "$TMP/linked-dist" \
    --source-date-epoch "$EPOCH" >"$TMP/linked-release.log" 2>&1; then
    echo "ERROR: final release accepted symbolic links" >&2
    exit 1
fi
grep -q "ERROR: public release cannot contain symbolic links" \
    "$TMP/linked-release.log"
test ! -e "$TMP/linked-dist/$NAME.tar.gz"
echo "RELEASE_SYMLINKED_PRIVATE_PATHS=REJECTED"

# Untrusted VERSION must never insert slash or path traversal into archive names.
printf "%s\n" "0.5.14-hw/../../outside" > "$FIXTURE/VERSION"
if "$FIXTURE/scripts/release.sh" --dry-run \
    --output-dir "$TMP/version-dist" > "$TMP/invalid-version.log" 2>&1; then
    echo "ERROR: release dry-run accepted unsafe version" >&2
    exit 1
fi
grep -q "ERROR: VERSION must be a release version" \
    "$TMP/invalid-version.log"
test ! -e "$TMP/version-dist"
echo "RELEASE_INVALID_VERSION=REJECTED"


# Private directories at arbitrary depth must never enter public archives.
# Remove links deliberately kept by the preceding negative fixture.
rm -f "$FIXTURE/linked-env" "$FIXTURE/linked-private-directory"
cp "$PROJECT/VERSION" "$FIXTURE/VERSION"
mkdir -p "$FIXTURE/nested/deeper/secrets" \
    "$FIXTURE/nested/deeper/validation" \
    "$FIXTURE/nested/deeper/dist-publish" \
    "$FIXTURE/nested/deeper/.work-state"
printf "%s\n" "synthetic_nested_private" \
    > "$FIXTURE/nested/deeper/secrets/notes.txt"
printf "%s\n" "synthetic_nested_qa" \
    > "$FIXTURE/nested/deeper/validation/evidence.json"
printf "%s\n" "synthetic_nested_output" \
    > "$FIXTURE/nested/deeper/dist-publish/output.tar.gz"
printf "%s\n" "synthetic_nested_checkpoint" \
    > "$FIXTURE/nested/deeper/.work-state/checkpoint.json"
"$FIXTURE/scripts/release.sh" \
    --output-dir "$TMP/nested-private-dist" \
    --source-date-epoch "$EPOCH" \
    > "$TMP/nested-private-release.log"
tar -tzf "$TMP/nested-private-dist/$NAME.tar.gz" \
    > "$TMP/nested-private-list.txt"
if grep -E '/(secrets|validation|dist-publish|[.]work-state)/' \
    "$TMP/nested-private-list.txt"; then
    echo "ERROR: nested private directory leaked into public release" >&2
    exit 1
fi
echo "RELEASE_NESTED_PRIVATE_PATHS=EXCLUDED"


# A linked template is dereferenced by cp, so staging-only link checks miss it.
rm -f "$FIXTURE/.env.example"
ln -s "README.md" "$FIXTURE/.env.example"
if "$FIXTURE/scripts/release.sh" \
    --output-dir "$TMP/linked-template-dist" \
    --source-date-epoch "$EPOCH" > "$TMP/linked-template.log" 2>&1; then
    echo "ERROR: release accepted a linked env example template" >&2
    exit 1
fi
grep -q "ERROR: public release template must be an independent regular file" \
    "$TMP/linked-template.log"
test ! -e "$TMP/linked-template-dist/$NAME.tar.gz"
echo "RELEASE_LINKED_ENV_TEMPLATE=REJECTED"

# A hard-linked template has the same regular-file type as its source.
rm -f "$FIXTURE/.env.example"
ln "$FIXTURE/README.md" "$FIXTURE/.env.example"
if "$FIXTURE/scripts/release.sh" \
    --output-dir "$TMP/hardlinked-template-dist" \
    --source-date-epoch "$EPOCH" > "$TMP/hardlinked-template.log" 2>&1; then
    echo "ERROR: release accepted a hard-linked env example template" >&2
    exit 1
fi
grep -q "ERROR: public release template must be an independent regular file" \
    "$TMP/hardlinked-template.log"
test ! -e "$TMP/hardlinked-template-dist/$NAME.tar.gz"
echo "RELEASE_HARDLINKED_ENV_TEMPLATE=REJECTED"
