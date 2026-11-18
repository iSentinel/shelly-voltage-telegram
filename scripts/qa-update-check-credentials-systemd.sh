#!/usr/bin/env bash
# Verify public update-check credentials through real systemd without network.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: scripts/qa-update-check-credentials-systemd.sh [-h|--help]

Verify LoadCredential and DynamicUser using a transient systemd unit.
Tests isolated status writes, release marker and --help without network.
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
runtime_name="svt-checker-runtime-$(date +%s)-$$"
credential_dir="/run/credentials/${unit}.service"
runtime_dir="/run/$runtime_name"
expected_version="$(cat "$release")"
probe="$(cat <<'PY'
from pathlib import Path
import json
import os
import runpy
import subprocess
import sys

credential_dir = Path(sys.argv[1])
state = Path(sys.argv[2])
expected = sys.argv[3]
checker = credential_dir / "github_update.py"
module = runpy.run_path(str(checker), run_name="credential_smoke")
actual = module["installed_version"](credential_dir, state)
if actual != expected:
    raise SystemExit("FAIL: incorrect release credential")
for protected in ("/opt/shelly-voltage/.env", "/opt/shelly-voltage/secrets"):
    if os.access(protected, os.R_OK):
        raise SystemExit("FAIL: protected application credentials readable")

module["check"].__globals__["published_release"] = lambda: {
    "tag_name": "v" + expected
}
status = module["check"](credential_dir, state)
if status["installed"] != expected or status["available"]:
    raise SystemExit("FAIL: unexpected synthetic release availability")
if status["auto_install"] is not False:
    raise SystemExit("FAIL: auto-install unexpectedly enabled")
output = state / "status.json"
module["atomic_json"](output, status)
saved = json.loads(output.read_text(encoding="utf-8"))
if saved["installed"] != expected or (output.stat().st_mode & 0o777) != 0o640:
    raise SystemExit("FAIL: isolated state file contents or mode")
if (credential_dir / "status.json").exists():
    raise SystemExit("FAIL: wrote into credential directory")

result = subprocess.run(
    [sys.executable, str(checker), "--help"],
    capture_output=True, text=True, check=False, timeout=10,
)
if result.returncode != 0 or "usage: github_update.py" not in result.stdout:
    raise SystemExit("FAIL: copied checker CLI help")
print(result.stdout.splitlines()[0])
print("PASS: simulated release, isolated 0640 status, secrets denied")
PY
)"
if ! output="$(systemd-run --unit="$unit" --collect --wait --pipe     --service-type=exec     -p DynamicUser=yes -p WorkingDirectory=/     -p PrivateTmp=yes -p NoNewPrivileges=yes     -p ProtectHome=yes -p ProtectSystem=strict     -p RestrictAddressFamilies=AF_UNIX     -p Environment=PYTHONDONTWRITEBYTECODE=1     -p "RuntimeDirectory=$runtime_name"     -p RuntimeDirectoryMode=0700     -p "LoadCredential=github_update.py:$checker"     -p "LoadCredential=RELEASE_VERSION:$release"     /usr/bin/python3 -c "$probe" "$credential_dir"         "$runtime_dir" "$expected_version" 2>&1)"; then
    printf '%s
' "$output" >&2
    echo "FAIL: transient DynamicUser credential invocation" >&2
    exit 1
fi

if [[ "$output" != *"usage: github_update.py"* ||
      "$output" != *"status=0/SUCCESS"* ]]; then
    printf '%s\n' "$output" >&2
    echo "FAIL: missing expected checker or service success receipt" >&2
    exit 1
fi
# Also verify the writable StateDirectory used by the real installed unit.
state_unit="svt-checker-state-qa-$(date +%s)-$$"
state_name="svt-checker-state-qa-$(date +%s)-$$"
state_dir="/var/lib/$state_name"
state_credential="/run/credentials/$state_unit.service"

cleanup_state() {
    # Only delete paths from this unique test namespace, never live state.
    if [[ "$state_name" == svt-checker-state-qa-* ]]; then
        if [[ -L "$state_dir" ]]; then
            rm -f -- "$state_dir"
        fi
        if [[ -d "/var/lib/private/$state_name" &&
              ! -L "/var/lib/private/$state_name" ]]; then
            rm -rf -- "/var/lib/private/$state_name"
        fi
    fi
}
trap cleanup_state EXIT

if ! state_output="$(systemd-run --unit="$state_unit" --collect --wait --pipe \
    --service-type=exec \
    -p DynamicUser=yes -p WorkingDirectory=/ \
    -p PrivateTmp=yes -p PrivateDevices=yes \
    -p NoNewPrivileges=yes -p ProtectHome=yes \
    -p ProtectSystem=strict -p RestrictAddressFamilies=AF_UNIX \
    -p CapabilityBoundingSet= -p LockPersonality=yes \
    -p MemoryDenyWriteExecute=yes \
    -p Environment=PYTHONDONTWRITEBYTECODE=1 \
    -p "StateDirectory=$state_name" -p StateDirectoryMode=0750 \
    -p "LoadCredential=github_update.py:$checker" \
    -p "LoadCredential=RELEASE_VERSION:$release" \
    /usr/bin/python3 -c "$probe" "$state_credential" \
        "$state_dir" "$expected_version" 2>&1)"; then
    printf '%s\n' "$state_output" >&2
    echo "FAIL: transient StateDirectory DynamicUser check" >&2
    exit 1
fi
if [[ "$state_output" != *"status=0/SUCCESS"* ||
      "$state_output" != *"simulated release, isolated 0640 status"* ||
      ! -f "$state_dir/status.json" ||
      "$(stat -c %a "$state_dir/status.json")" != 640 ||
      "$(stat -L -c %a "$state_dir")" != 750 ]]; then
    printf '%s\n' "$state_output" >&2
    echo "FAIL: unexpected isolated persistent state acceptance" >&2
    exit 1
fi
cleanup_state
trap - EXIT
if [[ -e "$state_dir" || -e "/var/lib/private/$state_name" ]]; then
    echo "FAIL: isolated StateDirectory residue" >&2
    exit 1
fi
echo "PASS: DynamicUser RuntimeDirectory and StateDirectory, protected secrets; no network"
