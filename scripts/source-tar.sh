#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: source-tar.sh SOURCE_DIRECTORY
Write a filtered source tar archive to standard output.
Exclude credentials, local secret directories, and generated files.
EOF
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    usage
    exit 0
fi

if [[ $# -ne 1 || ! -d "$1" ]]; then
    usage >&2
    exit 2
fi

cd "$1"
find . \( -name ".git" -o -name "secrets" -o \
    -name "__pycache__" -o -name "*.pyc" -o \
    \( -name ".env*" ! -name ".env.example" \) \) -prune -o -print0 |
    tar --null --no-recursion -cf - -T -
