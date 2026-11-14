# Writer-fenced exact A/B source + SQLite snapshot rehearsal

**Scope: isolated QA only.** This new QA tool demonstrates a *cooperative*
writer-lock protocol and exact source/SQLite parity. It is **not** a
production snapshot executor and must never be used on LXC110's actual
source directory or SQLite database.

## Why an online backup is insufficient

The live host's normal backup.sh uses SQLite's safe online backup API,
which preserves a consistent point-in-time view. However, that backup
does **not** prove the Docker writer was stopped or that source files
and SQLite represent the same stopped-writer instant. Older SQLite
archives cannot replace newly collected live history.

A production owner-approved upgrade still requires a separate
systemd/Docker restart fence, explicit stopped-writer proof, a full A/B
snapshot, signed OCI identity proof, post-boot recovery acceptance and
independent Telegram pinned-post continuity validation.

## QA harness

The script scripts/writer-fenced-snapshot-qa.py refuses paths outside
a private (0700), owner-owned marker-protected directory under /tmp or
/var/tmp. Its layout is:

- .writer-fence-qa: literal DISPOSABLE_WRITER_FENCE_SNAPSHOT_QA
- .writer.lock: existing unlinked private file, mode 0600
- source/: filtered release source fixture and matching versions
- history.sqlite3: disposable SQLite fixture, never the production DB

Commands for a **prepared QA directory only**:

    python3 scripts/writer-fenced-snapshot-qa.py \
        --root /var/tmp/shelly-private-qa-fixture --dry-run

    python3 scripts/writer-fenced-snapshot-qa.py \
        --root /var/tmp/shelly-private-qa-fixture --capture-disposable

The harness takes a non-blocking exclusive flock on the QA writer-lock
file; an active cooperating fixture writer prevents snapshot creation.
The lock **cannot prove the real Docker writer was stopped** and is not
accepted as production authorization. Lock handling does not alter the
actual running container.

With the model writer fenced, two separate private A/B source tar.gz
and SQLite gzip archives are taken and verified against full logical
database state (all tables and persistent SQLite header fields).
The two source archive digests must match, both SQLite digests must
equal the before/after source history, and both restored copies must
pass quick_check. A private JSON manifest includes source version,
SHA256 archive hashes, database logical digest, fence scope and an
explicit false production_release_authorized flag. Files and directories
are synced and only then is the pair published into a fresh snapshots/
directory. No existing pair is overwritten.

## Results and restrictions

- Positive dry run and A/B capture both PASS on fixture data.
- Official staged v0.5.21-hw source filtered into a temporary source
  fixture and captured with a new disposable SQLite database: PASS.
- Both official-source A/B archives checked by the release validator:
  PASS; full SQLite compare-db restoration: PASS.
- Negative fixtures: active cooperative writer, missing/linked/exposed
  fence, missing authorization marker, symlink destinations, mismatched
  source versions, source mutation and SQLite mutation: fail closed.
- A separate subprocess holds the cooperative writer lock: capture
  fails before making an A/B archive; after that process exits the
  disposable snapshot succeeds.
- Fault-injected failure during the second backup and missing source
  essentials never publish an incomplete snapshot pair.
- WAL-backed data appears in both A/B restored fixtures.
- 18 dedicated unit tests PASS with ResourceWarnings treated as errors;
  full Ruff 0.14.2 project quality PASS.
- No live SQLite, Docker, image, credentials or Telegram posts changed.

## Remaining production gates

1. Independently design and review real Docker/systemd writer fencing,
   including restart suppression, process exit and stale locks.
2. Bind archive evidence to exact signed image/source identity and
   capture production source, whole SQLite and private release receipts.
3. Test power loss, partial archives, recovery and restart with a
   **disposable copy** of the entire application under production-like
   paths, ensuring no historical SQLite can overwrite the newest data.
4. Require natural Shelly restoration, stable /ready 200, owner approval
   and external Telegram message/pin verification before live promotion.

This QA code MUST NOT be interpreted as a release gate PASS or a
replacement for the previously denied administrative source install.
