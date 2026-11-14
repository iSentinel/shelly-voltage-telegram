# Signed image / source-only recovery: disposable QA

Status: **QA PASS only**. Not a production repair command and not an
authorization to reconcile /opt/shelly-voltage during an outage.

## Why this exists

The production OCI image is signed and pinned at 0.5.21-hw, while
the files currently installed in LXC110 still report 0.5.15-hw.
The historical full rollback restores an old SQLite copy and is correctly
refused by SNAPSHOT_SIGNED_VERSION_MISMATCH. Do not remove that guard.

The production source-only recovery design must preserve all collected
measurements, the open outage, the immutable OCI image, Telegram identity
and unrelated pinned posts. It must never invoke Docker build or
automatically restore a historical SQLite backup. An approved, fenced
source + full SQLite A/B snapshot is mandatory before any live source
replacement.

## Disposable QA runner

The tool scripts/source-only-recovery-qa.py accepts only private,
marker-protected targets under /tmp or /var/tmp. It refuses the
production source directory, database inside the target, invalid/linked
paths, private entries in source archives and mismatched version markers.

For a scratch target prepared by the test harness:

    python3 scripts/source-only-recovery-qa.py \
      --archive /var/tmp/qa/signed-candidate-source.tar.gz \
      --target /var/tmp/qa/target \
      --database /var/tmp/qa/history.sqlite3

    python3 scripts/source-only-recovery-qa.py \
      --archive /var/tmp/qa/signed-candidate-source.tar.gz \
      --target /var/tmp/qa/target \
      --database /var/tmp/qa/history.sqlite3 \
      --apply-disposable

These are **illustrative QA commands**: they fail unless the target
exists, is private and contains a .source-only-qa marker with the
exact content DISPOSABLE_SOURCE_RECOVERY_QA. They never start/stop
Docker, call Telegram, modify a real SQLite, or authorize production.

## Executed validation, 2026-11-14

- test_source_only_recovery_qa.py: 23 / 23 PASS.
- Existing test_source_reconcile_recovery_preflight.py: 11 / 11 PASS.
- Actual 0.5.21-hw staged source/archive validator: PASS.
- Real-source disposable dry-run + apply: PASS, source became 0.5.21-hw.
- Test .env, secrets/secret and SQLite sample ID 30 retained.
- Archive corruption, mismatched release markers, linked/local state,
  path traversal, absent QA marker and production target refusal: PASS.
- Fault-injected interrupted stage rename restores the old QA source;
  simultaneous recovery attempts are rejected by a private QA lock.
- A private fsync'd JSON journal persists the candidate and previous
  directory names across an interruption of the source rename sequence.
- The --recover-disposable operation reopens only private paths and
  restores the original source if the destination is missing and the
  previous source is structurally verified. Ambiguous post-promotion
  states remain blocked for manual review instead of erasing evidence.
- Invalid, exposed and malicious journal files, missing original source
  and mixed source states are tested to fail closed.
- A subprocess calls os._exit(77) after the first directory rename;
  a separate recovery invocation reads the journal, restores the
  earlier source and leaves test SQLite/secret fixtures unchanged.
- Full Ruff 0.14.2 quality gate PASS; SQLite connections explicitly close.
- Production preflight during outage #30: BLOCKED with OPEN_OUTAGE,
  SHELLY_OFFLINE_OR_STALE and SHELLY_NOT_READY. No live writes.

## Explicit disposable crash-recovery only

The optional --recover-disposable action validates a private journal
and safely restores a previous *test* source when the QA swap was
interrupted. It does not read production updater receipts, stop the
writer or authorize a source change on LXC110. It does not guarantee
atomicity over actual sudden power loss or prove post-reboot parity.
Tests inject exceptions and hard subprocess termination that model
transaction interruptions, but do not inject actual power loss.
Source state remains BLOCKED when the evidence is ambiguous.

## Remaining release gates

1. Review and approve the production failure-recovery executor and its
   writer fencing, checksums, owner approval and postfailure semantics.
2. Extend the QA interruption/concurrency tests to a separately approved
   **production** recovery design, including process crash and power loss.
3. Independently verify immutable signed image identity and the newest
   live SQLite before and after permitted maintenance.
4. Only when Shelly is online, close the outage naturally and require
   stable /ready=200 before source reconciliation.
5. Require production source/image parity, reboot continuity, Telegram
   same-message and unrelated-pin verification before PHOTO cutover.

No production source or database is changed by this QA exercise.
