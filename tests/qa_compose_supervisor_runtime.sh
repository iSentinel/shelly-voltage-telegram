#!/usr/bin/env bash
# Disposable, no-network single-owner integration test for LXC 114 only.
set -euo pipefail
if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help)
            echo "Usage: qa_compose_supervisor_runtime.sh [-h|--help]"
            echo "Runs isolated Compose/SQLite acceptance on LXC 114 only."
            exit 0 ;;
        *) echo "FAIL: unsupported arguments" >&2; exit 2 ;;
    esac
fi
[[ "$(hostname)" == "shelly-docker-reboot-qa" ]] || {
    echo "FAIL: disposable LXC 114 hostname required" >&2
    exit 3
}
[[ $EUID -eq 0 ]] || { echo "FAIL: QA root required" >&2; exit 3; }
SRC="$(cd "$(dirname "$0")/.." && pwd -P)"
FENCE=/var/lib/svt-qa-fence-20261119
NAME=svt-qa-compose-owner-20261119
PROJECT="$(mktemp -d /root/svt-qa-compose-owner-XXXXXX)"
DATA="$PROJECT/data"
clean() {
 docker rm -f "$NAME" >/dev/null 2>&1 || true
 if [[ -e "$FENCE/.svt-update-pending" ]]; then
  SVT_WRITER_FENCE_DIR="$FENCE" python3 "$SRC/scripts/writer-fence-marker.py" \
    clear --database "$DATA/history.sqlite3" --token qa-compose-owner-20261119 \
    >/dev/null 2>&1 || true
 fi
 rm -rf -- "$PROJECT"
}
trap clean EXIT
test ! -e "$FENCE/.svt-update-pending"
python3 "$SRC/scripts/prepare-writer-fence.py"  --directory "$FENCE" --uid 10001
mkdir -m 0700 "$DATA"
chown 10001:10001 "$DATA"
python3 - "$PROJECT" "$DATA" "$FENCE" "$SRC" "$NAME" <<'PY'
import json
import sys
from pathlib import Path

project, data, fence, source, name = sys.argv[1:]
project = Path(project)
worker = """
import time
from pathlib import Path
from app import Storage
from writer_fence import writer_lease, WriterFenceError
p=Path("/data/history.sqlite3")
with writer_lease(p):
 store=Storage(p,"Europe/Kyiv")
 store.initialize()
 store.set_meta("telegram_setting_meter_type","pro3em")
 store.set_meta("telegram_status_media_identity","photo:COMPOSEQA")
 while True:
  try:
   store.add_phases(time.time(),{
    "L1":(220.0,50.0,1.0),
    "L2":(221.0,50.0,1.0),
    "L3":(222.0,50.0,1.0)})
  except WriterFenceError:
   raise SystemExit(3)
  time.sleep(.3)
"""
original_worker = worker
worker = (
    "import traceback\n"
    "try:\n"
    " exec(" + repr(original_worker) + ")\n"
    "except BaseException:\n"
    " open('/data/qa-worker-error.txt','w').write(traceback.format_exc())\n"
    " raise\n"
)
svc = {
    "container_name": name,
    "image": "svt-qa-verified-local:0.5.21-hw",
    "network_mode": "none",
    "restart": "unless-stopped",
    "read_only": True,
    "user": "10001:10001",
    "working_dir": "/src",
    "entrypoint": ["python", "-u", "-c", worker],
    "environment": {
        "SVT_WRITER_FENCE_DIR": "/run/svt-fence",
        "PYTHONPATH": "/src",
        "PYTHONDONTWRITEBYTECODE": "1",
    },
    "volumes": [
        f"{data}:/data:rw",
        f"{fence}:/run/svt-fence:rw",
        f"{source}:/src:ro",
    ],
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges"],
}
(project/"compose.yaml").write_text(
    json.dumps({"services":{"shelly-voltage":svc}}, indent=2)
)
(project/"override.yaml").write_text(
    json.dumps({"services":{"shelly-voltage":{
        "image":"svt-qa-verified-local:0.5.21-hw"
    }}})
)
(project/"supervisor.yaml").write_text(
    json.dumps({"services":{"shelly-voltage":{"restart":"no"}}})
)
for name in ("override.yaml","supervisor.yaml"):
    (project/name).chmod(0o600)
PY
export SVT_ROOT="$PROJECT"
export SVT_UPDATE_OVERRIDE="$PROJECT/override.yaml"
export SVT_SUPERVISOR_OVERLAY="$PROJECT/supervisor.yaml"
bash "$SRC/scripts/start-compose.sh" --create-only
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = false
test "$(docker inspect "$NAME" --format '{{.HostConfig.RestartPolicy.Name}}')" = no
echo "REAL_DOCKER_CREATE_ONLY_NOT_RUNNING_PASS"
python3 "$SRC/scripts/supervised-container.py" --container "$NAME" \
 --database "$DATA/history.sqlite3" --fence-dir "$FENCE" --qa-offline --once
for i in $(seq 1 90); do
 if python3 - "$DATA/history.sqlite3" <<'PY'
import sqlite3
import sys
try:
    with sqlite3.connect(
        f"file:{sys.argv[1]}?mode=ro", uri=True
    ) as db:
        rows = dict(db.execute(
            "SELECT phase, count(*) FROM measurements GROUP BY phase"
        ))
        assert set(rows) == {"L1","L2","L3"}
        assert len(set(rows.values()))==1
        assert db.execute("PRAGMA quick_check").fetchone()[0]=="ok"
        print("SUPERVISOR_POSITIVE_L1_L2_L3_PASS",rows)
except (sqlite3.Error,AssertionError):
    sys.exit(1)
PY
 then break; fi
 sleep .2
done
if [[ "$i" -ge 90 ]]; then
 docker inspect "$NAME" --format 'STATUS={{.State.Status}} EXIT={{.State.ExitCode}}'
 test ! -f "$DATA/qa-worker-error.txt" || cat "$DATA/qa-worker-error.txt"
 exit 7
fi
before="$(python3 - "$DATA/history.sqlite3" <<'PY'
import sqlite3
import sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    print(db.execute("SELECT COUNT(*) FROM measurements").fetchone()[0])
PY
)"
docker stop "$NAME" >/dev/null
SVT_WRITER_FENCE_DIR="$FENCE" python3 "$SRC/scripts/writer-fence-marker.py" \
 publish --database "$DATA/history.sqlite3" --token qa-compose-owner-20261119
python3 "$SRC/scripts/supervised-container.py" --container "$NAME" \
 --database "$DATA/history.sqlite3" --fence-dir "$FENCE" --qa-offline --once
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = false
echo "PENDING_BLOCKS_SUPERVISED_START_PASS"
SVT_WRITER_FENCE_DIR="$FENCE" python3 "$SRC/scripts/writer-fence-marker.py" \
 clear --database "$DATA/history.sqlite3" --token qa-compose-owner-20261119
python3 "$SRC/scripts/supervised-container.py" --container "$NAME" \
 --database "$DATA/history.sqlite3" --fence-dir "$FENCE" --qa-offline --once
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = true
after=0
for step in $(seq 1 80); do
  after="$(python3 - "$DATA/history.sqlite3" <<'PY'
import sqlite3
import sys
with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    phases = dict(db.execute(
        "SELECT phase,count(*) FROM measurements GROUP BY phase"
    ))
    assert len(phases) == 3 and len(set(phases.values())) == 1
    assert db.execute(
        "SELECT value FROM meta WHERE key='telegram_status_media_identity'"
    ).fetchone() == ("photo:COMPOSEQA",)
    print(sum(phases.values()))
PY
)"
  (( after > before )) && break
  sleep .2
done
(( after > before )) || { echo "FAIL: no new phase cycle" >&2; exit 8; }
echo "AFTER_CLEAR_THREE_PHASE_CYCLES_PASS before=$before after=$after"
docker stop "$NAME" >/dev/null
echo "QA_REAL_COMPOSE_CREATE_SUPERVISOR_PENDING_PASS"
