#!/usr/bin/env bash
set -euo pipefail

ROOT="${SVT_ROOT:-/opt/shelly-voltage}"
ENV_FILE="$ROOT/.env"
SECRETS="$ROOT/secrets"

usage() {
    cat <<'EOF'
Usage: configure.sh [--non-interactive] [-h|--help]

Safely configure Shelly and optional Telegram settings.
Interactive Telegram token input is hidden and never passed as a command-line
argument. Non-interactive values may be supplied with:
  SVT_SHELLY_HOST
  SVT_SHELLY_METER_TYPE
  SVT_TELEGRAM_BOT_TOKEN
  SVT_TELEGRAM_CHAT_ID
  SVT_TELEGRAM_STATUS_CHAT_ID
  SVT_TELEGRAM_ADMIN_USER_IDS
EOF
}

NON_INTERACTIVE=false
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --non-interactive) NON_INTERACTIVE=true ;;
    "") ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
esac

[[ -f "$ENV_FILE" ]] || cp "$ROOT/.env.example" "$ENV_FILE"
mkdir -p "$SECRETS"
chmod 0700 "$SECRETS"
for file in telegram_token telegram_chat_id shelly_password shelly_l2_password shelly_l3_password; do
    touch "$SECRETS/$file"
    chown 10001:10001 "$SECRETS/$file"
    chmod 0600 "$SECRETS/$file"
done
chmod 0600 "$ENV_FILE"

current_host="$(sed -n 's/^SHELLY_HOST=//p' "$ENV_FILE" | head -n1)"
current_meter="$(sed -n 's/^SHELLY_METER_TYPE=//p' "$ENV_FILE" | head -n1)"
current_meter="${current_meter:-pro_em50}"
current_status_chat="$(sed -n 's/^TELEGRAM_STATUS_CHAT_ID=//p' "$ENV_FILE" | head -n1)"
current_admins="$(sed -n 's/^TELEGRAM_ADMIN_USER_IDS=//p' "$ENV_FILE" | head -n1)"
host="${SVT_SHELLY_HOST:-}"
meter_type="${SVT_SHELLY_METER_TYPE:-}"
token="${SVT_TELEGRAM_BOT_TOKEN:-}"
chat_id="${SVT_TELEGRAM_CHAT_ID:-}"
status_chat_id="${SVT_TELEGRAM_STATUS_CHAT_ID:-}"
admin_ids="${SVT_TELEGRAM_ADMIN_USER_IDS:-}"

if [[ "$NON_INTERACTIVE" != true && -r /dev/tty ]]; then
    read -r -p "Shelly host [$current_host]: " input </dev/tty
    host="${input:-$current_host}"
    read -r -p "Shelly meter type [$current_meter]: " input </dev/tty
    meter_type="${input:-$current_meter}"
    read -r -s -p "Telegram bot token (Enter = keep current/disabled): " input </dev/tty
    printf '\n' >/dev/tty
    [[ -n "$input" ]] && token="$input"
    read -r -p "Telegram control chat ID (Enter = keep current/disabled): " input </dev/tty
    [[ -n "$input" ]] && chat_id="$input"
    read -r -p "Telegram status channel/chat ID [$current_status_chat]: " input </dev/tty
    status_chat_id="${input:-$current_status_chat}"
    read -r -p "Telegram admin user IDs [$current_admins]: " input </dev/tty
    admin_ids="${input:-$current_admins}"
fi

host="${host:-$current_host}"
meter_type="${meter_type:-$current_meter}"
status_chat_id="${status_chat_id:-$current_status_chat}"
admin_ids="${admin_ids:-$current_admins}"
python3 - "$ENV_FILE" "$host" "$meter_type" "$status_chat_id" "$admin_ids" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
updates = {
    "SHELLY_HOST": sys.argv[2],
    "SHELLY_METER_TYPE": sys.argv[3],
    "TELEGRAM_STATUS_CHAT_ID": sys.argv[4],
    "TELEGRAM_ADMIN_USER_IDS": sys.argv[5],
}
lines = path.read_text().splitlines()
out = []
seen = set()
for line in lines:
    key = line.split("=", 1)[0]
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n")
PY

if [[ -n "$token" ]]; then
    printf '%s' "$token" > "$SECRETS/telegram_token"
fi
if [[ -n "$chat_id" ]]; then
    printf '%s' "$chat_id" > "$SECRETS/telegram_chat_id"
fi

chown 10001:10001 "$SECRETS"/*
chmod 0600 "$SECRETS"/* "$ENV_FILE"

printf 'Shelly host: %s\n' "$host"
printf 'Shelly meter type: %s\n' "$meter_type"
printf 'Telegram status destination: %s\n' "${status_chat_id:-control-chat}"
printf 'Telegram admins configured: %s\n' "$([[ -n "$admin_ids" ]] && echo yes || echo no)"
if [[ -s "$SECRETS/telegram_token" && -s "$SECRETS/telegram_chat_id" ]]; then
    echo 'Telegram: configured'
else
    echo 'Telegram: disabled until token and chat ID are configured'
fi
