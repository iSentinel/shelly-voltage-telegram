#!/usr/bin/env bash
set -euo pipefail

REPO="${REPO:-iSentinel/shelly-voltage-telegram}"
REF="${REF:-main}"
INSTALL_ARGS=()

usage() {
    cat <<'EOF'
Usage: bootstrap.sh [--dry-run] [--non-interactive] [-h|--help]

Download the project from GitHub and run its installer.
Environment:
  REPO=owner/repository   GitHub repository
  REF=main               Branch/tag archive reference (default: main)

Example after publication:
  curl -fsSL https://raw.githubusercontent.com/iSentinel/shelly-voltage-telegram/main/scripts/bootstrap.sh | sudo bash
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run|--non-interactive) INSTALL_ARGS+=("$1") ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

for command in curl tar; do
    command -v "$command" >/dev/null || {
        echo "ERROR: missing command: $command" >&2
        exit 2
    }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
BRANCH_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"
TAG_URL="https://github.com/${REPO}/archive/refs/tags/${REF}.tar.gz"
ARCHIVE="$TMP/source.tar.gz"

echo "Downloading ${REPO}@${REF} ..."
if ! curl -fL "$BRANCH_URL" -o "$ARCHIVE"; then
    echo "Branch ${REF} not found; trying tag ${REF} ..."
    curl -fL "$TAG_URL" -o "$ARCHIVE"
fi
tar -xzf "$ARCHIVE" -C "$TMP"
SOURCE="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -x "$SOURCE/scripts/install.sh" ]] || chmod 0755 "$SOURCE/scripts/install.sh"

if [[ ${EUID} -eq 0 ]]; then
    exec "$SOURCE/scripts/install.sh" "${INSTALL_ARGS[@]}"
fi
command -v sudo >/dev/null || {
    echo "ERROR: root privileges are required." >&2
    exit 2
}
exec sudo "$SOURCE/scripts/install.sh" "${INSTALL_ARGS[@]}"
