# Issue #340 — two independent CURRENT-history SQLite A/B restore QA

**QA ONLY. No source installation, Docker restart, live SQLite snapshot,
release upgrade, rollback, Telegram write or credential access.**

## Objective

Before any approved production maintenance, the Shelly monitoring
release process needs two independent complete CURRENT SQLite history
backup-and-restore paths coupled to the exact signed source/OCI
identity. Existing rollback archives may be stale; saving only the
SQLite main file while WAL has committed data can lose history.

This PR supplies a **synthetic, default-inert, networkless acceptance
fixture** for the data-parity portion of that requirement. It does
not claim that a real signed release or a live source+database A/B
restore has been completed.

## How this offline test works

Only on verified isolated LXC114
`shelly-docker-reboot-qa`, with exactly the `lo` network
interface, stopped original QA writer (restart=no), and disabled
original QA systemd owners, the script creates a brand-new
mode0700 `/var/tmp/svt-current-ab-qa-*` directory.

The invented SQLite schema includes:

- a measurements-like table, unique keys and timestamp index;
- an outages table including an open-outage row;
- metadata records, `PRAGMA user_version` and
  `application_id`;
- a SQL trigger;
- 256 initial readings and two additional independently committed
  WAL readings (258 total in clean case).

Each A/B copy is separately produced with SQLite
`Connection.backup()`, which includes committed WAL transactions
in one consistent backup. **It is not a raw file copy of the
live `.sqlite3` without its WAL.** Each copy is restored
independently into a distinct database, checked using SQLite
`quick_check`, full schema, **all** logical records and SQLite
header metadata. Logical data is hashed and compared with the
CURRENT source snapshot.

A separate invented static `source.fixture` is copied into A
and B, with SHA-256 content parity. **This is not a cosign
signature, source archive, GHCR OCI provenance check or real
release trust decision.**

## Actual LXC114 acceptance — 2026-11-15

| Case | Result |
| --- | --- |
| A and B independent restores, 258 current WAL-inclusive readings | A PASS / B PASS |
| A archived, new committed WAL event, then B archived | A BLOCKED_STALE / B PASS |
| B SQLite header deliberately corrupted | B BLOCKED_CORRUPT |

**3/3 real synthetic SQLite acceptance cases PASS.**
**17/17 unit tests PASS** with
`-W error::ResourceWarning`, Ruff 0.14.2 quality PASS,
PEP8, function length <=40 and source line width <=79.
Tests cover equal-count row edits, changed schema/user_version,
source SHA mismatch, SQLite corruption, idempotent independent
restores, permissions and exact offline guest consent.

All disposable SQLite history, source blobs, restores and temp dirs
are deleted after execution. No actual production database or
source files were ever read or modified by this QA tool.

## Reproduction

No action by default:

```bash
python3 scripts/disposable-current-history-ab-qa.py --help
python3 scripts/disposable-current-history-ab-qa.py

python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_current_history_ab_qa.py -v
```

Only on the exact networkless LXC114 after the restricted preflight:

```bash
python3 scripts/disposable-current-history-ab-qa.py \
  --run-disposable \
  --confirm OFFLINE_SYNTHETIC_CURRENT_SQLITE_AB_QA_ONLY
```

Any other hostname, external network interface, running original
QA writer, unexpected restart policy, enabled original QA owners
or missing exact consent must fail closed.

## What remains production NO-GO

- Global noncircular **B EX / A EX maintenance ownership** and
  lifetime-held **B SH writer supervisor**, with no Docker
  auto-restart or detached start gap, must first be reviewed
  and implemented. Real offline owner issues are detailed
  in draft PR #360.
- Actual **CURRENT** production SQLite (all logical tables,
  WAL, metadata and concurrently committed writes) has NOT
  been frozen or copied. This tool never tries to do so.
- A and B must live in independently verified, independently
  restorable storage destinations; two folders on one disk
  are **not independent failure domains**.
- The exact **real cryptographically signed release source**
  and immutable GHCR image digest must be verified together
  with each A/B artifact. This QA fixture uses only invented
  static bytes and SHA-256 and proves no signature identity.
- The real update/rollback, interrupted boot, prestart
  recovery and fresh SQLite WAL changes must survive a
  hardware power-loss test on sacrificial hardware.
- Independent reviewer signoff and **explicit Owner Approval**
  are required before changing live source, restart policy,
  Docker services or current SQLite.
- Telegram pinned TEXT-to-PHOTO is separately **NO-GO**
  until its own acceptance.

**Decision: REVIEW-ONLY, NOT FOR MERGE/DEPLOY ON GREEN CI ALONE.**

Related: #335, #336, #340, #355, #358, #359, #360.

## Read-only actual backup inventory — 2026-11-15

A separate **read-only** Gateway LXC110 inspection, not part of the
synthetic QA runner, found the current backup service
`shelly-voltage-backup.service` invokes
`/opt/shelly-voltage/scripts/backup.sh` daily. The timer last ran
at **03:22 Kyiv on Nov 15**. The script defaults to
`/var/backups/shelly-voltage` and retains **14 archives**.

Verified mountpoint identity for
`/var/lib/shelly-voltage`, `/var/lib/shelly-voltage-updater`,
`/opt/shelly-voltage`, and the actual backup directory:
all resolve to **the same ext4 virtual block device**
`/dev/mapper/pve-vm--110--disk--0` (st_dev=64536).

The latest historic backup was verified **without modifying any
archive or writing a database file to disk**:

| Check | Read-only result |
| --- | --- |
| Sidecar SHA-256 equals compressed archive hash | PASS |
| Gzip decompression with 128 MiB maximum | PASS |
| Decompressed SQLite bytes | 4,886,528 |
| SQLite quick_check in a memory-only clone | OK |
| Rows in historical archive | 67,266 |
| Rows in current live SQLite at same inspection | 70,360 |
| CURRENT-history row-count parity | NO |

To inspect the online-backup SQLite file in `:memory:`, its
journal read/write-version header (2/2 WAL mode) was temporarily
normalized to 1/1 **in an in-memory bytearray only** before
`sqlite3.deserialize`. Original archive bytes, sidecar, backup
directory, live DB, runtime and systemd units were untouched.

**Conclusion:** automatic archives are useful and the most recent
archive passed basic checksum/integrity, but they are NOT two
independent physical A/B copies and the latest nightly archive is
NOT equal to the current live database. It must never be silently
substituted for an approved full-CURRENT rollback image.
Actual signed source and independent physical-failure-domain
restores still require owner-approved testing before deployment.
