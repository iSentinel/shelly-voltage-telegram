# Issue #340 — local WAL backups during concurrent synthetic writes

**QA-only, stacked on Draft #377. No production SQLite access.**

An SQLite online backup can capture a **consistent point in time**
while another independent SQLite connection commits new rows to WAL.
The *two backups need not represent the same point in time*.
A manifest tied to a later live row count must never falsely
invalidate an older intact snapshot or claim that the older copy
contains all readings as of the release cutover.

## Actual isolated LXC114 test

`scripts/disposable-concurrent-wal-backup-qa.py` uses only
the sibling single-NVMe test helpers from Draft #377,
is inert by default, and requires exact
`--run-disposable --confirm
OFFLINE_CONCURRENT_WAL_BACKUP_QA_ONLY`.
The fixed-host guard requires root, hostname
`shelly-docker-reboot-qa` and only interface `lo`.
No Docker, actual Shelly, live SQLite, Telegram
or external network is touched.

- Create 420 invented committed rows in a SQLite source
  with live WAL (autocheckpoint disabled).
- Start a **separate Python thread with its own SQLite
  connection**, making repeated individually committed
  inserted rows while backup work proceeds.
- Take snapshot A using SQLite's online backup API,
  allow the writer to continue, then take snapshot B.
- Open each backup read-only to record **its own** immutable
  state and SHA256 checksum, *not* a shared late live count.
- Restore each backup independently to a different
  disposable path using checksum checks and expected
  snapshot-specific row count/sum/max ID.
- Require `quick_check=ok` and complete increasing row IDs
  (`sum(id)=rows*(rows+1)/2`, `max(id)=rows`) for each;
  require B has more committed rows than A.
- Terminate and join the synthetic writer thread within
  a bounded deadline and delete all scratch data.

**Actual PASS, repeated:** snapshot A had **421 rows**,
snapshot B had **433 rows**. Both independent restores
passed SQLite quick_check, row ordering and their own
snapshot-specific checksums; no torn sequence or leaked
threads. No disposable files remain.

## QA regression coverage

- **17/17** new unit tests PASS: inert CLI/exact consent,
  fixed source module, valid/torn/empty/stale row state,
  true active WAL, snapshot identity differences,
  tamper rejection, thread cleanup, explicit guard
  order, and failures returning a nonzero code.
- Combined stacked QA: **266/266 tests PASS under root
  and unprivileged UID65534**, Ruff format E/F/I/UP/SIM
  PASS and new functions <=40 lines, width <=79.
- GitHub CI checks the safe unit suite only. The actual
  threaded WAL integration ran on exact offline LXC114.

```bash
python3 scripts/disposable-concurrent-wal-backup-qa.py --help
python3 scripts/disposable-concurrent-wal-backup-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_concurrent_wal_backup_qa.py -v

# Exact isolated LXC114 acceptance:
python3 scripts/disposable-concurrent-wal-backup-qa.py \
  --run-disposable \
  --confirm OFFLINE_CONCURRENT_WAL_BACKUP_QA_ONLY
```

## Production limitations and cutover rule

**This tests synthetic data only**, not any snapshot of LXC110.
In production, a backup taken during ongoing measurements may be
consistent but will immediately lag later measurements. A release
that replaces live SQLite must first **quiesce the old writer and
freeze every other entrypoint** so a final CURRENT snapshot is
taken at an explicit cutover boundary and no subsequent records
are silently lost. Backing up online is not evidence the new
owner global admission, complete WAL recovery or signed-source
provenance is ready.

The owner will not install a second backup medium. Both backups
are therefore on the same NVMe: they support **software-error
rollback**, not recovery from failure of that NVMe or its host.
Final Owner Approval must separately acknowledge this risk.
No cryptographic production OCI signature check, real Telegram
long-poll shutdown, physical power-loss QA, or all-owner
G EX → quiesce old → B EX → A EX release handoff is completed.
Public pinned PHOTO change remains independently NO_GO.

**Do not merge or deploy based only on green QA.**
