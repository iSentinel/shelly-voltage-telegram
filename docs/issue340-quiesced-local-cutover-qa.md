# Issue #340 — quiesced final local WAL cutover (isolated QA)

**QA-only, stacked on Draft PR #379. Not an executable release.**

Drafts #377–#378 demonstrated that SQLite online backup
captures a consistent committed state even while another
writer is active. Actual concurrent synthetic test copied
421 and 433 rows into two independently consistent snapshots.
The earlier snapshot correctly lacked the later 12 rows.
A release rollback must not silently erase readings added
after a pre-cutover snapshot.

## Acceptance contract

A final CURRENT rollback boundary for production is possible
**only after every legacy process and all its start/restart
owner paths have been frozen and joined**. Current actual
production is NOT in that state. This scenario proves
the narrower ordering for **one cooperative synthetic
writer**, not all production systemd/Docker/updater/rollback
actors.

The default-inert script
`scripts/disposable-quiesced-local-cutover-qa.py`
requires explicit
`--run-disposable --confirm
OFFLINE_QUIESCED_FINAL_WAL_CUTOVER_QA_ONLY`.
The sibling QA guard requires exact LXC114, root and only
loopback NIC. No real network, Telegram, Shelly or
production SQLite is accessed.

1. Create a disposable 420-row SQLite database in WAL mode,
   using the existing local rollback QA helper.
2. Start one **separate cooperative synthetic writer** using
   its own SQLite connection and commit additional rows.
3. Signal the owned test writer to stop; **join its thread**
   and verify it is no longer alive and has no errors.
   A set stop flag alone is explicitly insufficient.
4. Only then take **two** local SQLite online snapshots,
   with WAL committed state included.
5. Independently restore each snapshot with exact
   snapshot-specific SHA256, `PRAGMA quick_check` and
   row-count/sum/max identity.
6. Confirm both restored snapshots have the **same final
   row boundary**, no dangling writer thread and no
   disposable scratch directory.
7. Return `release_decision=NO_GO` and explicitly
   `every_real_production_start_owner_frozen=false`.

**Actual offline result PASS, repeated:** 435 rows were
present at the stopped-writer cutover boundary, and both
independent restored copies matched this exact state.

## Regression

- **14/14 new safety tests PASS**: default inert,
  exact QA consent, approved helper identity, refusal to
  back up before stop, denial of stop flag with still-live
  writer, SQLite writer errors, no false cutover before
  committed rows, both snapshot restores and no thread
  leaks, fail-closed CLI behavior.
- **300/300 combined strict QA tests PASS** under
  root and UID65534; Ruff format E/F/I/UP/SIM,
  source width <=79 and new functions <=40 lines.
- Repeated actual offline integration PASS, no leftovers.
- GitHub CI executes unit tests only; actual integration
  requires explicit offline LXC114 consent.

```bash
python3 scripts/disposable-quiesced-local-cutover-qa.py -h
python3 scripts/disposable-quiesced-local-cutover-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_quiesced_local_cutover_qa.py -v

# Only on exact loopback-only LXC114 with explicit authorization:
python3 scripts/disposable-quiesced-local-cutover-qa.py \
  --run-disposable \
  --confirm OFFLINE_QUIESCED_FINAL_WAL_CUTOVER_QA_ONLY
```

## Live deployment remains NO-GO

On LXC110, the old immutable signed v0.5.21-hw image
still runs with `restart=unless-stopped`, host source is
older, no stable G admission inode exists, and systemd
prestart recovery, Compose Start/Reload, updater, installer,
rollback, emergency and manual paths are not all
cooperating. A known old OCI ignored maintenance G EX
and created SQLite in isolated QA, so B EX or G EX alone
cannot prove old writers are stopped.

The real pre-release backup directory
`/var/backups/shelly-voltage/release-prep-current-20261115-183320`
holds two validated **preparation** snapshots (72,690
measurements each). The working database kept growing,
so those copies are **not** final CURRENT cutover backups.

No physically independent medium will be added, per
owner instruction. The one-NVMe failure and possible
loss of all local copies must be explicitly acknowledged
during **Owner Approval**, not concealed by green QA.

The remaining mandatory release evidence is real
all-owner freeze and no circular systemd/updater lock
handoff, a final consistent whole-CURRENT signed source+
SQLite WAL boundary with two locally restored copies,
secure rollback to a G-aware image, actual Telegram
long-poll and worker shutdown, isolated power-loss QA,
independent review and explicit Owner Approval.
The public pinned Telegram PHOTO switch remains a
separate release decision.

**Do not merge/deploy or restart the live bot on
the strength of this synthetic QA.**
