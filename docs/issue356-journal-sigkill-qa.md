# Issue #356: offline durable-status journal SIGKILL model

**QA-ONLY / REVIEW ONLY. No production code, updater, Docker, systemd,
SQLite, Telegram, release, or credentials are changed by this PR.**

## Why this exists

Draft stacked PR #358 fixes an observed *status publication ordering* bug
in the candidate Bash updater. It reports failure if public metadata
publication fails, instead of clearing `pending-update` and printing
`UPDATE PASS`. However, its fault injection demonstrates an unresolved
durability gap: `unlink(pending-update)` may succeed and subsequent
directory `fsync` may fail or the process may die. A missing pending
marker cannot by itself prove an update committed.

This standalone Python QA model illustrates a proposed **durable release
journal**, not a complete production implementation. It models the
phases `prepared`, `published` and `committed`.

## Synthetic commit protocol

1. Stage invented immutable version/digest, pending marker and durable
   `prepared` journal (0600). Keep private/public status on the old
   version at this point.
2. Simulate new private status write, then new public status write (0644).
   Writes use same-directory temp files, fsync(file), os.replace and
   fsync(directory).
3. Persist `published` journal before unlinking pending.
4. Unlink the pending marker and fsync its parent directory.
5. Persist `committed` journal, then report commit.

The *reconciler* checks the journal release, phase, private/public
deployed version+digest, a separately supplied trust result, exact
running immutable OCI ref and running image ID. It **does not** equate a
missing pending marker with committed state: a trusted, complete
`published` transaction reports `RECONCILE_REQUIRED`.

Only a synthetic exclusive maintenance **B** lock followed by exclusive
updater **A** lock permits the model to finish reconciliation. If
either lock is busy, the journal remains uncommitted. All locks are
**fresh private QA inodes**, not the real updater lock. This does not
prove that real Docker/systemd/installer/rollback owners obey them.

## Reproduce

Default, inert invocation:

```bash
python3 scripts/disposable-status-journal-recovery-qa.py --help
python3 scripts/disposable-status-journal-recovery-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_status_journal_recovery_qa.py -v
```

Only in the user-owned, **networkless loopback-only LXC114**
`shelly-docker-reboot-qa` with original QA writer stopped,
restart=no, original QA Compose/watchdog disabled:

```bash
python3 scripts/disposable-status-journal-recovery-qa.py \
  --run-disposable \
  --confirm OFFLINE_JOURNAL_SIGKILL_QA_ONLY
```

The runner requires exact hostname, root, only `lo`, stopped
QA Docker writer and disabled QA systemd owners. It creates
fresh `/var/tmp/svt-journal-fault-*` private mode-0700
invented files, never touches production paths, never runs
Docker start/stop or contacts Telegram. All scratch paths are
deleted after test execution. Worker mode is restricted to
the QA guest and controlled scratch prefix.

## Actual isolated acceptance — 2026-11-15

Each case spawns a **real child process** and forcibly ends it with
**SIGKILL** at the specified metadata boundary:

| SIGKILL after | Initial classification | After model |
| --- | --- | --- |
| prepared | BLOCKED_PREPARED | BLOCKED_PREPARED |
| private-written | BLOCKED_PREPARED | BLOCKED_PREPARED |
| public-written | BLOCKED_PREPARED | BLOCKED_PREPARED |
| published journal persisted | RECONCILE_REQUIRED | COMMITTED |
| pending unlinked, before directory fsync | RECONCILE_REQUIRED | COMMITTED |
| pending unlinked and directory fsynced | RECONCILE_REQUIRED | COMMITTED |
| committed journal persisted | COMMITTED | COMMITTED |

**7/7 real SIGKILL cut points PASS.** Independent **23/23 Python
unit tests PASS** with strict ResourceWarning, Ruff 0.14.2
format/lint PASS, all functions at most 40 source lines,
all Python source lines at most 79 chars.

Negative tests reject missing/corrupt/symlinked journal, missing pending
with old public metadata, different digest or image ID, missing
synthetic trust, inconsistent committed journal and busy B or A lock.
Successful reconciliation is idempotent.

## What this does NOT prove

- `verified=True` in this model is an **invented Boolean**, not real
  cosign, Sigstore certificate identity, image-ID provenance, nor
  actual source/manifest trust. Real production read-only signature
  verification from earlier roadmap checkpoints is a separate fact.
- No production `apply-update.sh`, Docker `restart=unless-stopped`,
  boot recovery, installer, rollback or emergency restore was patched.
  Real boot ownership and complete all-writer B/A fencing are NOT
  implemented by this QA model.
- SIGKILL is **not physical power loss** and does not establish block
  device durability, fsync drive-cache semantics, power-safe journal
  writes or restart correctness across reboot.
- The model does not validate whole current SQLite history, WAL/SHM,
  signed source archive, or independently restored CURRENT-history A/B.
- The journal semantics must be independently reviewed against old
  installed source (0.5.15-hw), signed runtime (0.5.21-hw), and
  rollback/history preservation before any real deployment.
- Public Telegram pinned TEXT-to-PHOTO is a separate unapproved
  release gate.

**Release decision: NO-GO.** Review this as a test contract only.
Refs #340, #356, #357, #358 and #336.
