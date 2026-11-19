#!/usr/bin/env bash
# Persistent, disposable pending-and-rescue guest-reboot acceptance (LXC114).
set -euo pipefail

usage() {
    echo "Usage: qa_paired_rescue_reboot.sh prepare|verify|prepare-fault|verify-fault|cleanup|-h|--help"
    echo "Only on LXC114. Never connects to a real meter or Telegram."
}
if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    usage
    exit 0
fi
[[ $# -eq 1 ]] || { usage >&2; exit 2; }
[[ "$1" == prepare || "$1" == verify || "$1" == cleanup ||
   "$1" == prepare-fault || "$1" == verify-fault ]] || {
    usage >&2
    exit 2
}
[[ "$(hostname)" == "shelly-docker-reboot-qa" && "$EUID" -eq 0 ]] || {
    echo "FAIL: LXC114 root-only QA" >&2
    exit 3
}

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
FENCE=/var/lib/svt-qa-fence-20261119
DATA=/var/tmp/svt-qa-reboot-pair-20261119
OUTPUT=/var/lib/svt-qa-reboot-pair-rescue-20261119
PAIR=/var/lib/svt-qa-paired-proof-20261119
NAME=svt-qa-reboot-pair-20261119
TOKEN=qa-reboot-pair-20261119
IMAGE=svt-qa-verified-local:0.5.21-hw
DB="$DATA/history.sqlite3"
RESCUE="$OUTPUT/newest.sqlite3"

paired_proof() {
    python3 "$ROOT/scripts/qa-paired-rescue-preflight.py" \
      --qa-only --source-snapshot "$PAIR/upgrades" \
      --signed-snapshot "$PAIR/20261118T015250Z-0.5.25-hw" \
      --new-receipt "$PAIR/new-deployed.json" \
      --new-version "$PAIR/RELEASE_VERSION" \
      --container "$NAME" --database "$DB" --fence-dir "$FENCE" \
      --output "$RESCUE" --token "$TOKEN" --jsonl
}

row_count() {
    python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True) as db:
    assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
    counts = dict(db.execute(
        "SELECT phase, count(*) FROM measurements GROUP BY phase"
    ))
    assert set(counts) == {"L1", "L2", "L3"}
    assert len(set(counts.values())) == 1
    assert db.execute(
        "SELECT value FROM meta "
        "WHERE key='telegram_status_media_identity'"
    ).fetchone() == ("photo:REBOOT-PAIR",)
    print(sum(counts.values()))
PY
}

cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    if [[ -e "$FENCE/.svt-update-pending" ]]; then
        SVT_WRITER_FENCE_DIR="$FENCE" python3 \
          "$ROOT/scripts/writer-fence-marker.py" clear \
          --database "$DB" --token "$TOKEN" >/dev/null
    fi
    rm -rf -- "$DATA" "$OUTPUT" "$PAIR"
    echo "QA_REBOOT_PAIR_CLEANED"
}

prepare() {
    [[ ! -e "$DATA" && ! -e "$OUTPUT" && ! -e "$PAIR" ]] || {
        echo "FAIL: existing reboot QA fixture" >&2
        return 3
    }
    [[ ! -e "$FENCE/.svt-update-pending" ]] || return 3
    python3 "$ROOT/scripts/prepare-writer-fence.py" \
      --directory "$FENCE" --uid 10001
    mkdir -m 0700 "$DATA" "$OUTPUT"
    chown 10001:10001 "$DATA"
    python3 "$ROOT/tests/qa_paired_rescue_fixture.py" "$PAIR"
    seed_history
    create_writer
    docker start "$NAME" >/dev/null
    sleep 1
    if [[ "${1:-}" == fault ]]; then
        prepare_fault_boundary
        return
    fi
    python3 "$ROOT/scripts/stage-guarded-update.py" \
      --qa-only --container "$NAME" --database "$DB" \
      --fence-dir "$FENCE" --output "$RESCUE" --token "$TOKEN"
    paired_proof >"$OUTPUT/proof-before.jsonl"
    row_count >"$OUTPUT/row-count-before"
    cat /proc/sys/kernel/random/boot_id >"$OUTPUT/boot-id-before"
    [[ "$(docker inspect "$NAME" --format '{{.State.Running}}')" == false ]]
    [[ -e "$FENCE/.svt-update-pending" ]]
    echo "PENDING_RESCUE_BEFORE_GUEST_REBOOT_PASS"
}

prepare_fault_boundary() {
    # Inject a controlled application exception before writing QA receipt.
    set +e
    python3 - "$ROOT/scripts/stage-guarded-update.py" \
      --qa-only --container "$NAME" --database "$DB" \
      --fence-dir "$FENCE" --output "$RESCUE" \
      --token "$TOKEN" <<'PYFAULT'
import importlib.util
import sys
from pathlib import Path

script = Path(sys.argv[1])
sys.argv = [str(script), *sys.argv[2:]]
spec = importlib.util.spec_from_file_location("svt_qa_fault_updater", script)
assert spec is not None and spec.loader is not None
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def reject_receipt(_args):
    """Test a controlled failure after backup but before the QA receipt."""
    raise updater.StageBlocked("SVT_QA_INJECTED_RECEIPT_FAILURE")


updater.persist_receipt = reject_receipt
raise SystemExit(updater.main())
PYFAULT
    status=$?
    set -e
    [[ "$status" -eq 3 ]] || {
        echo "FAIL: injected receipt failure did not return 3" >&2
        return 6
    }
    [[ -s "$RESCUE" && ! -e "$RESCUE.qa.json" ]]
    [[ -e "$FENCE/.svt-update-pending" ]]
    [[ "$(docker inspect "$NAME" --format '{{.State.Running}}')" == false ]]
    row_count >"$OUTPUT/row-count-before"
    echo "CONTROLLED_FAILURE_AFTER_RESCUE_FAIL_CLOSED_PASS"
}

verify_fault_boundary() {
    [[ -s "$RESCUE" && ! -e "$RESCUE.qa.json" ]]
    [[ -e "$FENCE/.svt-update-pending" ]]
    [[ "$(docker inspect "$NAME" --format '{{.State.Running}}')" == false ]]
    local before after
    before="$(cat "$OUTPUT/row-count-before")"
    [[ "$(row_count)" == "$before" ]]
    python3 "$ROOT/scripts/stage-guarded-update.py" \
      --qa-only --container "$NAME" --database "$DB" \
      --fence-dir "$FENCE" --output "$RESCUE" --token "$TOKEN" \
      --recover-receipt
    paired_proof >"$OUTPUT/proof-recovered.jsonl"
    [[ "$(row_count)" == "$before" ]]
    [[ -e "$FENCE/.svt-update-pending" ]]
    echo "FAULT_RECEIPT_RECONSTRUCTION_AND_PAIR_PARITY_PASS"
    SVT_WRITER_FENCE_DIR="$FENCE" python3 \
      "$ROOT/scripts/writer-fence-marker.py" clear \
      --database "$DB" --token "$TOKEN"
    python3 "$ROOT/scripts/supervised-container.py" \
      --container "$NAME" --database "$DB" \
      --fence-dir "$FENCE" --qa-offline --once
    after="$before"
    for _ in $(seq 1 80); do
        after="$(row_count)"
        (( after > before )) && break
        sleep .2
    done
    (( after > before )) || return 5
    echo "FAULT_RECOVERY_THREE_PHASE_PASS before=$before after=$after"
    cleanup
}

seed_history() {
    local code
    code="$(cat <<'PY'
from pathlib import Path
from app import Storage
from writer_fence import writer_lease

path = Path("/data/history.sqlite3")
with writer_lease(path):
    store = Storage(path, "Europe/Kyiv")
    store.initialize()
    store.set_meta("telegram_setting_meter_type", "pro3em")
    store.set_meta("telegram_status_media_identity", "photo:REBOOT-PAIR")
    for ts in (1000.0, 1010.0):
        store.add_phases(ts, {
            "L1": (221.0, 50.0, 1.0),
            "L2": (222.0, 50.0, 1.0),
            "L3": (223.0, 50.0, 1.0),
        })
PY
)"
    docker run --rm --name svt-qa-reboot-pair-seed-20261119 \
      --network none --read-only --user 10001:10001 \
      --cap-drop ALL --security-opt no-new-privileges \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      -v "$ROOT:/src:ro" -v "$DATA:/data:rw" \
      -v "$FENCE:/run/svt-fence:rw" -w /src \
      -e PYTHONPATH=/src -e SVT_WRITER_FENCE_DIR=/run/svt-fence \
      --entrypoint python "$IMAGE" -c "$code"
}

create_writer() {
    local code
    code="$(cat <<'PY'
import time
from pathlib import Path
from app import Storage
from writer_fence import WriterFenceError, writer_lease

path = Path("/data/history.sqlite3")
with writer_lease(path):
    store = Storage(path, "Europe/Kyiv")
    while True:
        try:
            store.add_phases(time.time(), {
                "L1": (221.0, 50.0, 1.0),
                "L2": (222.0, 50.0, 1.0),
                "L3": (223.0, 50.0, 1.0),
            })
        except WriterFenceError:
            raise SystemExit(3)
        time.sleep(0.2)
PY
)"
    docker create --name "$NAME" --restart no --network none \
      --read-only --user 10001:10001 \
      --cap-drop ALL --security-opt no-new-privileges \
      --pids-limit 128 --memory 256m \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      -v "$ROOT:/src:ro" -v "$DATA:/data:rw" \
      -v "$FENCE:/run/svt-fence:rw" -w /src \
      -e PYTHONPATH=/src -e SVT_WRITER_FENCE_DIR=/run/svt-fence \
      --entrypoint python "$IMAGE" -u -c "$code" >/dev/null
}

verify() {
    [[ -s "$OUTPUT/boot-id-before" ]] || return 3
    [[ "$(cat /proc/sys/kernel/random/boot_id)" != \
       "$(cat "$OUTPUT/boot-id-before")" ]] || {
        echo "FAIL: guest boot ID unchanged" >&2
        return 3
    }
    [[ "$(docker inspect "$NAME" --format '{{.State.Running}}')" == false ]]
    [[ "$(docker inspect "$NAME" --format '{{.HostConfig.RestartPolicy.Name}}')" == no ]]
    [[ -e "$FENCE/.svt-update-pending" ]]
    local before after
    before="$(cat "$OUTPUT/row-count-before")"
    [[ "$(row_count)" == "$before" ]] || return 3
    paired_proof >"$OUTPUT/proof-after.jsonl"
    python3 - "$OUTPUT/proof-after.jsonl" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
assert report["status"] == "PASS_QA_IDENTITY_AND_WAL_ONLY"
assert report["sqlite_wal_photo_parity"] is True
assert report["release_authorized"] is False
assert report["rollback_authorized"] is False
PY
    echo "PENDING_RESCUE_SURVIVED_GUEST_REBOOT_PASS rows=$before"
    SVT_WRITER_FENCE_DIR="$FENCE" python3 \
      "$ROOT/scripts/writer-fence-marker.py" clear \
      --database "$DB" --token "$TOKEN"
    python3 "$ROOT/scripts/supervised-container.py" \
      --container "$NAME" --database "$DB" \
      --fence-dir "$FENCE" --qa-offline --once
    after="$before"
    for _ in $(seq 1 80); do
        after="$(row_count)"
        (( after > before )) && break
        sleep .2
    done
    (( after > before )) || { echo "FAIL: no new phases" >&2; return 5; }
    echo "POST_REBOOT_NEW_THREE_PHASE_CYCLES_PASS before=$before after=$after"
    cleanup
}

case "$1" in
    prepare) prepare ;;
    verify) verify ;;
    prepare-fault) prepare fault ;;
    verify-fault) verify_fault_boundary ;;
    cleanup) cleanup ;;
esac
