# Issue #340 — repeatable local single-NVMe preparation backup builder

**QA-only stacked Draft. Do not activate as an automated production owner.**

The owner explicitly rejected a separate physical backup medium. The
local software rollback plan uses two private SQLite online snapshots,
SHA-256 manifest and **separate read-only restored-copy verification**
on the existing single NVMe. These are preparation copies, not final
cutover files or hardware-failure disaster recovery.

This change adds `scripts/create-local-preparation-backups.py` on
top of the independent immutable verifier in Draft #379 and the
stopped-writer cutover model in Draft #380. It is **inert by default**:
`-h/--help` and no-argument dry-run do not open any database or
write a backup. Explicit `--prepare`, full local paths, an
unambiguous new directory name, pinned image digest and exact
`--confirm SINGLE_NVME_PREPARATION_BACKUP_ONLY` are required.

The builder:
1. Rejects nonexistent or symlinked SQLite source **before**
   looking up free space; validates output directory, UID, available
   capacity, new name and full observed image digest.
2. Creates one **new** 0700 output directory only. Never reuses or
   overwrites a previous archive name.
3. Opens the supplied SQLite source in mode=ro, allowing committed
   WAL changes in SQLite `Connection.backup()` (not a raw copy).
4. Generates separate atomic 0600 A/B snapshots, with fsync before
   rename, hashes, byte counts and per-snapshot schema, row count,
   latest timestamp and `PRAGMA quick_check` state.
5. Copies and independently opens each snapshot as an immutable
   **finished** SQLite database, verifying checksum and identity;
   removes only its own temporary copied test destinations.
6. Writes 0600 JSON manifest and invokes the existing immutable
   verifier, which requires exactly two snapshots plus manifest.
7. Explicitly marks `physical_failure_domains=1`,
   `last_cutover_snapshot=false`,
   `runtime_signature_reverified_in_this_run=false`, and
   `release_approval=false`. A passing preparation remains NO_GO.

The tool does **not** stop Shelly, change runtime Docker/systemd,
touch Telegram, sign an image, authenticate the observed digest,
or authorize a rollback. On failure, inspect the new private
directory and treat the incomplete result as **unaccepted**;
do not silently overwrite existing historical backup data.

## Offline evidence

On loopback-only LXC114, an invented 60-row SQLite was saved in
two snapshots with a private manifest. Both copies were verified
by the immutable verifier, with **zero WAL/SHM sidecars** and
the exact expected file set. A second run with the same name was
refused (`FileExistsError`) without touching the prior backup.
The disposable directory was removed.

**14/14 new safety unit tests PASS**, including inert CLI,
exact consent, missing DB, symlink source, wrong output root,
bad OCI digest, invalid destination names/traversal, existing
archive refusal, source not changed, private files and manifest,
and fail-closed errors. The initial missing-source QA exposed
a preflight ordering bug; it was fixed and the regression passed.

**314/314 combined stacked strict QA tests PASS** under root
and UID65534, `-W error::ResourceWarning`, Ruff format/E/F/I/UP/SIM
and source lines <=79/new functions <=40.

### Example QA-only commands

```bash
python3 scripts/create-local-preparation-backups.py --help
python3 scripts/create-local-preparation-backups.py

# Only an already-approved, private local test source:
python3 scripts/create-local-preparation-backups.py \
  --prepare \
  --confirm SINGLE_NVME_PREPARATION_BACKUP_ONLY \
  --database /path/to/qa-synthetic.sqlite3 \
  --output-root /path/to/private/qa-backups \
  --name release-prep-qa-snapshot \
  --image offline-qa@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

## Production release blockers remain

The existing real LXC110 preparation copies made at 18:33 Kyiv
2026-11-15 contain 72,690 rows each, were individually restored
and passed immutable verification. At 21:14 live LXC110 already
contained 73,649 measurements, so these older preparation copies
are **not** a final current cutover state and cannot be used for
unattended rollback without losing newer measurements.

A real release still requires external freeze of **all** old
unguarded Docker/systemd/Compose/updater/recovery/installer/rollback/
emergency/manual paths, verified writer quiescence, non-circular
G EX→B EX→A EX, two final CURRENT local copies tied to a
signed source/OCI release manifest, real Telegram longpoll
shutdown, isolated physical power-loss consistency acceptance
and explicit Owner Approval. Owner must explicitly acknowledge
single-NVMe total-history-loss risk; the refusal to add a
second disk is not an approval. Public pinned PHOTO remains
a separate NO_GO.

**Do not merge or deploy based only on QA or CI success.**
