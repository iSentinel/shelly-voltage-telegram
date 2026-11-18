#!/usr/bin/env bash
# Verify public update-check credentials through real systemd without network.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: scripts/qa-update-check-credentials-systemd.sh [-h|--help]

Verify LoadCredential and DynamicUser using a transient systemd unit.
Runs only github_update.py --help; never contacts GitHub or alters the bot.
Requires root and PID 1 systemd. No production unit is installed or restarted.
HELP
}

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    usage
    exit 0
fi
if (( $# != 0 )); then
    usage >&2
    exit 2
fi
if (( EUID != 0 )) || [[ $(ps -p 1 -o comm=) != systemd ]]; then
    echo "FAIL: root and PID 1 systemd are required" >&2
    exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
checker="$root/scripts/github_update.py"
release="$root/RELEASE_VERSION"
if [[ ! -f "$checker" || ! -f "$release" ]]; then
    echo "FAIL: public checker or release marker is missing" >&2
    exit 1
fi

unit="svt-checker-cred-qa-$(date +%s)-$$"
credential_dir="/run/credentials/${unit}.service"
expected_version="$(cat "$release")"
probe="$(cat <<'PY'
from pathlib import Path
import os
import runpy
import sys

credential_dir = Path(sys.argv[1])
expected = sys.argv[2]
module = runpy.run_path(
    str(credential_dir / "github_update.py"),
    run_name="credential_smoke",
)
actual = module["installed_version"](
    credential_dir, credential_dir / "unused_state"
)
if actual != expected:
    raise SystemExit("FAIL: incorrect release credential")
os.execv(
    "/usr/bin/python3",
    ["/usr/bin/python3", str(credential_dir / "github_update.py"), "--help"],
)
PY
)"
if ! output="$(systemd-run --unit="$unit" --collect --wait --pipe \
    --service-type=exec \
    -p DynamicUser=yes -p WorkingDirectory=/ \
    -p PrivateTmp=yes -p NoNewPrivileges=yes \
    -p ProtectHome=yes -p ProtectSystem=strict \
    -p RestrictAddressFamilies=AF_UNIX \
    -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    -p "LoadCredential=github_update.py:$checker" \
    -p "LoadCredential=RELEASE_VERSION:$release" \
    /usr/bin/python3 -c "$probe" "$credential_dir"         "$expected_version" 2>&1)"; then
    printf '%s\n' "$output" >&2
    echo "FAIL: transient DynamicUser credential invocation" >&2
    exit 1
fi

if [[ "$output" != *"usage: github_update.py"* ||
      "$output" != *"status=0/SUCCESS"* ]]; then
    printf '%s\n' "$output" >&2
    echo "FAIL: missing expected checker or service success receipt" >&2
    exit 1
fi
echo "PASS: DynamicUser reads checker and release credentials; no network"
