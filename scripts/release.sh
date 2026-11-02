#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT/dist}"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-0}"
DRY_RUN=false
STAGE_DIR=""

cleanup() {
    [[ -z "${STAGE_DIR:-}" ]] || rm -rf -- "$STAGE_DIR"
}

usage() {
    cat <<'EOF'
Usage: release.sh [--output-dir DIR] [--source-date-epoch EPOCH] [--dry-run] [-h|--help]

Build a reproducible source release archive and SHA256 checksums.
Deployment .env, secrets, pycache, local databases and generated dist files are excluded.

Options:
  --output-dir DIR          Output directory (default: ./dist)
  --source-date-epoch N     Fixed archive timestamp (default: 0)
  --dry-run                 Validate inputs and print planned output only
  -h, --help                Show this help
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --output-dir)
                [[ $# -ge 2 ]] || { echo "ERROR: --output-dir needs a value" >&2; exit 2; }
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --source-date-epoch)
                [[ $# -ge 2 ]] || { echo "ERROR: --source-date-epoch needs a value" >&2; exit 2; }
                SOURCE_DATE_EPOCH="$2"
                shift 2
                ;;
            --dry-run)
                DRY_RUN=true
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

validate_inputs() {
    [[ "$SOURCE_DATE_EPOCH" =~ ^[0-9]+$ ]] || {
        echo "ERROR: SOURCE_DATE_EPOCH must be a non-negative integer." >&2
        exit 2
    }
    local required=(
        VERSION RELEASE_VERSION LIVE_VERSION app.py Dockerfile
        compose.yaml requirements.txt README.md
    )
    for file in "${required[@]}"; do
        [[ -f "$PROJECT/$file" ]] || {
            echo "ERROR: required release file missing: $file" >&2
            exit 3
        }
    done
}

release_name() {
    local version
    version="$(tr -d '\r\n' < "$PROJECT/VERSION")"
    [[ -n "$version" ]] || { echo "ERROR: VERSION is empty" >&2; exit 3; }
    printf 'shelly-voltage-telegram-%s' "$version"
}

copy_public_tree() {
    local destination="$1"
    mkdir -p "$destination"
    tar -C "$PROJECT" \
        --exclude='./.env' \
        --exclude='./secrets' \
        --exclude='./dist' \
        --exclude='./dist-publish' \
        --exclude='./validation' \
        --exclude='./ROADMAP.md.pre-*' \
        --exclude='./.git' \
        --exclude='./.github-main-sha' \
        --exclude='./__pycache__' \
        --exclude='*/__pycache__' \
        --exclude='./.ruff_cache' \
        --exclude='./.pytest_cache' \
        --exclude='./.mypy_cache' \
        --exclude='*.pyc' \
        --exclude='*.pyo' \
        --exclude='*.sqlite' \
        --exclude='*.sqlite3' \
        --exclude='*.sqlite3-*' \
        --exclude='*.log' \
        -cf - . | tar -C "$destination" -xf -
}

write_file_manifest() {
    local destination="$1"
    (
        cd "$destination"
        find . -type f ! -name FILES.SHA256 -print0 \
            | LC_ALL=C sort -z \
            | xargs -0r sha256sum > FILES.SHA256
    )
}

assert_public_tree() {
    local destination="$1"
    local forbidden
    forbidden="$(
        cd "$destination"
        find . -type f \( \
            -name '.env' -o -path './secrets/*' -o \
            -path './dist-publish/*' -o -path './validation/*' -o \
            -path './.ruff_cache/*' -o -path './.pytest_cache/*' -o \
            -path './.mypy_cache/*' -o -name '.github-main-sha' -o \
            -name 'ROADMAP.md.pre-*' -o \
            -name '*.pyc' -o -name '*.pyo' -o \
            -name '*.sqlite' -o -name '*.sqlite3' -o \
            -name '*.sqlite3-*' -o -name '*.log' \
        \) -print
    )"
    [[ -z "$forbidden" ]] || {
        echo "ERROR: private/generated files entered release tree:" >&2
        printf '%s\n' "$forbidden" >&2
        exit 4
    }
    [[ -f "$destination/.env.example" ]] || {
        echo "ERROR: public .env.example missing from release." >&2
        exit 4
    }
}

assert_no_deployment_identifiers() {
    local destination="$1"
    local value source label
    while IFS='|' read -r label value; do
        [[ -n "$value" ]] || continue
        if grep -RqsF -- "$value" "$destination"; then
            echo "ERROR: local deployment value leaked into public release: $label" >&2
            exit 4
        fi
    done < <(
        if [[ -f "$PROJECT/.env" ]]; then
            for source in SHELLY_HOST TELEGRAM_STATUS_CHAT_ID TELEGRAM_ADMIN_USER_IDS; do
                value="$(sed -n "s/^${source}=//p" "$PROJECT/.env" | head -n1)"
                [[ -n "$value" ]] && printf '%s|%s
' "$source" "$value"
            done
        fi
        if [[ -d "$PROJECT/secrets" ]]; then
            for source in telegram_token telegram_chat_id shelly_password shelly_l2_password shelly_l3_password; do
                [[ -f "$PROJECT/secrets/$source" ]] || continue
                value="$(cat "$PROJECT/secrets/$source")"
                [[ -n "$value" ]] && printf '%s|%s
' "$source" "$value"
            done
        fi
    )
}

build_archive() {
    local name="$1" stage="$2" archive="$3"
    tar -C "$stage" \
        --sort=name \
        --mtime="@$SOURCE_DATE_EPOCH" \
        --owner=0 --group=0 --numeric-owner \
        --pax-option=delete=atime,delete=ctime \
        -cf - "$name" | gzip -n > "$archive"
}

main() {
    parse_args "$@"
    validate_inputs
    local name archive stage
    name="$(release_name)"
    archive="$OUTPUT_DIR/$name.tar.gz"
    if [[ "$DRY_RUN" == true ]]; then
        echo "DRY-RUN: validate public source tree"
        echo "DRY-RUN: create $archive"
        echo "DRY-RUN: create $OUTPUT_DIR/SHA256SUMS"
        echo "DRY-RUN PASS: no release files created."
        return
    fi
    mkdir -p "$OUTPUT_DIR"
    stage="$(mktemp -d)"
    STAGE_DIR="$stage"
    trap cleanup EXIT
    copy_public_tree "$stage/$name"
    assert_public_tree "$stage/$name"
    assert_no_deployment_identifiers "$stage/$name"
    write_file_manifest "$stage/$name"
    rm -f "$archive" "$OUTPUT_DIR/SHA256SUMS"
    build_archive "$name" "$stage" "$archive"
    (
        cd "$OUTPUT_DIR"
        sha256sum "$(basename "$archive")" > SHA256SUMS
        sha256sum -c SHA256SUMS
    )
    echo "RELEASE PASS: $archive"
}

main "$@"
