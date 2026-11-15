# Issue #340 — noncircular writer admission G with B/W and updater A

**QA-only architecture experiment; no production lock or updater changes.**

This draft is stacked on #365 (stable lock inode release/rollback),
#364 (writer and HTTP shutdown quiescence), and #363 (opt-in
in-container B SH + W EX writer lease). These PRs remain
unmerged; no Owner Approval has been given.

## Deadlock hidden in B-only maintenance ordering

The writer lifetime lease holds **B SH** until the SQLite
writer has stopped. Maintenance requires **B EX** to
exclude all writers during a source or database transition.

A naive maintenance sequence

```text
acquire B EX -> stop running writer -> acquire updater A EX
```

deadlocks when a writer is already active: maintenance cannot
acquire B EX because the existing writer still holds B SH,
and the writer is not stopped until after B EX acquisition.
Stopping first without an admission freeze leaves a
restart gap where Docker or another actor can start
a new writer before maintenance gets B EX.

## Candidate four-inode protocol

Add a fourth distinct stable root-owned **G (admission gate)**
inode alongside B (maintenance), W (single writer), and
A (updater serialization).

**Cooperating writer startup**:

1. Acquire **G SH**, nonblocking; if denied, do not open SQLite.
2. Acquire **B SH**, nonblocking; if denied, fail before SQLite.
3. Acquire **W EX**, nonblocking; if denied, fail before SQLite.
4. Release **G SH** only **after** B SH and W EX have been acquired.
5. Retain B SH and W EX until every SQLite-capable worker and
   HTTP handler is quiescent, and the writer process exits.
   Handle aborted/exceptional shutdown with safe process-wide
   fail-stop as described in draft PR #364.

**Cooperating updater / maintenance**:

1. Acquire **G EX** first. Existing writers already released
   G SH after admission, so this EX is not blocked by their
   entire lifetime. New writer starts cannot enter.
2. Stop all possible existing Docker/systemd/manual writers
   and wait for their verified complete shutdown and
   SQLite WAL quiescence. If stop cannot be verified,
   do not proceed.
3. Acquire **B EX**, excluding writers.
4. Acquire existing **A EX** to serialize updater/recover jobs
   and execute signed source/current SQLite transitions.
5. Release A EX, then B EX, then G EX only after verified
   recovery/activation and full poststop invariants.

**Important:** G must be held as an independent stable inode
outside replaceable source trees. Writer G SH is a brief
*admission lock*, not a lifetime lock. W remains exclusive
through writer lifetime. All start paths MUST participate;
a process bypassing G/B/W invalidates the contract.
The actual existing A lock's acquire/recover order needs
separate real integration and cannot be modified on
production without Owner Approval.

## Actual isolated LXC114 result

The executable QA code starts real independent local Python
subprocesses with only root-private mode0600 synthetic
G/B/W/A files. These are Unix `flock` operations, **not
real Docker or SQLite processes**:

- Incumbent acquires G SH -> B SH -> W EX, releases G SH,
  stays alive holding B/W.
- Maintenance gets **G EX while incumbent B SH remains
  held**, so no B EX deadlock occurs.
- A newly launched writer is **denied** G SH while
  maintenance G EX is held.
- Maintenance stops/reaps the incumbent, acquires
  **B EX then A EX** and rejects writer B SH.
- After releasing all maintenance locks, a new writer
  starts normally. All four device/inode identities are
  unchanged, and all subprocesses/scratch are cleaned.

**Real isolated 2026-11-15 test PASS.** The sibling
LXC114 guest guard checks exact hostname, only loopback,
root, stopped original signed QA writer (restart=no),
disabled original QA units and expected cached image ID.
The CLI is **inert by default** and requires exact
isolated consent before launching subprocesses.

## Tests and reproducibility

- **16/16** new default-inert unit tests PASS including
  actual kernel G/B/W lock contention, marker validation,
  known-PID cleanup, inode replacement failure and
  actual synthetic noncircular handoff.
- Across the stacked QA workspace **91/91 tests PASS**
  under root and under unprivileged UID65534; Ruff
  E/F/I/UP/SIM, PEP8, all new functions <=40 lines and
  source width <=79 columns PASS.
- Zero temporary QA directory or subprocess remains.

```bash
python3 scripts/disposable-admission-gate-handoff-qa.py --help
python3 scripts/disposable-admission-gate-handoff-qa.py
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_disposable_admission_gate_handoff_qa.py -v

# Exact offline LXC114 only:
python3 scripts/disposable-admission-gate-handoff-qa.py \
  --run-disposable \
  --confirm OFFLINE_ADMISSION_G_B_A_HANDOFF_QA_ONLY
```

## Release status — NO-GO

This is a **model of a proposed owner protocol**, not a
claim that actual `writer_fence.py` already supports G,
nor that production `apply-update.sh`, recovery,
`start-compose.sh`, installer, rollback, systemd
or emergency restore perform this lock order.

Current read-only review of the source shows existing
`apply-update.sh` acquires only old updater A using
`/run/lock/shelly-voltage-updater.lock`.
The recovery mode also obtains this A lock, but no
shared admission G is wired into startup.
`start-compose.sh` invokes detached `docker compose up -d`;
`install.sh` can re-enable Docker
`restart=unless-stopped`.
`rollback.sh` can remove source files under the
target directory and replace the SQLite database.
All must be coordinated before production lock mounts
are introduced. Lock inodes MUST live outside any
updater, source, or backup replacement/deletion tree.

Actual app/Telegram shutdown, Docker daemon restarts,
multiple independent start actors, sign/verify, crash
journal replay, independent full CURRENT SQLite+WAL
A/B, physical power loss and Owner Approval still
require acceptance on separate failure domains.
Production LXC110 and QA LXC114 share one physical
Proxmox machine. Public pinned Telegram TEXT-to-PHOTO
remains independently NO-GO.

Do not deploy G/B/W/A or change production restart
policies and databases based only on this isolated QA.
