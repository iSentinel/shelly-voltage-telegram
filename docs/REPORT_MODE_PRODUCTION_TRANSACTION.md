# Production report-mode transaction contract

Status: design plus fail-closed boundary tests. No production pause
operation or authorization is implemented.

## Current boundary

Until the full transaction is implemented and qualified, the normal
supervisor accepts an absent mode (runtime default enabled) or exactly
one explicit SCHEDULED_REPORTS_MODE=enabled. It rejects paused, unknown
and duplicate entries. The signed cutover operator rejects a resolved
Compose plan with any non-enabled explicit mode before stopping the
old writer. QA-offline behavior stays restricted to its own fixtures.
A normal release receipt is not approval of a paused report profile.

## Proposed durable ownership

Use a separate root-only transaction under the existing protected
updater state. Never edit the signed source archive or image. Bind the
mode transition to the complete installed pair receipt (commit, source
SHA256, OCI digest and pair transaction), original private configuration
hash, exact target configuration hash, previous timer state, database
device/inode and full stopped-writer rescue. Store any original secret
configuration privately and never print it or include it in the public
receipt. Validate the same signed source and actual image independently
at recovery. Reject stale or foreign state; no downgrade exception.

Acquire the existing signed-forward operator lock then updater lock.
Publish an owned startup marker under the shared update gate before
stopping/recreating anything; wait for exclusive writer ownership
before the rescue and configuration update. Follow the actual installed
supervisor and timer contracts. No concurrent updater, direct Compose
up, or direct privileged Docker operation is part of the protocol.

## State transitions

- PREPARED: immutable reviewed intent, exact pair and original config.
- FENCED: owned marker, updater coordination and stopped writer.
- CONFIGURED: durable desired config, verified original rescue.
- ARMED: exact stopped replacement ID, pinned image, mode and mounts
  checked; durable identity before removing the owned marker.
- START_CLAIMED: one durable attempt before Docker start. Any
  uncertainty requires observation, not automatic replay of START.
- VERIFIED: exact launched ID/StartedAt, all three fresh phases,
  readiness, PHOTO identity and unchanged historical prefix and report
  metadata. Persist the approved active mode and identity for every
  subsequent boot, independent of the supervisor process lifetime.

A systemd process restart before VERIFIED must not adopt a foreign
running writer or repeat a consumed START. A verified paused profile
may only permit normal restart of its exact approved container after
revalidating the signed pair, protected policy, mounts and mode.
Replacing the container, re-enabling reports or changing signed pair
requires a separate owned transition. A failed readiness check cannot
authorize restoration of an older database or re-enable reports.

The production electrical window remains distinct from the short
synthetic QA window. Recheck the full 15-minute physical gate and
recipient-visible PHOTO evidence in the approved maintenance workflow.
Do not use the report reconciliation QA helper in production. Preserve
unresolved delivery metadata; approval for a pause does not approve
resolving or deleting it.

## Evidence and remaining work

2026-11-27: 118 focused tests and project quality gate passed. Real
networkless LXC114 Docker inspect fixtures accepted absent/enabled and
rejected paused/invalid; none were started. Actual Docker Compose
resolution of a synthetic paused .env was refused by validate_compose.
The initial Compose invocation on the Proxmox host failed because it
has no Docker CLI; the isolated LXC trial then passed.

The proposed production journal, lock lifetime, automatic persistent
boot binding and full interruption/reboot tests are not implemented.
Signed report-worker source/image qualification and explicit owner
review remain mandatory. This change is excluded from the narrow
v0.5.29-hw source candidate.
