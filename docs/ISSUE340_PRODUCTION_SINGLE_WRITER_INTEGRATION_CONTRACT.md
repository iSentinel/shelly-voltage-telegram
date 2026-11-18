# Issue 340: production single-writer integration contract

Status: SOURCE-ONLY DESIGN, NOT INSTALLED, NOT RELEASE APPROVAL.
Evidence: disposable offline LXC114 Docker and guest-reboot A/B QA,
plus Issue 422 signed rollback full-history/WAL fixture acceptance.
Target is Shelly Pro3EM-3CT63 L1/L2/L3, not retired EM-50.

## Existing production behavior confirmed on 2026-11-19

- compose.yaml still configures Docker restart: unless-stopped.
  Docker restarts this container independently of the systemd
  shelly-voltage-compose.service unit.
- The installed systemd unit calls apply-update.sh --recover as
  ExecStartPre. Its contents differ from the tracked source unit.
- The updater has its own /run/lock/shelly-voltage-updater.lock, but
  the live Monitor and Storage code do NOT acquire that lock.
- app.py Monitor.run calls initialize before starting its polling
  and Telegram command threads. Storage.initialize can change schema.
- app.py Storage.connect opens SQLite and sets journal_mode=WAL.
  Storage.add opens/commits one INSERT per phase. Other writes include
  alert state, Telegram PHOTO/post intent, settings, offsets and
  maintenance. Gating only poll_cycle is therefore insufficient.
- apply-update.sh snapshot runs an online SQLite backup before
  mark_pending. recover_pending calls restore_snapshot_database,
  which can delete main -wal/-shm files and replace live SQLite
  from an older archive after stopping Compose. It can discard
  all newer three-phase records without a verified newest rescue.
- A known 21:01 backup compared to live 22:07 history would lose
  exactly 380 records per phase. Never restore stale history.
- Telegram saved meter type pro3em is persisted in SQLite meta;
  saved preference overrides the environment during Monitor startup.

## Proposed single shared writer lifecycle

1. Docker cannot independently own restart. Future reviewed Compose
   must use restart: no. The matching installed and tracked systemd
   service is the sole supervisor; a guarded watchdog may delegate
   restart only to that service. Require explicit health/failure
   supervision so blocking does not silently abandon availability.
2. The real app must acquire a durable nonblocking lifetime owner
   lease BEFORE Storage.initialize, schema migration, health
   listeners, Telegram threads or any other writable SQLite access.
   Another owner must fail closed before opening SQLite. This is
   held for the entire process lifetime, not one polling cycle.
3. All write-capable SQLite operations, including Storage.connect,
   metadata, alert state, Telegram PHOTO intent, maintenance and
   migration, must share a short transaction gate with the updater.
   Under that same gate, check for pending-update before opening
   a write transaction, then commit or roll back atomically. Read
   paths and special CLI commands require a separate privilege
   review. Do not accidentally make read-only health probes write.
4. Updater obtains its exclusive updater lease and publishes a
   fail-closed pending-update state under the shared transaction
   gate. Publishing must be atomic, durable, and protected against
   symlinks and hardlinks; it cannot wait forever on a stuck writer.
   Existing writers must observe the marker and stop accepting
   new work; publication and inserts cannot cross each other.
5. The supervisor stops the real Docker writer, joins its
   Telegram/background threads, and waits until its lifetime
   owner lease is released. A service stop or successful Docker
   command alone is NOT proof all SQLite handles have closed.
   If the owner lease remains busy, abort without replacing DB.
6. With both writer and restart authority fenced, take a newest
   consistent SQLite backup through sqlite3 backup API, including
   committed WAL records. Independently restore to an isolated
   file, quick_check it, compare full logical contents, schema,
   phase counts and maxima, persisted pro3em model, PHOTO identity
   and pending Telegram intents. Retain this newest rescue even
   if update or rollback fails.
7. Verify exact old/new VERSION, RELEASE_VERSION, OCI digest,
   Cosign identity, source archive SHA256 and signed deployment
   receipt before any source/Compose/image activation. Stage
   changes durably and refuse any version/source mismatch.
8. Never overwrite newer history with an older source/upgrade
   snapshot. Recovery after any SIGKILL must reconcile the
   newest rescue while the writer remains fenced. Reject leftover
   active -wal/-shm, linked path, busy lease, invalid schema,
   partial phase history and an uncleared Telegram send intent.
9. A successful activation must obtain one supervised writer
   lease, verify new source/image/receipt and stable real
   L1/L2/L3 /ready over multiple polls, keep PHOTO identity
   and prevent duplicate Telegram sends. Only then atomically
   finalize the transaction and clear pending under the same
   synchronization discipline. Otherwise leave pending and
   require explicit owner-approved recovery.

## Files and components requiring a coordinated code change

| Component | Required change and acceptance |
| --- | --- |
| app.py Monitor entry | Acquire lifetime lease before Storage.initialize and all threads |
| app.py Storage.connect | Common transaction/pending fence for ALL writes and migrations |
| app.py Storage.add | Decide and test atomic L1/L2/L3 batch semantics; now one commit per phase |
| Telegram status and callbacks | Preserve PHOTO marker, post intents and update offset across crashes |
| compose.yaml | Disable independent Docker restart, keep pinned signed image and nonroot UID |
| shelly-voltage-compose.service | Match tracked/effective unit; read-only boot classifier, not unchecked recovery |
| scripts/start-compose.sh | Refuse pending and unauthorized Compose override; single owner only |
| scripts/apply-update.sh | Publish pending under shared gate; stop/fence writer; newest WAL rescue |
| scripts/rollback.sh | Pair exact signed OCI/source/receipt and recover newest entire history |
| scripts/backup.sh | Validate SQLite backup/WAL consistency and per-phase history parity |

## Security and compatibility prerequisites

- The real process currently runs as a nonroot container UID.
  Host root-owned updater state is not automatically readable.
  Design a minimal, owner-controlled pending visibility mount,
  separate from secrets and signed update receipts; ensure the
  marker directory is searchable and status is visible to the
  designated process without exposing credentials.
- Both host updater and container writer must refer to the same
  underlying lock inode via stable, explicitly mounted paths.
  Use O_NOFOLLOW and validate directory ownership/link count;
  do not lock unrelated files named the same in distinct mounts.
- The updater marker and the SQLite transaction lock must have
  identical lock ordering across all writers. Resolve waits with
  finite timeouts, stable reason codes and audit receipts.
- Restore must not unlink WAL or replace the active DB while a
  stale Docker process may still own an open SQLite connection.
- Keep historical EM-50 L1 data and new Pro3EM L1/L2/L3 intact.
  Never auto-downgrade the saved pro3em model preference.
- A low but real voltage after power restoration is not an
  application failure and does not authorize threshold changes.

## Required isolated acceptance matrix before Owner Approval

- Actual Docker daemon restart and real disposable guest reboot,
  with pending absent and present, under one supervisor only.
- Two concurrent Docker writer processes: only one owner lease.
  A restart loop must not acquire a second lease or write.
- Pending published between initial startup check and writer
  lock; no inserted rows after marker durability.
- SIGKILL during any phase of SQLite WAL commit, pending creation,
  writer stop, newest backup, OCI/source activation, file rename,
  and recovery retry. Require no newest-history loss.
- Complete three-phase groups and all prior L1 history retained,
  plus same PHOTO identity, zero duplicate status sends and
  stable Telegram intent reconciliation.
- Version-paired old/new source+OCI+receipt with valid Cosign and
  integrity checks, and rejected mixed versions/linked paths.
- Read-only production boot-owner audit returns PASS only after
  actual installed unit and Docker policy match the accepted
  candidate. Verify positive availability after an approved
  restart as well as negative fail-closed behavior.
- Exact source tree, Python 3.11/3.12/3.13 CI, independent
  reviewer/Owner Approval and explicit release permission.

## Proven so far, and explicit remaining blocker

Disposable LXC114: real Docker/systemd independent restart bypass
reproduced, guarded watchdog both branches PASS, guest reboot A/B
PASS; new separate synthetic two-lock Docker writer tests PASS,
including pending before/during run and independent Docker restart.
Source-only Issue 422: full logical SQLite/WAL rollback and actual
cross-process SIGKILL/retry fixtures PASS.

NONE of those checks changes the signed runtime process or updater
into a cooperating actor. The real boot-owner and full-history
transaction remain BLOCKED until implemented together and tested
in an isolated actual Docker/systemd environment. Never merge or
deploy the separate QA helpers as a production workaround.
