#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-OWNER/shelly-voltage-telegram}"
VERSION="${VERSION:-}"
TAG="${TAG:-}"
RELEASE_BASE_URL="${RELEASE_BASE_URL:-}"
INSTALL_ARGS=()

usage() {
    cat <<'EOF'
Usage: install-release.sh [--dry-run] [--non-interactive] [-h|--help]

Download a pinned project release, verify its SHA256 checksum, validate the
archive layout, then run the bundled installer.

Environment:
  REPO=owner/repository       Required after GitHub publication
  VERSION=0.4.2-hw           Required release version
  TAG=v0.4.2-hw              Optional GitHub release tag (default: VERSION)
  RELEASE_BASE_URL=https://  Optional release-asset base URL override

Future example:
  curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/install-release.sh \
    | sudo REPO=OWNER/REPO VERSION=0.4.2-hw TAG=v0.4.2-hw bash -s -- --non-interactive
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run|--non-interactive)
                INSTALL_ARGS+=("$1")
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "ERROR: unknown option: $1" >&2
                usage >&2
                exit 2
                ;;
        esac
    done
}

validate_config() {
    [[ "$REPO" != OWNER/* ]] || {
        echo "ERROR: set REPO=owner/repository." >&2
        exit 2
    }
    [[ -n "$VERSION" ]] || {
        echo "ERROR: set VERSION to an explicit release version." >&2
        exit 2
    }
    [[ "$VERSION" =~ ^[A-Za-z0-9._-]+$ ]] || {
        echo "ERROR: VERSION contains unsupported characters." >&2
        exit 2
    }
    TAG="${TAG:-$VERSION}"
    [[ "$TAG" =~ ^[A-Za-z0-9._-]+$ ]] || {
        echo "ERROR: TAG contains unsupported characters." >&2
        exit 2
    }
    for command in curl tar sha256sum; do
        command -v "$command" >/dev/null || {
            echo "ERROR: missing command: $command" >&2
            exit 2
        }
    done
}

base_url() {
    if [[ -n "$RELEASE_BASE_URL" ]]; then
        printf '%s' "${RELEASE_BASE_URL%/}"
        return
    fi
    printf 'https://github.com/%s/releases/download/%s' "$REPO" "$TAG"
}

verify_checksum() {
    local dir="$1" archive_name="$2"
    local selected="$dir/SELECTED.SHA256"
    awk -v file="$archive_name" '$2 == file {print $1 "  " $2}' \
        "$dir/SHA256SUMS" > "$selected"
    [[ $(wc -l < "$selected") -eq 1 ]] || {
        echo "ERROR: checksum entry for $archive_name is missing or ambiguous." >&2
        exit 4
    }
    (cd "$dir" && sha256sum -c "$(basename "$selected")")
}

validate_archive() {
    local archive="$1" root_name="$2"
    local list verbose
    list="$(mktemp)"
    verbose="$(mktemp)"
    tar -tzf "$archive" > "$list"
    tar -tvzf "$archive" > "$verbose"
    awk -v root="$root_name" '
        $0 == root "/" {next}
        index($0, root "/") != 1 {bad=1; next}
        /(^|\/)\.\.($|\/)/ {bad=1; next}
        {next}
        END {exit bad}
    ' "$list" || {
        rm -f "$list" "$verbose"
        echo "ERROR: release archive contains an unexpected path." >&2
        exit 4
    }
    if grep -qE '^[lh]' "$verbose"; then
        rm -f "$list" "$verbose"
        echo "ERROR: release archive contains links; refusing extraction." >&2
        exit 4
    fi
    rm -f "$list" "$verbose"
}

run_installer() {
    local installer="$1"
    chmod 0755 "$installer"
    if [[ ${EUID} -eq 0 ]]; then
        exec "$installer" "${INSTALL_ARGS[@]}"
    fi
    command -v sudo >/dev/null || {
        echo "ERROR: root privileges are required." >&2
        exit 2
    }
    exec sudo "$installer" "${INSTALL_ARGS[@]}"
}

main() {
    parse_args "$@"
    validate_config
    local tmp url archive_name root_name source
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    url="$(base_url)"
    archive_name="shelly-voltage-telegram-${VERSION}.tar.gz"
    root_name="shelly-voltage-telegram-${VERSION}"
    echo "Downloading pinned release ${REPO}@${TAG} ..."
    curl -fL "$url/$archive_name" -o "$tmp/$archive_name"
    curl -fL "$url/SHA256SUMS" -o "$tmp/SHA256SUMS"
    verify_checksum "$tmp" "$archive_name"
    validate_archive "$tmp/$archive_name" "$root_name"
    tar -xzf "$tmp/$archive_name" -C "$tmp"
    source="$tmp/$root_name"
    [[ -x "$source/scripts/install.sh" ]] || chmod 0755 "$source/scripts/install.sh"
    echo "Verified release $VERSION; starting installer."
    run_installer "$source/scripts/install.sh"
}

main "$@"
