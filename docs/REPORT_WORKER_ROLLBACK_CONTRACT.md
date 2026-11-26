# Scheduled report worker — cross-version rollback review contract

Status: proposal and QA evidence only; no cross-version activation or
rollback approval. The existing exact monitor-input equality guard
remains mandatory. This document does not introduce an override.

## Exact known baseline

The published v0.5.28-hw pair remains immutable:
- source commit: 59588e38b9a7ae59f14ac056451fe3c20f8a69d2
- source archive SHA256: 5776c9e06bb18d32968b6b4a083904c28b802f75fc327c9b95b5edfd6ff68ba0
- OCI digest: sha256:03e83600d633301c59bbe2fa247d7982d95133ad4ca96907828688711c3de249

At the reviewed unsigned report-worker source checkpoint (parent PR #462,
f8880da9a1710aa00b06787439cad6430e5f9903), 27 of the 28 monitor build
inputs are byte-identical to the signed archive. The sole changed input,
app.py, has SHA256
603ea10f5f9ab11322c7105bf7d22a9dccab240c80b35f4b243158d4ba39c328
versus baseline
960f6d6c0a3d862a0d5743ce3c980c7ab514586f1cb72523cf0b50359bec5b8d.
The proposed origin has no signed release identity yet.
Same SQLite schema version is not proof of runtime compatibility.

## Mandatory pending-intent boundary

The prior signed release does not understand report reservation markers.
Any key matching telegram_report_pending:* (including malformed, empty
or legacy values) must block the transition. Check in the live read-only
preflight AND after the old writer is stopped and fenced, on the actual
source/rescue snapshot. Do not rely solely on a preflight taken while a
report can start. Keep PHOTO identity, history, inode and existing
electrical checks unchanged. Never clear a report marker to obtain PASS.

If a late reservation is discovered after fencing, restore the unchanged
previous source/image pair and start the previous supervisor. Leave the
transaction at previous-pair-restored and the release timer paused while
delivery remains unresolved; do not declare rollback or acceptance.
The original monitor can collect measurements. Resolve the exact report
only through a separately approved operator procedure. Resume recovery
only after all normal safety checks pass.

## Requirements for any future compatibility exception

A future proposal must fail closed unless all of the following are met:

1. Both complete source/OCI pairs are independently signed and verified,
   with exact source hash, commit, image digest, provenance and direction.
   Bind the reviewed exception to those exact identities and the exact
   operator revision; never permit a version range or mutable tag.
2. Enumerate all changed monitor/build and supervisor inputs. Review
   differences in database schema, metadata semantics, configuration,
   report retry policy, writer fencing and startup/recovery. A changed
   source or artifact invalidates prior evidence.
3. Demonstrate the actual signed images in an isolated forward and
   reverse supervised cutover, using the same WAL-mode database and
   preserved PHOTO, history and delivered-period markers. Exercise
   confirmed delivery, explicit rejection, ambiguous ACK, a reservation
   between preflight and freeze, and attempted stale acknowledgement.
   Verify no duplicate report or incorrect delivered period after restart.
4. Verify the stopped-writer rescue, full logical history and current
   inode. Test crashes before and after either writer can run. Recovery
   must never rewind a database a candidate could have written.
5. Show that an older writer will not ignore any unresolved future-only
   state. The stopped-writer gate must reject it; retain all audit keys.
   Evaluate whether a new old-behavior-but-compatible signed target is
   safer than downgrading to the already-published older release.
6. Require a separate owner review of the actual signed pair and test
   evidence. Do not convert QA-only evidence or a publication approval
   into activation authority.

Until a specific mechanism is reviewed and implemented, differing
monitor inputs still reject rollback. No automatic migration or
production reconciliation is authorized by this contract.

## Current QA evidence (2026-11-26)

- Exact signed .28 archive SHA256 verified during input comparison;
  27/28 matching inputs, changed app.py; strict rollback still rejects.
- 46 cutover, 26 updater and 14 reconciliation tests PASS.
- Simulated service lifecycle with actual SQLite: late reservation
  rejected before source/image switch, previous writer resumed,
  journal remained previous-pair-restored, history and intent retained.
- Networkless LXC114: actual copied pending and reconciled report
  databases, each with 215 measurements and synthetic PHOTO fixture.
  The candidate operator backup check rejected the pending copy and
  accepted the reconciled copy with exact logical history and inode
  parity. The original QA evidence was not changed.
- No actual signed report-worker candidate or cross-version supervised
  switch has been performed. The existing strict rollback policy
  remains unchanged.

## Same-runtime recovery profile (preferred for report-only failures)

For failures confined to scheduled reports, use the identical future
signed runtime in startup mode SCHEDULED_REPORTS_MODE=paused rather
than downgrading to an older runtime that ignores report intent keys.
This is a configuration-only recovery profile, not a new permission
to roll back across differing monitor inputs.

The paused mode skips the scheduled worker entirely, also guards
the daily, weekly and common delivery entry points, and leaves
ordinary status/alert transport, commands and sampling active.
It does not alter subscriptions, delivered-period keys or any
unresolved reservation. The default is enabled for compatibility;
an unrecognized mode refuses startup. Changing mode requires a
controlled restart; Telegram preferences cannot override the pause.

Before production use: release and independently verify the exact
runtime pair; rehearse protected supervisor restart with a root-owned
configuration and verify the resolved Compose mode, running image
digest, mounts, readiness, history and PHOTO. Keep both updater
locks and normal writer fencing, require a fresh rescue and explicit
owner approval. A paused runtime is not clearance to delete pending
markers, resume a cutover or automatically re-enable reports. Use
the separately reviewed reconciliation procedure before resuming
the affected stream. A failure elsewhere in the runtime still
requires an independently reviewed recovery path.

2026-11-26 QA: the same unsigned derived image was started first
in paused and then enabled on one network-isolated database.
Paused: ordinary synthetic alert accepted, no scheduled request,
nine stored samples. Enabled: exactly one synthetic daily report
accepted, 39 stored samples total, original prefix and inode intact.
An existing weekly pending reservation and both opt-in preferences
were preserved. Both containers exited zero. No live Telegram traffic
or production restart occurred. Full signed supervisor rehearsal
is still required.

## Protected supervisor restart rehearsal

The existing start-compose.sh --create-only uses --no-recreate. A
changed .env followed by a normal service restart does not necessarily
apply the new mode. Never infer the active mode from the configuration
file alone. The QA supervisor option --expected-report-mode checks the
actual container environment for one exact explicit enabled/paused
value, rejecting absent, duplicate or stale values before Docker start.
It is deliberately unavailable outside the existing offline QA mode.

LXC114 rehearsal used the actual Compose create-only helper and
supervised-container.py --qa-offline --once, with a root-owned
configuration and protected shared writer fence. The old stopped
container kept enabled after .env changed to paused and was correctly
refused. Only the disposable stopped QA container was removed and
recreated with the same verified local unsigned image ID. The subsequent
paused launch preserved all 20 original measurements and added 21;
inode, daily delivery marker and weekly pending were retained.
Ordinary synthetic alert succeeded, no additional report was sent,
and the container exited zero. A first positive probe did not meet
its startup assertion; the separate retry with diagnostic logs passed.

For production, a reviewed operator must hold both operator locks,
verify signed pair and protected config/mounts, fence and stop the
existing owner, create a validated rescue, and recreate only the exact
stopped container under an owned transaction. Verify the resolved
Compose mode and actual environment before starting the sole supervisor.
Recovery after interruption and production integration of the mode
guard remain outstanding. Do not simply restart an old container,
edit a signed artifact, or use --qa-offline on production.

## Configuration transaction crash rehearsal (QA only)

The separate qa-report-mode-transaction.py helper models one exact
enabled-to-paused transition in a protected networkless LXC114 fixture.
It supports prepare and recover, stores original nonsecret configuration
bytes and SHA256 identities, and durably publishes both configuration
and journal using fsync and atomic rename. Operator, quiescent-writer
and transaction locks exclude an active writer. No production paths
or live service actions are supported.

Once prepared is durable, recovery rolls forward to paused. It accepts
only the exact original or intended bytes before configuration commit.
A configured journal with changed configuration blocks rather than
silently overwriting it. An orphan rescue created before the journal
also blocks for manual review. The journal explicitly leaves
runtime_verified and deployment_authorized false.

Real LXC114 QA on 2026-11-26: active writer lock blocked prepare;
SIGKILL after mode publication but before journal commit exited 137.
Recovery completed the configured phase; a repeated replay was identical.
SQLite SHA256 and inode remained identical, all 41 measurements and
one pending reservation preserved. A separate actual Compose create-only
and offline supervisor start consumed the recovered paused configuration,
added 20 measurements, preserved prefix, inode and pending/daily markers,
sent only an ordinary synthetic alert and exited zero. A new rescue
copy was retained before this runtime trial.

Initial setup required correcting a QA-only fence directory created
under umask 077; the interface check was corrected to use kernel
interface enumeration rather than counting sysfs bonding_masters.
Network isolation remained mandatory.

This is not a production transaction: the helper has no durable
cross-process startup marker spanning the separate container lifecycle,
signed pair verification, production updater lock coordination, or
runtime acceptance journal. Those gates and crash tests remain required.
No automatic re-enable, production application or signed rollback
exception is implied.

## Durable startup fence for the QA configuration transaction

The QA transaction now publishes the existing .svt-update-pending
marker before recording prepared. Recovery retains the marker across
configuration publication. Both old and recreated containers remain
blocked by the existing supervisor gate. A separate arm operation
requires the exact marker owner, configured journal and original
rescue, a stopped networkless container with the pinned QA image ID,
one explicit paused mode and the expected writable data/fence mounts.
Only then may it clear its own marker.

A crash before journal creation leaves an orphan marker and blocks
automatic recovery. Foreign markers are never replaced or cleared.
The arm result does not certify runtime readiness. It is not a
production transaction or signed image approval. Container recreation
and subsequent supervisor launch remain separate QA operations; a
durable runtime acceptance record and recovery after interruption of
those operations still need implementation. Privileged concurrent
Docker/config changes outside the cooperating operator locks are not
covered by this rehearsal.

2026-11-27 LXC114: actual supervisor returned SKIP_PENDING for the
old enabled container in prepared and configured phases, and for the
recreated paused container before arm. Attempted arm of the stale
enabled container was refused. After validating the paused container,
the exact marker was cleared and offline supervisor start succeeded.
History grew from 61 to 82 rows, original prefix, inode, daily and
weekly metadata were preserved. Only an ordinary synthetic alert was
accepted and the container exited zero. 46 focused tests passed.

## Durable armed container identity (QA only)

ARM now writes armed-container.json with the exact immutable Docker
container ID, pinned local QA image ID and paused mode before clearing
its owned startup marker. Replay requires the same stopped validated
container even if the marker was already removed. A replacement under
the same name is rejected. The optional offline supervisor expectation
requires that exact ID and paused mode, and starts Docker by immutable
ID rather than by mutable name.

Real networkless LXC114 QA on 2026-11-27 injected SIGKILL immediately
before and after marker removal. The first interruption retained the
fence and supervisor returned SKIP_PENDING. The second retained the
armed identity with the marker cleared. Exact replay succeeded; wrong
ID startup and subsequent same-name replacement replay were refused.
The accepted paused container preserved 82 original measurement rows,
extended history to 103, preserved inode and daily/weekly markers,
sent only a synthetic ordinary alert and exited zero.

The expectation is explicit and QA-only; it is not automatically
enforced by every supervisor invocation. Runtime acceptance is not
yet durably recorded, and an already-running writer is not adopted.
A full production transaction must integrate signed receipt verification,
automatic binding at every startup, operator lock ownership, crash
recovery around START and independent runtime evidence. The strict
signed rollback policy remains unchanged.
