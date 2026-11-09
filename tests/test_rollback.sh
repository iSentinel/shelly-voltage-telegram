#!/usr/bin/env bash
set -euo pipefail

PROJECT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TARGET="$TMP/target"
DB_DIR="$TMP/data"
SNAP_ROOT="$TMP/snapshots"
SNAP="$SNAP_ROOT/001-0.4.1-hw"
RESCUE="$TMP/rescue"
OLD_SRC="$TMP/old-source"

mkdir -p "$TARGET/scripts" "$TARGET/secrets" "$DB_DIR" "$SNAP" "$OLD_SRC"
cp "$PROJECT/scripts/backup.sh" "$TARGET/scripts/backup.sh"
chmod 0755 "$TARGET/scripts/backup.sh"
printf '0.4.2-hw\n' > "$TARGET/VERSION"
printf 'new\n' > "$TARGET/NEW_ONLY"
printf 'KEEP_ME=1\n' > "$TARGET/.env"
printf 'BACKUP_KEEP_ME=1\n' > "$TARGET/.env.pre-upgrade"
mkdir -p "$TARGET/validation"
printf 'keep private QA results\n' > "$TARGET/validation/acceptance.txt"
printf 'secret-preserved\n' > "$TARGET/secrets/token"
printf 'local operator checkpoint\n' > "$TARGET/ROADMAP.md"
printf 'local prior checkpoint\n' > "$TARGET/ROADMAP.md.pre-test"
printf 'local protected lock\n' > "$TARGET/.roadmap-update.lock"
printf 'local acceptance lock\n' > "$TARGET/.telegram-private-acceptance.lock"
mkdir -p "$TARGET/.work-state"
printf 'local progress\n' > "$TARGET/.work-state/firmware-progress.json"

python3 - "$DB_DIR/voltage.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    db.executescript("""
    CREATE TABLE measurements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        voltage REAL NOT NULL,
        frequency REAL NOT NULL
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE outages(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at REAL NOT NULL,
        ended_at REAL
    );
    INSERT INTO measurements(ts, voltage, frequency) VALUES(200, 222.0, 50.0);
    INSERT INTO meta(key, value) VALUES('schema_version', '2');
    """)
PY

mkdir -p "$OLD_SRC/scripts" "$OLD_SRC/systemd"
cp "$PROJECT/scripts/backup.sh" "$OLD_SRC/scripts/backup.sh"
for unit in shelly-voltage-compose.service shelly-voltage-backup.service; do
    printf '[Unit]\nDescription=synthetic %s\n' "$unit" \
        > "$OLD_SRC/systemd/$unit"
done
for file in app.py compose.yaml Dockerfile requirements.txt; do
    printf 'synthetic rollback source\n' > "$OLD_SRC/$file"
done
printf '0.4.1-hw\n' > "$OLD_SRC/VERSION"
printf 'old\n' > "$OLD_SRC/OLD_ONLY"
printf 'snapshot-overwrite\n' > "$OLD_SRC/.env"
printf 'snapshot-overwrite\n' > "$OLD_SRC/.env.pre-upgrade"
mkdir -p "$OLD_SRC/validation" "$OLD_SRC/secrets"
printf 'snapshot-overwrite\n' > "$OLD_SRC/validation/acceptance.txt"
printf 'snapshot-overwrite\n' > "$OLD_SRC/secrets/token"
printf 'stale operator checkpoint\n' > "$OLD_SRC/ROADMAP.md"
printf 'stale prior checkpoint\n' > "$OLD_SRC/ROADMAP.md.pre-test"
printf 'stale lock\n' > "$OLD_SRC/.roadmap-update.lock"
printf 'stale acceptance lock\n' > "$OLD_SRC/.telegram-private-acceptance.lock"
mkdir -p "$OLD_SRC/.work-state"
printf 'stale progress\n' > "$OLD_SRC/.work-state/firmware-progress.json"
tar -C "$OLD_SRC" -czf "$SNAP/source.tar.gz" .
sha256sum "$SNAP/source.tar.gz" > "$SNAP/source.tar.gz.sha256"

python3 - "$TMP/old.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    db.executescript("""
    CREATE TABLE measurements(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        voltage REAL NOT NULL,
        frequency REAL NOT NULL
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    INSERT INTO measurements(ts, voltage, frequency) VALUES(100, 111.0, 49.9);
    INSERT INTO meta(key, value) VALUES('schema_version', '1');
    """)
PY

gzip -c "$TMP/old.sqlite3" > "$SNAP/voltage-old.sqlite3.gz"
sha256sum "$SNAP/voltage-old.sqlite3.gz" > "$SNAP/voltage-old.sqlite3.gz.sha256"

# Reject checksum-consistent but INCOMPLETE archives before any stop.
incomplete="$TMP/incomplete-source"
mkdir -p "$incomplete"
cp "$SNAP/voltage-old.sqlite3.gz" "$incomplete/"
sha256sum "$incomplete/voltage-old.sqlite3.gz" \
    > "$incomplete/voltage-old.sqlite3.gz.sha256"
mkdir -p "$TMP/incomplete-src"
printf '0.4.1-hw\n' > "$TMP/incomplete-src/VERSION"
tar -C "$TMP/incomplete-src" -czf "$incomplete/source.tar.gz" .
sha256sum "$incomplete/source.tar.gz" > "$incomplete/source.tar.gz.sha256"
source_before="$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)"
db_before="$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)"
if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
    SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$incomplete" \
        --dry-run --no-start > "$TMP/incomplete-source.log" 2>&1; then
    echo "ERROR: incomplete source archive accepted" >&2
    exit 1
fi
grep -q "source archive missing required application files" "$TMP/incomplete-source.log"
test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" = "$source_before"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$db_before"
echo "ROLLBACK_INCOMPLETE_SOURCE_ARCHIVE=REJECTED"

# Every mandatory runtime path must be individually present in the TAR.
for required_path in VERSION app.py Dockerfile compose.yaml \
    requirements.txt scripts/backup.sh \
    systemd/shelly-voltage-compose.service \
    systemd/shelly-voltage-backup.service; do
    label="${required_path//\//_}"
    missing="$TMP/missing-source-$label"
    src="$TMP/missing-source-data-$label"
    mkdir -p "$missing"
    cp -a "$OLD_SRC" "$src"
    rm -f "$src/$required_path"
    cp "$SNAP/voltage-old.sqlite3.gz" "$missing/"
    sha256sum "$missing/voltage-old.sqlite3.gz" \
        > "$missing/voltage-old.sqlite3.gz.sha256"
    tar -C "$src" -czf "$missing/source.tar.gz" .
    sha256sum "$missing/source.tar.gz" > "$missing/source.tar.gz.sha256"
    if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
        SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$missing" \
            --dry-run --no-start > "$TMP/missing-$label.log" 2>&1; then
        echo "ERROR: rollback accepted missing $required_path" >&2
        exit 1
    fi
    grep -q "source archive missing required application files" \
        "$TMP/missing-$label.log"
done
echo "ROLLBACK_MISSING_EACH_REQUIRED_SOURCE_FILE=REJECTED"

# The second TAR entry must not overwrite an earlier valid required file.
duplicate="$TMP/duplicate-archive"
mkdir -p "$duplicate"
cp "$SNAP/voltage-old.sqlite3.gz" "$duplicate/"
sha256sum "$duplicate/voltage-old.sqlite3.gz" \
    > "$duplicate/voltage-old.sqlite3.gz.sha256"
python3 - "$SNAP/source.tar.gz" "$duplicate/source.tar.gz" <<'PY_DUPLICATE'
import sys
import tarfile

with (
    tarfile.open(sys.argv[1], "r:gz") as original,
    tarfile.open(sys.argv[2], "w:gz") as corrupted,
):
    for member in original:
        payload = original.extractfile(member) if member.isfile() else None
        corrupted.addfile(member, payload)
    empty = tarfile.TarInfo("./app.py")
    empty.size = 0
    corrupted.addfile(empty)
PY_DUPLICATE
sha256sum "$duplicate/source.tar.gz" \
    > "$duplicate/source.tar.gz.sha256"
if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
    SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$duplicate" \
        --dry-run --no-start > "$TMP/duplicate-archive.log" 2>&1; then
    echo "ERROR: TAR with duplicated app.py was accepted" >&2
    exit 1
fi
grep -q "duplicate source archive member" "$TMP/duplicate-archive.log"
test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" = "$source_before"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$db_before"
echo "ROLLBACK_DUPLICATE_TAR_ENTRY=REJECTED"

# A successful checksum of an unrelated file cannot verify selected archives.
for wrong in source database; do
    snapshot="$TMP/foreign-manifest-$wrong"
    mkdir -p "$snapshot"
    cp "$SNAP/source.tar.gz" "$snapshot/source.tar.gz"
    cp "$SNAP/voltage-old.sqlite3.gz" "$snapshot/voltage-old.sqlite3.gz"
    sha256sum "$snapshot/source.tar.gz" \
        > "$snapshot/source.tar.gz.sha256"
    sha256sum "$snapshot/voltage-old.sqlite3.gz" \
        > "$snapshot/voltage-old.sqlite3.gz.sha256"
    if [[ $wrong == source ]]; then
        cp "$snapshot/source.tar.gz" "$TMP/decoy-source.tar.gz"
        sha256sum "$TMP/decoy-source.tar.gz" \
            > "$snapshot/source.tar.gz.sha256"
    else
        cp "$snapshot/voltage-old.sqlite3.gz" "$TMP/decoy-sqlite.gz"
        sha256sum "$TMP/decoy-sqlite.gz" \
            > "$snapshot/voltage-old.sqlite3.gz.sha256"
    fi
    if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
        SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$snapshot" \
            --dry-run --no-start > "$TMP/foreign-$wrong.log" 2>&1; then
        echo "ERROR: checksum bound to foreign $wrong was accepted" >&2
        exit 1
    fi
    grep -q "ERROR: archive manifest not bound to selected file" \
        "$TMP/foreign-$wrong.log"
    test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" = "$source_before"
    test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$db_before"
    echo "ROLLBACK_FOREIGN_MANIFEST_$wrong=REJECTED"
done

# Multiple checksum-valid backups must never permit arbitrary first-file picks.
ambiguous="$TMP/ambiguous-sqlite-backup"
mkdir -p "$ambiguous"
cp "$SNAP/source.tar.gz" "$ambiguous/source.tar.gz"
sha256sum "$ambiguous/source.tar.gz" \
    > "$ambiguous/source.tar.gz.sha256"
for suffix in old newer; do
    cp "$SNAP/voltage-old.sqlite3.gz" \
        "$ambiguous/voltage-$suffix.sqlite3.gz"
    sha256sum "$ambiguous/voltage-$suffix.sqlite3.gz" \
        > "$ambiguous/voltage-$suffix.sqlite3.gz.sha256"
done
if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
    SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$ambiguous" \
        --dry-run --no-start > "$TMP/ambiguous-sqlite.log" 2>&1; then
    echo "ERROR: ambiguous source of historical SQLite accepted" >&2
    exit 1
fi
grep -q "ERROR: snapshot must contain exactly one SQLite backup" \
    "$TMP/ambiguous-sqlite.log"
test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" = "$source_before"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$db_before"
echo "ROLLBACK_MULTIPLE_SQLITE_BACKUPS=REJECTED"

# A source.tar.gz symlink can point outside the approved snapshot tree.
linked="$TMP/linked-source-archive"
mkdir -p "$linked"
cp "$SNAP/source.tar.gz" "$TMP/external-source.tar.gz"
ln -s "$TMP/external-source.tar.gz" "$linked/source.tar.gz"
sha256sum "$linked/source.tar.gz" > "$linked/source.tar.gz.sha256"
cp "$SNAP/voltage-old.sqlite3.gz" "$linked/voltage-old.sqlite3.gz"
sha256sum "$linked/voltage-old.sqlite3.gz" \
    > "$linked/voltage-old.sqlite3.gz.sha256"
if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
    SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$linked" \
        --dry-run --no-start > "$TMP/linked-source.log" 2>&1; then
    echo "ERROR: linked external source TAR was accepted" >&2
    exit 1
fi
grep -q "ERROR: source archive must be a regular nonlinked file" \
    "$TMP/linked-source.log"
test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" = "$source_before"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$db_before"
echo "ROLLBACK_LINKED_SOURCE_ARCHIVE=REJECTED"

# A symlink in a snapshot parent is not a trusted private snapshot root.
mkdir -p "$TMP/linked-parent-real"
cp -a "$SNAP" "$TMP/linked-parent-real/snapshot"
ln -s "$TMP/linked-parent-real" "$TMP/linked-parent"
alias_snap="$TMP/linked-parent/snapshot"
sha256sum "$alias_snap/source.tar.gz" > "$alias_snap/source.tar.gz.sha256"
sha256sum "$alias_snap/voltage-old.sqlite3.gz"     > "$alias_snap/voltage-old.sqlite3.gz.sha256"
if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3"     SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE"     "$PROJECT/scripts/rollback.sh" --snapshot "$alias_snap"         --dry-run --no-start > "$TMP/linked-parent.log" 2>&1; then
    echo "ERROR: rollback accepted symlinked snapshot parent" >&2
    exit 1
fi
grep -q "ERROR: rollback snapshot path contains a symlink"     "$TMP/linked-parent.log"
test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" = "$source_before"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$db_before"
echo "ROLLBACK_LINKED_SNAPSHOT_PARENT=REJECTED"

# Hard-linked archives/sidecars are not independent trusted artifacts.
for kind in source_archive sqlite_archive source_manifest sqlite_manifest; do
    directory="$TMP/hardlinked-$kind"
    mkdir -p "$directory"
    cp "$SNAP/source.tar.gz" "$directory/source.tar.gz"
    cp "$SNAP/voltage-old.sqlite3.gz"         "$directory/voltage-old.sqlite3.gz"
    sha256sum "$directory/source.tar.gz"         > "$directory/source.tar.gz.sha256"
    sha256sum "$directory/voltage-old.sqlite3.gz"         > "$directory/voltage-old.sqlite3.gz.sha256"
    case "$kind" in
        source_archive) filename=source.tar.gz ;;
        sqlite_archive) filename=voltage-old.sqlite3.gz ;;
        source_manifest) filename=source.tar.gz.sha256 ;;
        sqlite_manifest) filename=voltage-old.sqlite3.gz.sha256 ;;
    esac
    mv "$directory/$filename" "$TMP/external-$kind"
    ln "$TMP/external-$kind" "$directory/$filename"
    if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3"         SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE"         "$PROJECT/scripts/rollback.sh" --snapshot "$directory"             --dry-run --no-start > "$TMP/hardlinked-$kind.log" 2>&1; then
        echo "ERROR: rollback accepted hardlinked $kind" >&2
        exit 1
    fi
    grep -q "ERROR: rollback snapshot artifact has multiple links"         "$TMP/hardlinked-$kind.log"
    test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)"         = "$source_before"
    test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)"         = "$db_before"
done
echo "ROLLBACK_HARDLINKED_SNAPSHOT_ARTIFACTS=REJECTED"

# Do not stop the writer if rescue or live SQLite paths are redirected.
mkdir -p "$TMP/rollback-mock-bin" "$TMP/real-rescue-root"
ln -s "$TMP/real-rescue-root" "$TMP/linked-rescue-root"
ln -s "$DB_DIR" "$TMP/linked-live-db-parent"
cat > "$TMP/rollback-mock-bin/docker" <<'MOCK_PRECHECK_DOCKER'
#!/usr/bin/env bash
if [[ $1 == compose && $4 == stop ]]; then
    touch "$MOCK_STOP_MARKER"
    exit 0
fi
if [[ $1 == inspect ]]; then
    printf 'false\n'
    exit 0
fi
exit 97
MOCK_PRECHECK_DOCKER
chmod 0755 "$TMP/rollback-mock-bin/docker"
touch "$TARGET/compose.yaml"
for kind in linked_rescue linked_live_db; do
    rm -f "$TMP/precheck-stopped"
    db_for_case="$DB_DIR/voltage.sqlite3"
    rescue_for_case="$TMP/rescue"
    if [[ $kind == linked_rescue ]]; then
        rescue_for_case="$TMP/linked-rescue-root"
    else
        db_for_case="$TMP/linked-live-db-parent/voltage.sqlite3"
    fi
    if env PATH="$TMP/rollback-mock-bin:$PATH"         MOCK_STOP_MARKER="$TMP/precheck-stopped"         SVT_ROOT="$TARGET" DATABASE_PATH="$db_for_case"         SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$rescue_for_case"         "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --no-start             --confirm-db-replacement >"$TMP/precheck-$kind.log" 2>&1; then
        echo "ERROR: rollback accepted unsafe $kind" >&2
        exit 1
    fi
    if [[ -e "$TMP/precheck-stopped" ]]; then
        echo "ERROR: rollback stopped writer before $kind path validation" >&2
        exit 1
    fi
    grep -q "ERROR: rollback unsafe backup path"         "$TMP/precheck-$kind.log"
    test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)"         = "$db_before"
    test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)"         = "$source_before"
done
rm -f "$TARGET/compose.yaml"
echo "ROLLBACK_PRECHECK_RESCUE_AND_LIVE_DB=BLOCKED"

# The source destination and all its parents must be unlinkable.
ln -s "$TARGET" "$TMP/linked-target"
ln -s "$TMP" "$TMP/linked-target-parent"
for kind in linked_target linked_parent root_target relative_target \
    traversal_target missing_target; do
    case "$kind" in
        linked_target) rejected="$TMP/linked-target" ;;
        linked_parent) rejected="$TMP/linked-target-parent/target" ;;
        root_target) rejected="/" ;;
        relative_target) rejected="." ;;
        traversal_target) rejected="$TMP/target/../target" ;;
        missing_target) rejected="$TMP/no-such-application" ;;
    esac
    if SVT_ROOT="$rejected" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
        SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --dry-run \
            --no-start >"$TMP/unsafe-target-$kind.log" 2>&1; then
        echo "ERROR: rollback accepted unsafe source target $kind" >&2
        exit 1
    fi
    grep -q "ERROR: rollback unsafe application target" \
        "$TMP/unsafe-target-$kind.log"
    test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" \
        = "$db_before"
    test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" \
        = "$source_before"
done
echo "ROLLBACK_UNSAFE_SOURCE_TARGETS=REJECTED"

# A missing/non-directory SQLite parent must block before Docker stop.
printf 'not a directory\n' > "$TMP/not-a-db-directory"
for kind in missing_parent wrong_parent_type relative_db traversal_db; do
    case "$kind" in
        missing_parent) bad_db="$TMP/missing-db-parent/voltage.sqlite3" ;;
        wrong_parent_type) bad_db="$TMP/not-a-db-directory/voltage.sqlite3" ;;
        relative_db) bad_db="relative.sqlite3" ;;
        traversal_db) bad_db="$DB_DIR/../data/voltage.sqlite3" ;;
    esac
    if SVT_ROOT="$TARGET" DATABASE_PATH="$bad_db" \
        SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --dry-run \
            --no-start >"$TMP/unsafe-db-parent-$kind.log" 2>&1; then
        echo "ERROR: rollback accepted invalid SQLite parent $kind" >&2
        exit 1
    fi
    grep -q "ERROR: rollback unsafe backup path: SQLite directory" \
        "$TMP/unsafe-db-parent-$kind.log"
    test "$(sha256sum "$TARGET/VERSION" | cut -d' ' -f1)" \
        = "$source_before"
    test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" \
        = "$db_before"
done
echo "ROLLBACK_SQLITE_DIRECTORY_PREFLIGHT=BLOCKED"

# Reject corrupt but checksum-consistent archives before touching the target.
for unsafe_type in traversal symlink; do
    unsafe="$TMP/unsafe-$unsafe_type"
    mkdir -p "$unsafe"
    cp "$SNAP/voltage-old.sqlite3.gz" "$unsafe/"
    sha256sum "$unsafe/voltage-old.sqlite3.gz" > "$unsafe/voltage-old.sqlite3.gz.sha256"
    python3 - "$unsafe/source.tar.gz" "$unsafe_type" <<'PY_UNSAFE'
import io
import sys
import tarfile

with tarfile.open(sys.argv[1], "w:gz") as archive:
    if sys.argv[2] == "traversal":
        info = tarfile.TarInfo("../escape.txt")
        payload = b"should-not-extract"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    else:
        info = tarfile.TarInfo("./redirect")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)
PY_UNSAFE
    sha256sum "$unsafe/source.tar.gz" > "$unsafe/source.tar.gz.sha256"
    if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
        SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$unsafe" \
            --dry-run --no-start > "$TMP/unsafe-$unsafe_type.log" 2>&1; then
        echo "ERROR: unsafe $unsafe_type archive was accepted" >&2
        exit 1
    fi
    grep -q "ERROR: invalid source archive" "$TMP/unsafe-$unsafe_type.log"
    test ! -e "$TMP/escape.txt"
    test "$(cat "$TARGET/VERSION")" = "0.4.2-hw"
    echo "ROLLBACK_UNSAFE_$unsafe_type=REJECTED"
done

MISSING_DB="$DB_DIR/missing.sqlite3"
SVT_ROOT="$TARGET" \
DATABASE_PATH="$MISSING_DB" \
SNAPSHOT_ROOT="$SNAP_ROOT" \
RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --dry-run --no-start \
    > "$TMP/missing-db-dry-run.log"
grep -q "ROLLBACK PASS" "$TMP/missing-db-dry-run.log"
test ! -e "$MISSING_DB"
echo "ROLLBACK_MISSING_DB_DRY_RUN=PASS"

# A real rollback without explicit DB-replacement approval MUST fail
# before touching the active source, DB or Docker service.
if SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
    SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --no-start \
        > "$TMP/unapproved-rollback.log" 2>&1; then
    echo "ERROR: unapproved SQLite rewind was accepted" >&2
    exit 1
fi
grep -q "ERROR: would overwrite existing SQLite history" \
    "$TMP/unapproved-rollback.log"
test "$(cat "$TARGET/VERSION")" = "0.4.2-hw"
test "$(find "$RESCUE" -type f 2>/dev/null | wc -l)" -eq 0
echo "ROLLBACK_UNAPPROVED_DB_REWIND=BLOCKED"


# A failed Docker stop or still-running container must block restoration.
mkdir -p "$TMP/mock-bin"
cat > "$TMP/mock-bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
if [[ $1 == compose && $4 == stop ]]; then
    [[ $MOCK_STOP_FAIL == 1 ]] && exit 17
    exit 0
fi
if [[ $1 == inspect ]]; then
    printf '%s\n' "$MOCK_CONTAINER_RUNNING"
    exit 0
fi
exit 98
MOCK_DOCKER
chmod 0755 "$TMP/mock-bin/docker"
touch "$TARGET/compose.yaml"
before_db="$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)"
for case_name in command_failed writer_running; do
    mock_fail=0
    [[ "$case_name" == command_failed ]] && mock_fail=1
    if env PATH="$TMP/mock-bin:$PATH" MOCK_STOP_FAIL="$mock_fail" \
        MOCK_CONTAINER_RUNNING=true SVT_ROOT="$TARGET" \
        DATABASE_PATH="$DB_DIR/voltage.sqlite3" SNAPSHOT_ROOT="$SNAP_ROOT" \
        RESCUE_ROOT="$RESCUE" "$PROJECT/scripts/rollback.sh" \
        --snapshot "$SNAP" --no-start --confirm-db-replacement \
        > "$TMP/rollback-$case_name.log" 2>&1; then
        echo "ERROR: unsafe rollback accepted $case_name" >&2
        exit 1
    fi
    grep -Eq 'ERROR:.*(Docker|writer)' "$TMP/rollback-$case_name.log"
    test "$(cat "$TARGET/VERSION")" = "0.4.2-hw"
    test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$before_db"
    test "$(find "$RESCUE" -type f 2>/dev/null | wc -l)" -eq 0
    echo "ROLLBACK_$case_name=BLOCKED"
done
# Even a successful stop must not allow a writer that restarts later.
cat > "$TMP/mock-bin/docker" <<'MOCK_DOCKER'
#!/usr/bin/env bash
if [[ $1 == compose && $4 == stop ]]; then
    exit 0
fi
if [[ $1 == inspect ]]; then
    if [[ -e "$MOCK_INSPECT_MARKER" ]]; then
        printf 'true\n'
    else
        touch "$MOCK_INSPECT_MARKER"
        printf 'false\n'
    fi
    exit 0
fi
exit 98
MOCK_DOCKER
chmod 0755 "$TMP/mock-bin/docker"
if env PATH="$TMP/mock-bin:$PATH" \
    MOCK_INSPECT_MARKER="$TMP/inspect-marker" SVT_ROOT="$TARGET" \
    DATABASE_PATH="$DB_DIR/voltage.sqlite3" SNAPSHOT_ROOT="$SNAP_ROOT" \
    RESCUE_ROOT="$RESCUE" "$PROJECT/scripts/rollback.sh" \
    --snapshot "$SNAP" --no-start --confirm-db-replacement \
    > "$TMP/rollback-restarted.log" 2>&1; then
    echo "ERROR: restarted writer was accepted" >&2
    exit 1
fi
grep -q "ERROR: Docker writer is still running" "$TMP/rollback-restarted.log"
test "$(cat "$TARGET/VERSION")" = "0.4.2-hw"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" = "$before_db"
echo "ROLLBACK_writer_restarted=BLOCKED"
rm -rf "$RESCUE"
rm "$TARGET/compose.yaml"

# The existing Docker writer must block replacement even if compose.yaml
# is missing. Use a fresh synthetic copy so a RED test cannot harm state.
MISSING_COMPOSE_TARGET="$TMP/target-missing-compose"
MISSING_COMPOSE_DB="$TMP/missing-compose.sqlite3"
cp -a "$TARGET" "$MISSING_COMPOSE_TARGET"
cp "$DB_DIR/voltage.sqlite3" "$MISSING_COMPOSE_DB"
before_missing_db="$(sha256sum "$MISSING_COMPOSE_DB" | cut -d' ' -f1)"
cat > "$TMP/mock-bin/docker" <<'MOCK_MISSING_COMPOSE_DOCKER'
#!/usr/bin/env bash
if [[ $1 == inspect ]]; then
    [[ "$MOCK_INSPECTION_ERROR" == 1 ]] && exit 17
    printf 'true\n'
    exit 0
fi
exit 98
MOCK_MISSING_COMPOSE_DOCKER
chmod 0755 "$TMP/mock-bin/docker"
for case_name in running_writer unknown_writer; do
    inspect_error=0
    [[ "$case_name" == unknown_writer ]] && inspect_error=1
    if env PATH="$TMP/mock-bin:$PATH" \
        MOCK_INSPECTION_ERROR="$inspect_error" \
        SVT_ROOT="$MISSING_COMPOSE_TARGET" \
        DATABASE_PATH="$MISSING_COMPOSE_DB" \
        SNAPSHOT_ROOT="$SNAP_ROOT" \
        RESCUE_ROOT="$TMP/missing-compose-rescue" \
        "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" \
            --no-start --confirm-db-replacement \
            > "$TMP/missing-compose-$case_name.log" 2>&1; then
        echo "ERROR: rollback accepted absent compose with $case_name" >&2
        exit 1
    fi
    grep -q 'ERROR: Docker writer' \
        "$TMP/missing-compose-$case_name.log"
    test "$(cat "$MISSING_COMPOSE_TARGET/VERSION")" = "0.4.2-hw"
    test "$(sha256sum "$MISSING_COMPOSE_DB" | cut -d' ' -f1)" \
        = "$before_missing_db"
    test ! -e "$TMP/missing-compose-rescue"
    echo "ROLLBACK_missing_compose_$case_name=BLOCKED"
done

# A deliberately stopped synthetic Docker writer permits the test rollback.
cat > "$TMP/mock-bin/docker" <<'MOCK_STOPPED_DOCKER'
#!/usr/bin/env bash
if [[ $1 == inspect ]]; then
    printf 'false\n'
    exit 0
fi
exit 98
MOCK_STOPPED_DOCKER
chmod 0755 "$TMP/mock-bin/docker"

# Check restored SQLite is private BEFORE gzip writes its first byte.
cat > "$TMP/mock-bin/gzip" <<'MOCK_PRIVATE_GZIP'
#!/usr/bin/env bash
set -euo pipefail
if [[ $1 == -dc ]]; then
    permissions="$(stat -Lc '%a' "/proc/$$/fd/1")"
    printf '%s\n' "$permissions" > "$RESTORE_MODE_REPORT"
    if [[ "$permissions" != 600 ]]; then
        echo "ERROR: restored SQLite temp not private before gzip" >&2
        exit 89
    fi
fi
exec /usr/bin/gzip "$@"
MOCK_PRIVATE_GZIP
chmod 0755 "$TMP/mock-bin/gzip"
# Simulate a usual service account shell, not an inherited restrictive umask.
umask 022

RESTORE_MODE_REPORT="$TMP/restore-mode" PATH="$TMP/mock-bin:$PATH" \
SVT_ROOT="$TARGET" \
DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
SNAPSHOT_ROOT="$SNAP_ROOT" \
RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$SNAP" --no-start \
        --confirm-db-replacement

test "$(cat "$TMP/restore-mode")" = 600
test -z "$(find "$DB_DIR" -maxdepth 1 -name '*.restore.*' -print -quit)"
echo "ROLLBACK_SQLITE_PRIVATE_RESTORE_TEMP=PASS"

# Rescue backup is always inside a protected owner-only directory.
rescue_dir="$(find "$RESCUE" -mindepth 1 -maxdepth 1 -type d -print -quit)"
test -n "$rescue_dir"
test "$(stat -c %a -- "$rescue_dir")" = 700
echo "ROLLBACK_RESCUE_DESTINATION_PRIVATE=PASS"

test "$(cat "$TARGET/VERSION")" = "0.4.1-hw"
test -f "$TARGET/OLD_ONLY"
test ! -e "$TARGET/NEW_ONLY"
grep -qx 'KEEP_ME=1' "$TARGET/.env"
grep -qx 'BACKUP_KEEP_ME=1' "$TARGET/.env.pre-upgrade"
grep -qx 'keep private QA results' "$TARGET/validation/acceptance.txt"
grep -qx 'secret-preserved' "$TARGET/secrets/token"
grep -qx 'local operator checkpoint' "$TARGET/ROADMAP.md"
grep -qx 'local prior checkpoint' "$TARGET/ROADMAP.md.pre-test"
grep -qx 'local protected lock' "$TARGET/.roadmap-update.lock"
grep -qx 'local acceptance lock' "$TARGET/.telegram-private-acceptance.lock"
grep -qx 'local progress' "$TARGET/.work-state/firmware-progress.json"
echo "ROLLBACK_OPERATOR_STATE_PRESERVED=PASS"

python3 - "$DB_DIR/voltage.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0] == "1"
    assert db.execute("SELECT voltage FROM measurements").fetchone()[0] == 111.0
PY

RESCUE_DB="$(find "$RESCUE" -type f -name 'voltage-*.sqlite3.gz' | head -n1)"
test -n "$RESCUE_DB"
gzip -dc "$RESCUE_DB" > "$TMP/rescue.sqlite3"
python3 - "$TMP/rescue.sqlite3" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as db:
    assert db.execute("SELECT voltage FROM measurements").fetchone()[0] == 222.0
PY

echo "ROLLBACK_FIXTURE PASS"

# A mid-decompression failure must remove mode-0600 partial SQLite data.
before_failed_restore="$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)"
cat > "$TMP/mock-bin/gzip" <<'MOCK_FAILED_GZIP'
#!/usr/bin/env bash
set -euo pipefail
if [[ $1 == -dc ]]; then
    permissions="$(stat -Lc '%a' "/proc/$$/fd/1")"
    if [[ "$permissions" != 600 ]]; then
        echo "ERROR: partial SQLite temp opened too broadly" >&2
        exit 91
    fi
    printf 'synthetic-partial-private-database-bytes'
    echo "ERROR: injected gzip failure before database restore" >&2
    exit 92
fi
exec /usr/bin/gzip "$@"
MOCK_FAILED_GZIP
chmod 0755 "$TMP/mock-bin/gzip"
if PATH="$TMP/mock-bin:$PATH" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
    bash -c '
        set -euo pipefail
        source <(sed '\''$d'\'' "$1")
        SNAPSHOT="$2"
        restore_database
    ' _ "$PROJECT/scripts/rollback.sh" "$SNAP" \
        > "$TMP/partial-restore.log" 2>&1; then
    echo "ERROR: partial gzip failure was accepted" >&2
    exit 1
fi
grep -q "injected gzip failure" "$TMP/partial-restore.log"
test "$(sha256sum "$DB_DIR/voltage.sqlite3" | cut -d' ' -f1)" \
    = "$before_failed_restore"
test -z "$(find "$DB_DIR" -maxdepth 1 -name '*.restore.*' -print -quit)"
echo "ROLLBACK_PARTIAL_SQLITE_TEMP_CLEANUP=PASS"

# A SHA256-valid gzip must be verified as SQLite before any service stop.
INVALID_DB="$TMP/invalid-sqlite-snapshot"
mkdir -p "$INVALID_DB"
cp "$SNAP/source.tar.gz" "$INVALID_DB/"
sha256sum "$INVALID_DB/source.tar.gz" > "$INVALID_DB/source.tar.gz.sha256"
printf "%s" "a corrupt SQLite database" | gzip -c \
    > "$INVALID_DB/voltage-corrupt.sqlite3.gz"
sha256sum "$INVALID_DB/voltage-corrupt.sqlite3.gz" \
    > "$INVALID_DB/voltage-corrupt.sqlite3.gz.sha256"
set +e
SVT_ROOT="$TARGET" DATABASE_PATH="$DB_DIR/voltage.sqlite3" \
SNAPSHOT_ROOT="$SNAP_ROOT" RESCUE_ROOT="$RESCUE" \
    "$PROJECT/scripts/rollback.sh" --snapshot "$INVALID_DB" \
        --dry-run --no-start > "$TMP/corrupt-sqlite-rollback.log" 2>&1
invalid_status=$?
set -e
test "$invalid_status" -eq 2
! grep -q "DRY-RUN: stop Compose service" "$TMP/corrupt-sqlite-rollback.log"
echo "ROLLBACK_BAD_SQLITE_REJECTED_BEFORE_STOP=PASS"
