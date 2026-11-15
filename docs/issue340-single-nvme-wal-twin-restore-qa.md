# Issue #340 — two local-only WAL-complete SQLite restore checks

**QA-only Draft, single NVMe. Not proof of production recovery.**

The owner confirmed that no separate physical backup medium will
be provisioned. This stage implements a reproducible software-rollback
acceptance harness on exact offline LXC114. It does **not** make two
local directories into independent hardware failure domains.

## Actual isolated WAL experiment

Script: `scripts/disposable-local-wal-rollback-qa.py`

Default inert and supports `-h/--help`. Real disposable execution
requires `--run-disposable --confirm
OFFLINE_LOCAL_WAL_TWO_RESTORES_QA_ONLY`. The guard verifies root,
the exact `shelly-docker-reboot-qa` guest and only the loopback NIC.
No production database, network, Docker image, Shelly device or
Telegram token is used.

On each run it:

1. Creates a new **synthetic** SQLite with
   `PRAGMA journal_mode=WAL`, disabled autocheckpoint,
   420 committed invented measurements and a nonempty active WAL.
2. Uses the real SQLite `Connection.backup()` online API
   **twice**, writing independent same-filesystem snapshots
   `local-A.sqlite3` and `local-B.sqlite3` via 0600
   partial files, fsync and atomic rename.
3. Creates a 0600 manifest containing expected integrity/row state,
   SHA-256 of each backup, QA application source SHA-256 and the
   fixed *cached* QA image ID. **It explicitly marks
   `signature_verified=false`**: a hash or cached image name is not
   cryptographic signature/provenance verification.
4. Restores each backup to a **different disposable destination**,
   verifies the corresponding checksum before copy and after copy,
   opens each restored database read-only, runs
   `PRAGMA quick_check` and checks exact row count, sum and max ID.
5. Deliberately modifies an independent copy and confirms
   **checksum rejection before restore**. Deliberately changes the
   expected row state and confirms **stale-state rejection**.
6. Discards all scratch data automatically; it never touches the
   running Shelly SQLite or production backup archives.

**Actual isolated result: PASS.** Both recovered databases had
`quick_check=ok`, 420 rows, `sum(id)=88410`, max ID 420,
matching state and digests. Damaged and stale fixtures rejected,
zero disposable scratch remaining. All snapshots are on the
**same filesystem and same NVMe** and provide only local
software rollback protection.

## Regression coverage

**18 new strict unit safety tests** cover inert CLI, exact consent,
fixed guest/no external NIC, real WAL generation, SQLite backup
atomicity and 0600 mode, manifest privacy, refusal to overwrite
files, source/image identity being unsigned QA evidence only,
both independent local restorations, rejection of altered,
stale and existing-target files, and explicit NO_GO status.

The combined stacked QA workspace passes **249/249 tests** under
root and UID65534, Ruff format/E/F/I/UP/SIM, all new functions
<=40 lines and source width <=79 characters. GitHub CI runs
the **unit tests only**; the actual disposable offline
integration was executed with explicit consent on LXC114.

```bash
python3 scripts/disposable-local-wal-rollback-qa.py -h
python3 scripts/disposable-local-wal-rollback-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_local_wal_rollback_qa.py -v

# Exact offline LXC114 consent:
python3 scripts/disposable-local-wal-rollback-qa.py \
  --run-disposable \
  --confirm OFFLINE_LOCAL_WAL_TWO_RESTORES_QA_ONLY
```

## What is still unaccepted

- This uses **synthetic** rows, not the whole CURRENT LXC110
  database or its actual transient WAL. It does not verify real
  Telegram state, all production config, source or manifests.
- Both local copies can be lost in the **same physical NVMe
  failure**; no zero-loss disaster recovery is claimed.
- The fixed QA image ID is not a fresh `cosign verify`
  of the candidate production release, and the manifest
  itself is not a signed release bundle.
- Backup restoration into a live Docker/SQLite target is not
  attempted. The old ungated OCI still bypasses G, so the
  complete owner start/stop/updater/recover/install/rollback
  sequence must be reviewed and accepted before any mutation.
- Actual current local backups and **two read-only restores**,
  coordinated source/digest provenance, crash/power-loss
  acceptance, real Telegram long-poll shutdown and independent
  review are still pending.
- **Explicit Owner Approval** must separately acknowledge
  the permanent history-loss risk of a single NVMe. Choosing
  not to add a second disk is not itself acceptance of data loss.

**Production release NO_GO.** Public pinned Telegram PHOTO
also remains separately NO_GO. The current signed production
image, SQLite, Docker policy, systemd, backups, and Telegram
were not modified by this QA work.
