#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} == -h || ${1:-} == --help ]]; then
    printf '%b\n' 'Usage: support-bundle.sh [-h|--help]\nCreate a redacted diagnostics bundle under /var/tmp.'
    exit 0
fi

ROOT="${SVT_ROOT:-/opt/shelly-voltage}"
DB="${DATABASE_PATH:-/var/lib/shelly-voltage/voltage.sqlite3}"
CONTAINER="${CONTAINER_NAME:-shelly-voltage-bot}"
OUT_DIR="${SUPPORT_BUNDLE_DIR:-/var/tmp}"
STAMP="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
OUT="$OUT_DIR/shelly-voltage-support-${STAMP}.tar.gz"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/data" "$OUT_DIR"

{
  echo "generated=$(date --iso-8601=seconds)"
  echo "hostname=$(hostname)"
  echo "kernel=$(uname -srmo)"
  echo "docker=$(docker --version)"
  echo "compose=$(docker compose version)"
} > "$WORK/system.txt"

if [[ -f "$ROOT/.env" ]]; then
  sed -E \
    -e 's/^([^=]*(TOKEN|PASSWORD|SECRET|KEY|CHAT_ID)[^=]*)=.*/\1=<redacted>/I' \
    -e 's/^(SHELLY_DEVICES_JSON)=.*/\1=<redacted>/I' \
    "$ROOT/.env" > "$WORK/config.redacted.env"
else
  echo "deployment .env not found" > "$WORK/config.redacted.env"
fi

(
  cd "$ROOT"
  docker compose ps
) > "$WORK/docker-ps.txt" 2>&1 || true

docker inspect "$CONTAINER" \
  --format 'Readonly={{.HostConfig.ReadonlyRootfs}} CapDrop={{json .HostConfig.CapDrop}} SecurityOpt={{json .HostConfig.SecurityOpt}} Pids={{.HostConfig.PidsLimit}} Restart={{.HostConfig.RestartPolicy.Name}}' \
  > "$WORK/docker-security.txt" 2>&1 || true

docker logs --tail 500 "$CONTAINER" \
  > "$WORK/container.log" 2>&1 || true

for endpoint in health ready metrics; do
  curl -fsS "http://127.0.0.1:8080/${endpoint}" \
    > "$WORK/${endpoint}.txt" 2>&1 || true
done

docker exec "$CONTAINER" python /app/app.py doctor \
  > "$WORK/doctor.json" 2>&1 || true

python3 "$ROOT/scripts/soak-report.py" --db "$DB" --hours 24 --json \
  > "$WORK/soak-24h.json" 2>&1 || true

python3 - "$DB" > "$WORK/database.txt" <<'PY'
import sqlite3
import sys
from contextlib import closing

path = sys.argv[1]
with closing(sqlite3.connect(path)) as db:
    print("quick_check=", db.execute("PRAGMA quick_check").fetchone()[0])
    print("rows=", db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0])
    print("schema_version=", db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0])
    print("stats=", db.execute(
        "SELECT MIN(voltage),MAX(voltage),AVG(voltage),MIN(frequency),MAX(frequency) "
        "FROM measurements"
    ).fetchone())
    print("latest=", db.execute(
        "SELECT ts,voltage,frequency FROM measurements ORDER BY ts DESC LIMIT 1"
    ).fetchone())
    has_outages = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='outages'"
    ).fetchone()
    if has_outages:
        print("outages=", db.execute(
            "SELECT COUNT(*), SUM(ended_at IS NULL), MAX(started_at) FROM outages"
        ).fetchone())
PY

systemctl --no-pager --full status shelly-voltage-compose.service \
  > "$WORK/compose-service.txt" 2>&1 || true
systemctl --no-pager --full status shelly-voltage-backup.timer \
  > "$WORK/backup-timer.txt" 2>&1 || true

python3 - "$WORK" "$ROOT" <<'PY'
import os
import re
import sys
from pathlib import Path

work = Path(sys.argv[1])
root = Path(sys.argv[2])
values: set[str] = set()
sensitive = re.compile(r"TOKEN|PASSWORD|SECRET|KEY|CHAT_ID", re.I)
env_path = root / ".env"
if env_path.exists():
    for raw in env_path.read_text(errors="replace").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if sensitive.search(key) and value:
            values.add(value)
secrets = root / "secrets"
if secrets.is_dir():
    for item in secrets.iterdir():
        if item.is_file():
            value = item.read_text(errors="replace").strip()
            if value:
                values.add(value)
values = {value for value in values if len(value) >= 4}
for path in work.rglob("*"):
    if not path.is_file():
        continue
    text = path.read_text(errors="replace")
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    path.write_text(text)
for path in work.rglob("*"):
    if not path.is_file():
        continue
    text = path.read_text(errors="replace")
    leaked = [value for value in values if value in text]
    if leaked:
        raise SystemExit(f"secret redaction failed in {path.name}")
PY

tar -C "$WORK" -czf "$OUT" .
chmod 0600 "$OUT"
sha256sum "$OUT" > "${OUT}.sha256"

echo "bundle=$OUT"
sha256sum "$OUT"
