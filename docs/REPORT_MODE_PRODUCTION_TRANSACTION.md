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

## Isolated persistent verified restart rehearsal

The QA supervisor now recognizes a protected verified-restart policy
in the dedicated offline recovery layout. An explicit --seal-restart
review requires a current positive three-phase acceptance of the first
claimed launch. The policy binds the exact container/image, paused mode,
accepted StartedAt, hashes of eight protected transaction/configuration
inputs, and a fresh database history/metadata snapshot. A clean exit
with no OOM, unchanged policy and preserved historical prefix is
required before consuming one durable restart claim. The subsequent
review requires the new StartedAt, unchanged history and real fresh
three-phase readiness. A third launch is forbidden; no claim is reset
automatically. This is a deliberately one-shot QA prototype, not an
unlimited restart policy or production authorization.

2026-11-27: 122 focused tests and project quality gate PASS. Actual
networkless LXC114 systemd restart on a disposable unsigned image
passed first and second launches with the same ID and new StartedAt.
The first transient unit was unloaded after stop, so its third-start
failure did not demonstrate the guard. Repeated using an explicit
temporary /run/systemd/system unit: third start exited 3 with
"verified restart already claimed", unchanged StartedAt, stopped
container. History 331/358/394 rows, inode unchanged, SQLite OK;
three fresh measurements per phase, age 0.932s after restart.
Two ordinary synthetic notifications, no scheduled reports, exit 0
on clean stop. QA unit and disposable container removed; evidence
retained in /var/lib/svt-report-mode-tx-qa.TyvmMZ.

Full guest reboot, crash between verified restart claim and Docker
request, general multi-generation restart acceptance and signed
production integration remain outstanding. Production boundary still
rejects paused, and the narrow v0.5.29-hw candidate is unchanged.

## Guest reboot and stopped-writer restart baseline

2026-11-27 exact #475 (8a16835235397f72618fb4577072001e33c9849f),
CI 8/8 PASS. Real isolated LXC114 reboot changed host-visible init
PID 651694 to 1368060. The cleanly stopped QA database and sealed
policy SHA256 matched before and after guest reboot; Docker did not
autostart either test or historical containers. A manual systemd
start consumed the verified restart claim and passed three-phase
acceptance (three samples per phase, age .922s). A third start
failed with exit 3 and unchanged StartedAt. History 394/421/451,
inode and metadata retained. Evidence:
 /var/lib/svt-report-mode-tx-qa.irZ90l.
Separate SIGKILL after durable restart claim but before Docker
request preserved the original StartedAt and stopped container,
refused the retry, and retained both database and policy SHA256
(481 rows). Evidence: /var/lib/svt-report-mode-tx-qa.9JrQ45.

The next revision closes the history window between policy seal
and clean stop. Capture the complete stopped baseline under the
existing operator, update and quiescent writer gates in the restart
claim. Review must preserve both sealed and stopped history and
require fresh phase samples beyond the stopped last ID. Claims
without the new baseline fail closed. This does not prove the
integrity of rows before the stopped snapshot was captured.
124 focused tests and project quality gate PASS. New exact code
on networkless LXC114: 505 rows at seal, 553 at stop and 586 after
restart; preserved inode and metadata, fresh samples age .824s,
two ordinary synthetic notifications only, exit 0. Evidence:
 /var/lib/svt-report-mode-tx-qa.r6L35v.

The reboot and SIGKILL evidence above is from #475, not a repeat
of those scenarios on the revised baseline code. The guest unit
was manually started after reboot, not enabled for boot; no claim
of automatic boot coverage. Production pause remains blocked.
Multi-generation policy and signed qualification remain outstanding.
