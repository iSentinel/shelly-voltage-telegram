#!/usr/bin/env bash
# Isolated synthetic signed-pair and newest WAL/PHOTO QA on LXC114 only.
set -euo pipefail
if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help)
            echo "Usage: qa_paired_rescue_runtime.sh [-h|--help]"
            echo "Runs a disposable QA signed-pair/WAL proof only on LXC114."
            exit 0 ;;
        *) echo "FAIL: unexpected argument" >&2; exit 2 ;;
    esac
fi
[[ "$(hostname)" == "shelly-docker-reboot-qa" ]] || {
    echo "FAIL: disposable LXC114 required" >&2
    exit 3
}
[[ $EUID -eq 0 ]] || { echo "FAIL: QA root required" >&2; exit 3; }
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
FENCE=/var/lib/svt-qa-fence-20261119
DATA=/var/tmp/svt-qa-update-stage-20261119
OUT=/var/lib/svt-qa-stage-output-20261119
PAIR_ROOT=/var/lib/svt-qa-paired-proof-20261119
NAME=svt-qa-update-stage-20261119
TOKEN=qa-offline-stage-20261119
IMAGE=svt-qa-verified-local:0.5.21-hw
db="$DATA/history.sqlite3"
output="$OUT/newest.sqlite3"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  if test -e "$FENCE/.svt-update-pending"; then
    SVT_WRITER_FENCE_DIR="$FENCE" python3 \
      "$ROOT/scripts/writer-fence-marker.py" clear \
      --database "$db" --token "$TOKEN" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$DATA" "$OUT" "$PAIR_ROOT"
}
trap cleanup EXIT
test ! -e "$FENCE/.svt-update-pending"
test ! -e "$DATA" && test ! -e "$OUT"
python3 "$ROOT/scripts/prepare-writer-fence.py" --directory "$FENCE" --uid 10001
mkdir -m 0700 "$DATA" "$OUT"
chown 10001:10001 "$DATA"
seed="$(cat <<'PY'
from pathlib import Path
from app import Storage
from writer_fence import writer_lease
path=Path('/data/history.sqlite3')
with writer_lease(path):
 store=Storage(path,'Europe/Kyiv')
 store.initialize()
 store.set_meta('telegram_setting_meter_type','pro3em')
 store.set_meta('telegram_status_media_identity','photo:QA-STAGED')
 store.add_phases(1000.0,{
  'L1':(219.,50.,1.),'L2':(220.,50.,1.),'L3':(221.,50.,1.)
 })
print('QA_SOURCE_SEED_PASS',flush=True)
PY
)"
docker run --rm --name svt-qa-stage-seed-20261119 \
  --network none --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$ROOT:/src:ro" -v "$DATA:/data:rw" \
  -v "$FENCE:/run/svt-fence:rw" \
  -w /src -e PYTHONPATH=/src -e SVT_WRITER_FENCE_DIR=/run/svt-fence \
  --entrypoint python "$IMAGE" -c "$seed"
writer="$(cat <<'PY'
import time
from pathlib import Path
from app import Storage
from writer_fence import writer_lease,WriterFenceError
path=Path('/data/history.sqlite3')
with writer_lease(path):
 store=Storage(path,'Europe/Kyiv')
 while True:
  try:
   store.add_phases(time.time(),{
    'L1':(221.,50.,1.),'L2':(222.,50.,1.),'L3':(223.,50.,1.)
   })
  except WriterFenceError:
   raise SystemExit(3)
  time.sleep(.15)
PY
)"
docker create --name "$NAME" --restart no --network none \
  --read-only --user 10001:10001 \
  --cap-drop ALL --security-opt no-new-privileges \
  --pids-limit 128 --memory 256m \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  -v "$ROOT:/src:ro" -v "$DATA:/data:rw" \
  -v "$FENCE:/run/svt-fence:rw" \
  -w /src -e PYTHONPATH=/src -e SVT_WRITER_FENCE_DIR=/run/svt-fence \
  --entrypoint python "$IMAGE" -u -c "$writer" >/dev/null
set -- --qa-only --container "$NAME" --database "$db" \
 --fence-dir "$FENCE" --output "$output" --token "$TOKEN"
docker update --restart unless-stopped "$NAME" >/dev/null
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --dry-run >"$OUT/restart-rejected.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
test ! -e "$FENCE/.svt-update-pending"
test ! -e "$output"
echo UNFENCED_DOCKER_POLICY_REJECTED
docker update --restart no "$NAME" >/dev/null
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" --qa-only \
 --container "$NAME" --database /var/lib/shelly-voltage/voltage.sqlite3 \
 --fence-dir "$FENCE" --output "$output" --token "$TOKEN" \
 --dry-run >"$OUT/production-path-rejected.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
echo PRODUCTION_PATH_REJECTED
docker start "$NAME" >/dev/null
sleep .7
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --dry-run
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = true
test ! -e "$FENCE/.svt-update-pending"
echo NONINVASIVE_DRY_RUN_PASS
# Exercise actual updater SIGKILL boundaries; never kill Docker or systemd.
expect_updater_sigkill() {
  local label="$1" rc
  shift
  set +e
  python3 "$ROOT/scripts/stage-guarded-update.py" "$@" \
    >"$OUT/crash-$label.log" 2>&1
  rc=$?
  set -e
  test "$rc" -eq 137 || {
    echo "FAIL: expected QA self-SIGKILL at $label, rc=$rc" >&2
    exit 9
  }
  test -e "$FENCE/.svt-update-pending"
  echo "REAL_UPDATER_SIGKILL_$label:PENDING_RETAINED"
}
expect_updater_sigkill pending "$@" --qa-crash-at pending
test ! -e "$output"
expect_updater_sigkill writer_stopped "$@" \
  --resume-pending --qa-crash-at writer-stopped
test ! -e "$output"
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = false
expect_updater_sigkill rescue_written "$@" \
  --resume-pending --qa-crash-at rescue-written
test -s "$output"
test ! -e "$output.qa.json"
expect_updater_sigkill receipt_written "$@" \
  --resume-pending --qa-crash-at receipt-written
test -s "$output.qa.json"
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --resume-pending
echo REAL_SIGKILL_RECOVERY_WITHOUT_OLD_DB_RESTORE_PASS
test -s "$output"
test -s "$output.qa.json"
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = false
test -f "$FENCE/.svt-update-pending"
python3 - "$db" "$output" "$output.qa.json" <<'PY'
import hashlib, json,sqlite3,sys
from contextlib import closing
with closing(sqlite3.connect(sys.argv[1])) as live:
 with closing(sqlite3.connect(sys.argv[2])) as copy:
  assert live.execute("PRAGMA quick_check").fetchone()[0]=="ok"
  assert copy.execute("PRAGMA quick_check").fetchone()[0]=="ok"
  def digest(db):
   h=hashlib.sha256()
   for stmt in db.iterdump(): h.update((stmt+"\n").encode())
   return h.hexdigest()
  assert digest(live)==digest(copy)
  phases=dict(copy.execute("SELECT phase,count(*) FROM measurements GROUP BY phase"))
  assert set(phases)=={"L1","L2","L3"} and len(set(phases.values()))==1
  assert copy.execute("SELECT value FROM meta WHERE key='telegram_status_media_identity'").fetchone()[0]=="photo:QA-STAGED"
  print("STAGED_FULL_HISTORY_PARITY",phases)
with open(sys.argv[3],encoding='utf-8') as f:
 r=json.load(f)
assert r["status"]=="QA_ONLY_RESCUE_STAGED"
assert r["release_authorized"] is False
assert r["rollback_authorized"] is False
with open(sys.argv[2],"rb") as f:
 assert hashlib.file_digest(f,"sha256").hexdigest()==r["sha256"]
print("STAGED_QA_RECEIPT_SHA256_PASS")
PY
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect
echo STAGED_INSPECTION_READONLY_PASS
mv "$output.qa.json" "$OUT/qa-receipt-held"
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect >"$OUT/missing-receipt.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
test -f "$FENCE/.svt-update-pending"
test -s "$output"
echo INTERRUPTED_RECEIPT_RECOVERY_FAIL_CLOSED
cp "$output" "$OUT/verified-original.sqlite3"
python3 - "$output" <<'PYT'
import sqlite3,sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("UPDATE measurements SET voltage=199 WHERE phase='L3'")
PYT
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --recover-receipt >"$OUT/tampered-db.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
grep -q SVT_QA_RESCUE_HISTORY_MISMATCH "$OUT/tampered-db.log"
test ! -e "$output.qa.json"
test -f "$FENCE/.svt-update-pending"
echo MODIFIED_L3_HISTORY_BLOCKED_FROM_RECOVERY
mv -f "$OUT/verified-original.sqlite3" "$output"
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --recover-receipt
test -s "$output.qa.json"
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect
echo MISSING_RECEIPT_REGENERATED_WITH_HISTORY_PARITY
rm -f -- "$OUT/qa-receipt-held"
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --recover-receipt >"$OUT/duplicate-receipt.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
echo DUPLICATE_RECEIPT_RECOVERY_REFUSED
cp "$output.qa.json" "$OUT/qa-receipt-good"
python3 - "$output.qa.json" <<'PYR'
import json,sys
p=sys.argv[1]
with open(p) as f: d=json.load(f)
d["sha256"]="0"*64
with open(p,"w") as f: json.dump(d,f)
PYR
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect >"$OUT/forged-receipt.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
grep -q SVT_QA_RESCUE_HASH_MISMATCH "$OUT/forged-receipt.log"
test -s "$output"
echo FORGED_RECEIPT_HASH_BLOCKED
mv -f "$OUT/qa-receipt-good" "$output.qa.json"
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect
echo REAL_RECEIPT_RESTORED_PASS
test ! -e "$PAIR_ROOT"
python3 "$ROOT/tests/qa_paired_rescue_fixture.py" "$PAIR_ROOT"
python3 "$ROOT/scripts/qa-paired-rescue-preflight.py" \
 --source-snapshot "$PAIR_ROOT/upgrades" \
 --signed-snapshot "$PAIR_ROOT/20261118T015250Z-0.5.25-hw" \
 --new-receipt "$PAIR_ROOT/new-deployed.json" \
 --new-version "$PAIR_ROOT/RELEASE_VERSION" \
 "$@" --jsonl >"$OUT/paired-report.jsonl"
python3 - "$OUT/paired-report.jsonl" <<'PYREPORT'
import json
import sys
with open(sys.argv[1],encoding="utf-8") as handle:
    proof=json.load(handle)
assert proof["status"]=="PASS_QA_IDENTITY_AND_WAL_ONLY"
assert proof["sqlite_wal_photo_parity"] is True
assert proof["release_authorized"] is False
assert proof["rollback_authorized"] is False
assert proof["old_oci_signature_verified"] is False
assert proof["new_oci_signature_verified"] is False
print("REAL_QA_OLD_NEW_PAIR_AND_WAL_PHOTO_COHERENCE_PASS")
PYREPORT
test ! -x /usr/local/bin/cosign || {
 echo "FAIL: QA no-Cosign fixture expects missing executable" >&2
 exit 5
}
set +e
python3 "$ROOT/scripts/qa-paired-rescue-preflight.py" \
 --source-snapshot "$PAIR_ROOT/upgrades" \
 --signed-snapshot "$PAIR_ROOT/20261118T015250Z-0.5.25-hw" \
 --new-receipt "$PAIR_ROOT/new-deployed.json" \
 --new-version "$PAIR_ROOT/RELEASE_VERSION" \
 "$@" --verify-signatures >"$OUT/no-cosign.log" 2>&1
signature_rc=$?
set -e
test "$signature_rc" -eq 3
grep -q QA_COSIGN_UNAVAILABLE "$OUT/no-cosign.log"
test -f "$FENCE/.svt-update-pending"
test -s "$output"
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect
echo MISSING_COSIGN_DENIED_NO_QA_HISTORY_LOSS
printf "0.5.26-hw\\n" >"$PAIR_ROOT/RELEASE_VERSION"
set +e
python3 "$ROOT/scripts/qa-paired-rescue-preflight.py" \
 --source-snapshot "$PAIR_ROOT/upgrades" \
 --signed-snapshot "$PAIR_ROOT/20261118T015250Z-0.5.25-hw" \
 --new-receipt "$PAIR_ROOT/new-deployed.json" \
 --new-version "$PAIR_ROOT/RELEASE_VERSION" \
 "$@" >"$OUT/paired-wrong-version.log" 2>&1
pair_rc=$?
set -e
test "$pair_rc" -eq 3
grep -q QA_PAIR_TARGET_VERSION_MISMATCH "$OUT/paired-wrong-version.log"
echo REAL_QA_TARGET_VERSION_DRIFT_BLOCKED
printf "0.5.25-hw\\n" >"$PAIR_ROOT/RELEASE_VERSION"
cp "$output" "$OUT/newest-pair-verified.sqlite3"
python3 - "$output" <<'PYALTER'
import sqlite3
import sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("UPDATE measurements SET voltage=189 WHERE phase='L3'")
PYALTER
set +e
python3 "$ROOT/scripts/qa-paired-rescue-preflight.py" \
 --source-snapshot "$PAIR_ROOT/upgrades" \
 --signed-snapshot "$PAIR_ROOT/20261118T015250Z-0.5.25-hw" \
 --new-receipt "$PAIR_ROOT/new-deployed.json" \
 --new-version "$PAIR_ROOT/RELEASE_VERSION" \
 "$@" >"$OUT/paired-corrupt-history.log" 2>&1
pair_rc=$?
set -e
test "$pair_rc" -eq 3
test -f "$FENCE/.svt-update-pending"
mv -f "$OUT/newest-pair-verified.sqlite3" "$output"
python3 "$ROOT/scripts/qa-paired-rescue-preflight.py" \
 --source-snapshot "$PAIR_ROOT/upgrades" \
 --signed-snapshot "$PAIR_ROOT/20261118T015250Z-0.5.25-hw" \
 --new-receipt "$PAIR_ROOT/new-deployed.json" \
 --new-version "$PAIR_ROOT/RELEASE_VERSION" \
 "$@" --jsonl >/dev/null
echo REAL_QA_TAMPERED_RESCUE_REFUSED_AND_REPAIRED
set +e
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --dry-run >"$OUT/stale-pending.log" 2>&1
rc=$?
set -e
test "$rc" -eq 3
echo REENTRY_REFUSED_WHILE_PENDING
echo QA_ONLY_STOP_PENDING_RESCUE_TRANSACTION_PASS

# A crashed QA update must never clear pending without explicit acceptance.
python3 "$ROOT/scripts/stage-guarded-update.py" "$@" --inspect
set +e
SVT_WRITER_FENCE_DIR="$FENCE" python3 \
  "$ROOT/scripts/writer-fence-marker.py" clear \
  --database "$db" --token qa-wrong-transaction \
  >"$OUT/wrong-clear.log" 2>&1
wrong_rc=$?
set -e
test "$wrong_rc" -eq 3
test -e "$FENCE/.svt-update-pending"
echo WRONG_CLEAR_TOKEN_CANNOT_RELEASE_WRITER
python3 "$ROOT/scripts/supervised-container.py" \
  --container "$NAME" --database "$db" --fence-dir "$FENCE" \
  --qa-offline --once >"$OUT/pending-supervisor.log"
grep -q SKIP_PENDING "$OUT/pending-supervisor.log"
test "$(docker inspect "$NAME" --format '{{.State.Running}}')" = false
echo PENDING_STOPS_SUPERVISOR_AFTER_UPDATER_SIGKILL

# Resume the SAME offline QA image; this is not a signed image promotion.
SVT_WRITER_FENCE_DIR="$FENCE" python3 \
  "$ROOT/scripts/writer-fence-marker.py" clear \
  --database "$db" --token "$TOKEN"
python3 "$ROOT/scripts/supervised-container.py" \
  --container "$NAME" --database "$db" --fence-dir "$FENCE" \
  --qa-offline --once
python3 - "$db" "$output" <<'PYREPLAY'
import sqlite3
import sys
import time
from contextlib import closing

live_path, backup_path = sys.argv[1:]
with closing(sqlite3.connect(backup_path)) as backup:
    baseline = backup.execute(
        "SELECT * FROM measurements ORDER BY id"
    ).fetchall()
    original_photo = backup.execute(
        "SELECT value FROM meta WHERE key='telegram_status_media_identity'"
    ).fetchone()
assert original_photo == ("photo:QA-STAGED",)
deadline = time.monotonic() + 22
while time.monotonic() < deadline:
    with closing(sqlite3.connect(
        f"file:{live_path}?mode=ro", uri=True, timeout=5
    )) as live:
        rows = live.execute(
            "SELECT * FROM measurements ORDER BY id"
        ).fetchall()
        phases = dict(live.execute(
            "SELECT phase,count(*) FROM measurements GROUP BY phase"
        ))
        photo = live.execute(
            "SELECT value FROM meta WHERE key='telegram_status_media_identity'"
        ).fetchone()
        integrity = live.execute("PRAGMA quick_check").fetchone()
    assert rows[:len(baseline)] == baseline
    assert photo == original_photo
    assert integrity == ("ok",)
    assert set(phases) == {"L1", "L2", "L3"}
    assert len(set(phases.values())) == 1
    if len(rows) > len(baseline):
        print("POST_SIGKILL_REPLAY_FULL_OLD_HISTORY_PASS", flush=True)
        print("POST_SIGKILL_L1_L2_L3_NEW_CYCLES", phases, flush=True)
        break
    time.sleep(0.2)
else:
    raise SystemExit("FAIL: QA writer did not resume after marker clear")
PYREPLAY
test ! -e "$FENCE/.svt-update-pending"
docker stop "$NAME" >/dev/null
echo QA_SIGKILL_RESUME_SAME_IMAGE_AND_PHOTO_PASS
