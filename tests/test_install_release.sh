#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
VERSION="$(tr -d '\r\n' < "$PROJECT/VERSION")"
ARCHIVE="shelly-voltage-telegram-${VERSION}.tar.gz"

build_good_assets() {
    local target="$1"
    mkdir -p "$target"
    "$PROJECT/scripts/release.sh" \
        --output-dir "$target" --source-date-epoch 1793447306 >/dev/null
}

run_release_installer() {
    local target="$1" output="$2"
    REPO=test/repository VERSION="$VERSION" \
    RELEASE_BASE_URL="file://$target" \
        "$PROJECT/scripts/install-release.sh" \
        --dry-run --non-interactive >"$output" 2>&1
}

expect_failure() {
    local target="$1" pattern="$2" output="$3"
    if run_release_installer "$target" "$output"; then
        echo "ERROR: unsafe release unexpectedly passed" >&2
        exit 1
    fi
    grep -q "$pattern" "$output"
}

make_wrong_root() {
    local target="$1"
    mkdir -p "$target/build/wrong-root/scripts"
    printf '#!/usr/bin/env bash\nexit 0\n' \
        > "$target/build/wrong-root/scripts/install.sh"
    chmod 0755 "$target/build/wrong-root/scripts/install.sh"
    tar -C "$target/build" -czf "$target/$ARCHIVE" wrong-root
    (cd "$target" && sha256sum "$ARCHIVE" > SHA256SUMS)
    rm -rf "$target/build"
}

make_link_archive() {
    local target="$1" release_root
    release_root="shelly-voltage-telegram-${VERSION}"
    mkdir -p "$target/build/$release_root/scripts"
    printf '#!/usr/bin/env bash\nexit 0\n' \
        > "$target/build/$release_root/scripts/install.sh"
    chmod 0755 "$target/build/$release_root/scripts/install.sh"
    ln -s /tmp "$target/build/$release_root/unsafe-link"
    tar -C "$target/build" -czf "$target/$ARCHIVE" "$release_root"
    (cd "$target" && sha256sum "$ARCHIVE" > SHA256SUMS)
    rm -rf "$target/build"
}

GOOD="$TMP/good"
build_good_assets "$GOOD"
run_release_installer "$GOOD" "$TMP/good.log"
grep -q "Verified release $VERSION" "$TMP/good.log"
grep -q 'DRY-RUN PASS' "$TMP/good.log"
echo RELEASE_INSTALL_VALID=PASS

TAMPER="$TMP/tamper"
cp -a "$GOOD" "$TAMPER"
printf 'tampered\n' >> "$TAMPER/$ARCHIVE"
expect_failure "$TAMPER" 'FAILED' "$TMP/tamper.log"
echo RELEASE_INSTALL_CHECKSUM_FAIL_CLOSED=PASS

WRONG="$TMP/wrong"
mkdir -p "$WRONG"
make_wrong_root "$WRONG"
expect_failure "$WRONG" 'unexpected path' "$TMP/wrong.log"
echo RELEASE_INSTALL_PATH_FAIL_CLOSED=PASS

LINK="$TMP/link"
mkdir -p "$LINK"
make_link_archive "$LINK"
expect_failure "$LINK" 'contains links' "$TMP/link.log"
echo RELEASE_INSTALL_LINK_FAIL_CLOSED=PASS

echo INSTALL_RELEASE_FIXTURE PASS
