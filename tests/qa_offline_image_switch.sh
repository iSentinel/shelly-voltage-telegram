#!/usr/bin/env bash
# QA-only actual local Docker image change and reverse with full SQLite parity.
set -euo pipefail

if [[ $# -eq 1 && ( "$1" == -h || "$1" == --help ) ]]; then
    echo "Usage: bash tests/qa_offline_image_switch.sh [--qa-crash-recovery]"
    echo "Run as root in disconnected LXC114 only; unsigned local images."
    exit 0
fi
MODE=normal
if [[ $# -eq 1 ]]; then
    case "$1" in
        --qa-crash-recovery) MODE=crash ;;
        --qa-actor-forward) MODE=actor-forward ;;
        --qa-actor-reverse) MODE=actor-reverse ;;
        *) echo "FAIL: QA_ARGUMENT_INVALID" >&2; exit 2 ;;
    esac
fi
[[ $# -le 1 && "$EUID" -eq 0 &&
   "$(hostname)" == shelly-docker-reboot-qa ]] || {
    echo "FAIL: DISCONNECTED_ROOT_QA_ONLY" >&2
    exit 3
}

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
FENCE=/var/lib/svt-qa-local-switch-fence-20261119
DATA=/var/tmp/svt-qa-local-switch-data-20261119
RESCUE=/var/lib/svt-qa-local-switch-rescue-20261119
OLD_NAME=svt-qa-local-switch-old-20261119
NEW_NAME=svt-qa-local-switch-new-20261119
OLD_IMAGE=svt-qa-local-switch-old-20261119:qa
NEW_IMAGE=svt-qa-local-switch-new-20261119:qa
DB="$DATA/history.sqlite3"
TOKEN_NEXT=qa-local-switch-forward-20261119
TOKEN_BACK=qa-local-switch-back-20261119

guard_clean() {
    [[ ! -e "$DATA" && ! -e "$RESCUE" && ! -e "$FENCE" ]]
    [[ -z "$(docker ps -a --filter name=svt-qa-local-switch- --format '{{.Names}}')" ]]
    ! docker image inspect "$OLD_IMAGE" >/dev/null 2>&1
    ! docker image inspect "$NEW_IMAGE" >/dev/null 2>&1
}

clear_marker() {
    local token="$1"
    SVT_WRITER_FENCE_DIR="$FENCE" python3 \
      "$ROOT/scripts/writer-fence-marker.py" clear \
      --database "$DB" --token "$token"
}

cleanup() {
    docker rm -f "$OLD_NAME" "$NEW_NAME" >/dev/null 2>&1 || true
    docker image rm "$OLD_IMAGE" "$NEW_IMAGE" >/dev/null 2>&1 || true
    rm -rf -- "$DATA" "$RESCUE" "$FENCE"
}

build_images() {
    docker build --network none --pull=false -q -t "$OLD_IMAGE" - <<'OLD_DF'
FROM svt-qa-verified-local:0.5.21-hw
LABEL org.opencontainers.image.version="0.5.25-hw"
LABEL org.opencontainers.image.revision="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
LABEL qa.svt.local.image="old"
OLD_DF
    docker build --network none --pull=false -q -t "$NEW_IMAGE" - <<'NEW_DF'
FROM svt-qa-verified-local:0.5.21-hw
LABEL org.opencontainers.image.version="0.5.26-hw"
LABEL org.opencontainers.image.revision="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
LABEL qa.svt.local.image="new"
NEW_DF
    local old_id new_id
    old_id="$(docker image inspect "$OLD_IMAGE" --format '{{.Id}}')"
    new_id="$(docker image inspect "$NEW_IMAGE" --format '{{.Id}}')"
    [[ "$old_id" != "$new_id" ]]
    echo "DISTINCT_OFFLINE_DOCKER_IMAGE_IDS_PASS"
}

seed() {
    local code
    code="$(cat <<'PYSEED'
from pathlib import Path
from contextlib import closing
from app import Storage
from writer_fence import writer_lease
path=Path('/data/history.sqlite3')
with writer_lease(path):
    store=Storage(path,'Europe/Kyiv')
    store.initialize()
    store.set_meta('telegram_setting_meter_type','pro3em')
    store.set_meta('telegram_status_media_identity','photo:QA-LOCAL-IMAGE-SWITCH')
    store.set_meta('telegram_status_message_id','202')
    assert store.reserve_text_post()
    with closing(store.connect()) as db, db:
        db.execute(
            'INSERT INTO telegram_status_cleanup '
            '(chat_id,message_id,needs_unpin,retry_at) VALUES(?,?,?,?)',
            ('-100999',101,1,1300.0)
        )
    store.add_phases(1000.0,{
        'L1':(219.,50.,1.),'L2':(220.,50.,1.),'L3':(221.,50.,1.)
    })
print('SYNTHETIC_PRO3EM_AND_TELEGRAM_STATE_SEEDED')
PYSEED
)"
    docker run --rm --name svt-qa-switch-seed-20261119 \
      --network none --read-only --user 10001:10001 \
      --cap-drop ALL --security-opt no-new-privileges \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      -v "$ROOT:/src:ro" -v "$DATA:/data:rw" \
      -v "$FENCE:/run/svt-fence:rw" \
      -w /src -e PYTHONPATH=/src \
      -e SVT_WRITER_FENCE_DIR=/run/svt-fence \
      --entrypoint python "$OLD_IMAGE" -c "$code"
}

writer_code() {
    cat <<'PYWRITER'
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
        time.sleep(.12)
PYWRITER
}

create_writer() {
    local name="$1" image="$2" code="$3"
    docker create --name "$name" --restart no \
      --network none --read-only --user 10001:10001 \
      --cap-drop ALL --security-opt no-new-privileges \
      --pids-limit 128 --memory 256m \
      --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      -v "$ROOT:/src:ro" -v "$DATA:/data:rw" \
      -v "$FENCE:/run/svt-fence:rw" \
      -w /src -e PYTHONPATH=/src \
      -e SVT_WRITER_FENCE_DIR=/run/svt-fence \
      --entrypoint python "$image" -u -c "$code" >/dev/null
}

supervise_once() {
    python3 "$ROOT/scripts/supervised-container.py" \
      --container "$1" --database "$DB" \
      --fence-dir "$FENCE" --qa-offline --once
}

rescue() {
    SVT_WRITER_FENCE_DIR="$FENCE" python3 \
      "$ROOT/scripts/fenced-newest-rescue.py" \
      --database "$DB" --output "$1" --fence-dir "$FENCE"
}

verify_history() {
    python3 - "$DB" "$1" "$2" <<'PYVERIFY'
import sqlite3
import sys
import time
from contextlib import closing

db_path, snapshot_path, should_grow = sys.argv[1:]
with closing(sqlite3.connect(snapshot_path)) as old:
    original = old.execute(
        "SELECT * FROM measurements ORDER BY id"
    ).fetchall()
    intent = old.execute(
        "SELECT value FROM meta "
        "WHERE key='telegram_legacy_text_confirm_pending'"
    ).fetchone()
    cleanup = old.execute(
        "SELECT chat_id,message_id,needs_unpin,retry_at "
        "FROM telegram_status_cleanup"
    ).fetchall()
    photo = old.execute(
        "SELECT value FROM meta "
        "WHERE key='telegram_status_media_identity'"
    ).fetchone()
assert intent and intent[0].startswith("send:")
assert cleanup == [("-100999",101,1,1300.0)]
assert photo == ("photo:QA-LOCAL-IMAGE-SWITCH",)
deadline = time.monotonic() + 12
while True:
    with closing(sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True, timeout=5
    )) as live:
        rows = live.execute(
            "SELECT * FROM measurements ORDER BY id"
        ).fetchall()
        assert live.execute("PRAGMA quick_check").fetchone() == ("ok",)
        phases = dict(live.execute(
            "SELECT phase,count(*) FROM measurements GROUP BY phase"
        ))
        assert live.execute(
            "SELECT value FROM meta "
            "WHERE key='telegram_legacy_text_confirm_pending'"
        ).fetchone() == intent
        assert live.execute(
            "SELECT chat_id,message_id,needs_unpin,retry_at "
            "FROM telegram_status_cleanup"
        ).fetchall() == cleanup
        assert live.execute(
            "SELECT value FROM meta "
            "WHERE key='telegram_status_media_identity'"
        ).fetchone() == photo
    assert rows[:len(original)] == original
    assert set(phases) == {"L1","L2","L3"}
    assert len(set(phases.values())) == 1
    if should_grow == "grow" and len(rows) > len(original):
        print("ALL_PREVIOUS_ROWS_TELEGRAM_INTENTS_AND_PHOTO_PRESERVED",phases)
        break
    if should_grow == "stable" and len(rows) == len(original):
        print("QUIESCENT_FULL_SQLITE_HISTORY_AND_TELEGRAM_PARITY",phases)
        break
    if time.monotonic() >= deadline:
        raise SystemExit("FAIL: missing new 3-phase cycle")
    time.sleep(.15)
PYVERIFY
}

publish_marker() {
    local token="$1"
    SVT_WRITER_FENCE_DIR="$FENCE" python3 "$ROOT/scripts/writer-fence-marker.py"       publish --database "$DB" --token "$token"
}

fault_actor() {
    local stage="$1"
    [[ -f "$DB" && -d "$FENCE" && -d "$RESCUE" ]] || {
        echo "FAIL: QA_ACTOR_FIXTURE_MISSING" >&2
        return 3
    }
    if [[ "$stage" == forward ]]; then
        [[ "$(docker inspect "$OLD_NAME" --format '{{.State.Running}}')" == true ]]
        [[ ! -e "$RESCUE/old-latest.sqlite3" ]]
        publish_marker "$TOKEN_NEXT"
        docker stop -t 5 "$OLD_NAME" >/dev/null
        rescue "$RESCUE/old-latest.sqlite3"
    else
        [[ "$(docker inspect "$NEW_NAME" --format '{{.State.Running}}')" == true ]]
        [[ ! -e "$RESCUE/new-latest.sqlite3" ]]
        publish_marker "$TOKEN_BACK"
        docker stop -t 5 "$NEW_NAME" >/dev/null
        rescue "$RESCUE/new-latest.sqlite3"
    fi
    echo "QA_COORDINATOR_PREPARED_$stage" >&2
    kill -KILL "$BASHPID"
}

expect_coordinator_killed() {
    local stage="$1" rc
    set +e
    bash "$ROOT/tests/qa_offline_image_switch.sh" "--qa-actor-$stage"       >"$RESCUE/fault-$stage.log" 2>&1
    rc=$?
    set -e
    if [[ "$rc" -ne 137 ]]; then
        echo "FAIL: QA_COORDINATOR_EXIT_$stage rc=$rc" >&2
        cat "$RESCUE/fault-$stage.log" >&2
        return 3
    fi
    grep -q "QA_COORDINATOR_PREPARED_$stage" "$RESCUE/fault-$stage.log"
    echo "QA_COORDINATOR_SIGKILL_$stage:PENDING_RETAINED"
}

direct_start_must_fail_while_pending() {
    local name="$1" snapshot="$2" running code
    [[ -f "$FENCE/.svt-update-pending" ]]
    [[ "$(docker inspect "$name" --format '{{.State.Running}}')" == false ]]
    docker start "$name" >/dev/null
    running=true
    for _ in $(seq 1 80); do
        running="$(docker inspect "$name" --format '{{.State.Running}}')"
        [[ "$running" == false ]] && break
        sleep .10
    done
    [[ "$running" == false ]] || {
        echo "FAIL: UNFENCED_DIRECT_START_RUNNING" >&2
        return 3
    }
    code="$(docker inspect "$name" --format '{{.State.ExitCode}}')"
    [[ "$code" -ne 0 ]]
    verify_history "$snapshot" stable
    echo "DIRECT_DOCKER_START_DENIED_BY_SQLITE_PENDING_FENCE"
}

if [[ "$MODE" == actor-forward ]]; then
    fault_actor forward
elif [[ "$MODE" == actor-reverse ]]; then
    fault_actor reverse
fi

guard_clean
trap cleanup EXIT
python3 "$ROOT/scripts/prepare-writer-fence.py" \
    --directory "$FENCE" --uid 10001
mkdir -m 0700 "$DATA" "$RESCUE"
chown 10001:10001 "$DATA"
build_images
seed
code="$(writer_code)"
create_writer "$OLD_NAME" "$OLD_IMAGE" "$code"
create_writer "$NEW_NAME" "$NEW_IMAGE" "$code"
supervise_once "$OLD_NAME"
sleep .8
if [[ "$MODE" == crash ]]; then
    expect_coordinator_killed forward
    [[ -f "$FENCE/.svt-update-pending" ]]
    [[ -s "$RESCUE/old-latest.sqlite3" ]]
else
    publish_marker "$TOKEN_NEXT"
    docker stop -t 5 "$OLD_NAME" >/dev/null
    rescue "$RESCUE/old-latest.sqlite3"
fi
verify_history "$RESCUE/old-latest.sqlite3" stable
if [[ "$MODE" == crash ]]; then
    set +e
    clear_marker qa-unauthorized-forward >/dev/null 2>&1
    wrong_token_rc=$?
    set -e
    [[ "$wrong_token_rc" -eq 3 ]]
    [[ -f "$FENCE/.svt-update-pending" ]]
    echo "FORWARD_WRONG_TOKEN_CLEAR_DENIED"
fi
[[ "$(docker inspect "$OLD_NAME" --format '{{.State.Running}}')" == false ]]
supervise_once "$NEW_NAME" | grep -qx SKIP_PENDING
[[ "$(docker inspect "$NEW_NAME" --format '{{.State.Running}}')" == false ]]
echo "FORWARD_IMAGE_START_REFUSED_WHILE_PENDING"
direct_start_must_fail_while_pending "$NEW_NAME" "$RESCUE/old-latest.sqlite3"
clear_marker "$TOKEN_NEXT"
supervise_once "$NEW_NAME"
verify_history "$RESCUE/old-latest.sqlite3" grow
echo "ACTUAL_LOCAL_IMAGE_ID_CHANGED_AND_HISTORY_PRESERVED"
if [[ "$MODE" == crash ]]; then
    expect_coordinator_killed reverse
    [[ -f "$FENCE/.svt-update-pending" ]]
    [[ -s "$RESCUE/new-latest.sqlite3" ]]
else
    publish_marker "$TOKEN_BACK"
    docker stop -t 5 "$NEW_NAME" >/dev/null
    rescue "$RESCUE/new-latest.sqlite3"
fi
verify_history "$RESCUE/new-latest.sqlite3" stable
if [[ "$MODE" == crash ]]; then
    set +e
    clear_marker qa-unauthorized-reverse >/dev/null 2>&1
    wrong_token_rc=$?
    set -e
    [[ "$wrong_token_rc" -eq 3 ]]
    [[ -f "$FENCE/.svt-update-pending" ]]
    echo "REVERSE_WRONG_TOKEN_CLEAR_DENIED"
fi
supervise_once "$OLD_NAME" | grep -qx SKIP_PENDING
echo "REVERSE_IMAGE_START_REFUSED_WHILE_PENDING"
direct_start_must_fail_while_pending "$OLD_NAME" "$RESCUE/new-latest.sqlite3"
clear_marker "$TOKEN_BACK"
supervise_once "$OLD_NAME"
verify_history "$RESCUE/new-latest.sqlite3" grow
echo "ACTUAL_LOCAL_REVERSE_IMAGE_AND_TELEGRAM_HISTORY_PASS"
