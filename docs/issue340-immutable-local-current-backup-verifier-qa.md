# Issue #340 — immutable read-only verification of real local preparation copies

**QA-only stacked Draft. A verified archive is not a release GO.**

The owner will use **one NVMe** without a separate physical backup
medium. Earlier Drafts #377–#378 demonstrated two local SQLite
online snapshots with active WAL, two disposable restores and
snapshot-specific integrity under concurrent synthetic writes.
During a real **preparation** snapshot on production LXC110, both
current-A.sqlite3 and current-B.sqlite3 captured 72,690 records
at 18:33 Kyiv 2026-11-15, were independently restored and
validated, and a 0600 manifest was written in a private
0700 backup directory.

An initial follow-up inspection with SQLite `mode=ro` created
auxiliary **empty** `-wal` files and 32768-byte `-shm` files
for restored snapshots. After their exact path, size, mode and
ownership were checked, only these auxiliary files were deleted
from the new backup directory; live SQLite WAL/SHM was untouched.
Independent verification using `mode=ro&immutable=1` left no
sidecar files. That actual observation motivates this verifier.

## New repeatable read-only verifier

`scripts/verify-local-release-backups.py`:

- Default **inert**; `--verify --backup-dir PATH` required
  to inspect. Supports `-h/--help`.
- Reads only private root-owned (or current invoking UID-owned)
  0700 backup directories and exactly three files:
  `current-A.sqlite3`, `current-B.sqlite3`,
  `manifest.json`. Files must be regular, 0600,
  single-hardlink, nonsymlink and owned by the user running it.
- Rejects missing/extraneous files, including any leftover WAL,
  SHM, partial, or unrecognized file.
- Checks a bounded JSON manifest against the exact
  `single_nvme_online_release_preparation` schema,
  with `physical_failure_domains=1`,
  `last_cutover_snapshot=false`, `release_approval=false`
  and `runtime_signature_reverified_in_this_run=false`.
  An invented claim of cosign/Owner Approval is **rejected**.
- Requires two expected snapshots, exact bytes, SHA256 before
  and after inspection, and equality to **per-snapshot** SQLite
  `quick_check=ok`, measurement row count, last timestamp,
  and SHA256 of the SQLite schema.
- Uses only `file:...?...mode=ro&immutable=1` for each **finished,
  standalone** backup. It never runs a checkpoint, modifies live
  SQLite, or creates sidecars. Immutable is appropriate only
  for these finished copies after absence of `-wal`/`-shm`
  has been verified; **never** assume an active live SQLite DB
  is safe to read with immutable mode.
- Produces stable `PASS_LOCAL_COPY_READ_ONLY` or `FAIL_LOCAL_COPY`
  with nonzero exit on failure; a good result still explicitly
  includes `release_decision=NO_GO`.

### Actual current production copy inspection

Executed through Gateway by streaming the QA file into
`pct exec 110 -- python3 - --verify --backup-dir
/var/backups/shelly-voltage/release-prep-current-20261115-183320`.
It was **not installed** in production.

**PASS_LOCAL_COPY_READ_ONLY: A=72,690, B=72,690**;
exactly three files, no sidecar write, checksums and SQLite
state matched. Current live database continued growing afterward
(72,764 rows at 18:45). Thus these are **preparation snapshots**,
not final cutover snapshots. The signed OCI digest in the manifest
was **observed**, not independently cosign reverified in this
verification.

### QA regression

- **20 new tests PASS**, including default inert, exact
  source identity, invalid permissions/ownership, link type,
  changed digests and row count, missing/extra files, invented
  signature/Owner Approval claims and zero side effects.
- **286/286 total tests PASS** under both root and UID65534,
  `python -W error::ResourceWarning`, Ruff format and
  E/F/I/UP/SIM; new functions <=40 lines and source width <=79.
- Actual current production copies inspected read-only; no
  production image, SQLite, restart policy, systemd or Telegram
  changed by the verification.
- No additional backup-medium procurement.

```bash
python3 scripts/verify-local-release-backups.py -h
python3 scripts/verify-local-release-backups.py
python3 scripts/verify-local-release-backups.py --verify \
  --backup-dir /var/backups/shelly-voltage/release-prep-current-20261115-183320
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_verify_local_release_backups.py -q
```

## Remaining release gates

Read-only verification of **preparation snapshots** does not prove a
WAL-complete CURRENT cutover after old writers stop, does not
protect against loss of the sole NVMe, and does not authorize
a rollback. Required next: freeze **every** old Docker/systemd/
Compose/updater/recover/installer/rollback/emergency start actor,
verify the old writer is actually quiescent, capture an atomic
whole-CURRENT cutover boundary with two individually restorable
local snapshots and trusted signed OCI/source/config provenance,
then complete G/B/W/A handoff, real Telegram long-poll shutdown,
power-loss consistency QA, review and explicit Owner Approval.
Owner Approval must state the unrecoverable whole-NVMe risk.
Public pinned TEXT-to-PHOTO also remains separate NO_GO.

**Do not merge/deploy based only on green CI.**
