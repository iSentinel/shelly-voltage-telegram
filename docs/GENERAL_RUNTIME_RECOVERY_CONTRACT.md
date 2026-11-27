# General runtime failure recovery contract

Status: review proposal and synthetic QA evidence, not an approved
production recovery procedure. Applies to the future Report Worker
release track, not the immutable narrow .29 review.

## Recovery boundary and decisions

| Observed boundary | Recovery route to review |
| --- | --- |
| Preboot, candidate proved never able to write | Existing exact previous-pair recovery, without DB replacement |
| Preboot, unresolved delivery | Retain intent, review previous-pair monitoring only, no acceptance |
| Candidate may have run, report-only failure | Same signed runtime, protected paused-mode transition |
| Candidate may have run, general or unknown fault | Fenced hold and separately qualified forward repair |
| Corrupt DB, missing evidence, foreign owner | Incident hold, retain evidence, no automatic restore/start |

Do not infer preboot from an absent container, zero StartedAt on a
replacement, or a failed CLI response. Verify the durable cutover
journal, exact installed and previous pair, ownership, locks, and
whether any candidate writer could have run. Uncertainty must be
treated as candidate-may-have-run. A STOP/START operation must use
the existing separately reviewed maintenance and owner gates.

After the candidate could have written, the retained pre-cutover
database is evidence, not a database to restore over newer history.
Retain the current database, WAL and related report/status intent
state, plus a verified fresh rescue, before deciding on recovery.
An unchanged row count does not prove unchanged data or metadata.
If the database cannot be opened safely, preserve its bytes and
sidecars for a separate incident review. Do not delete markers,
force checkpoint, replace the live inode or silently rewind history.

## General fault recovery proposal

Prefer a separately approved forward repair retaining the report
reservation semantics and database compatibility of the installed
runtime. PAUSED is only useful for a confirmed report-only fault.
Require an explicit fault classification and evidence, exact signed
repair source/OCI pair, reviewed schema and metadata compatibility,
and a tested stopped-writer transaction preserving the current DB.
For any changed runtime, qualify the repair with synthetic hardware,
ordinary status and report delivery, unresolved ACKs, crash/restart
and rollback/forward recovery boundaries. Prove current history and
PHOTO preservation, sole writer, exact configuration and readiness.
No compatibility exception to strict existing rollback is introduced.

If no qualified repair exists, keep a separately authorized incident
hold rather than guessing a safe downgrade or retrying indefinitely.
The hold may interrupt monitoring; it is not an instruction to stop
a healthy production service. No new production operator is provided.

## Read-only synthetic reviewer

qa-general-recovery-review.py compares two distinct standalone
SQLite copies using full logical dump hashes, user_version,
application_id, measurement count and unresolved intent count.
It rejects nonregular or linked paths, sidecars, failed quick_check
and changed file identity. The CLI restricts inputs to a protected
offline LXC114 fixture; it uses immutable read-only SQLite access
only for those standalone files. It never starts Docker, creates a
backup, restores data, or changes a production journal.

The phase, fault classification and parity flags are supplied by
the caller, not independently authenticated. The decision is a
review route only. Every authorization flag is false, including
when all compared data match. Logical dump equality is not a
cryptographic receipt or proof that all possible live state is
captured. Production WAL acquisition and a durable hold/repair
operator are still unimplemented.

## Evidence, 2026-11-27

Exact #495 and narrow #472 CI 8/8 PASS. 180 focused tests and
quality gate PASS, including nine new tests with actual synthetic
SQLite files, modeled host guard, stale history, metadata-only
drift, sidecar, corrupt file and link rejection.

Real offline LXC114 fixture /var/lib/svt-general-recovery-qa.2vdOQ0:
- preboot: three rows in both copies, logical parity, no pending;
- candidate-may-have-run: four live rows, three rescue rows,
  one unresolved synthetic pending intent, forward-repair/hold
  review, stale rescue detected;
- report-only: same evidence, paused review route, no authorization;
- both file SHA256 unchanged by the postboot reviews; no Docker start.
Result PASS_REAL_OFFLINE_RECOVERY_REVIEW_ONLY.

Next: review evidence and requirements for a forward repair
candidate and incident-hold ownership, independently bind real
signed pair and journal evidence, qualify integrated QA when
available. Previously blocked integrated execution not repeated.
Production remains .27; no migration, signing or deployment.

## Durable isolated incident hold and repair admission (2026-11-27)

Exact #496 and narrow #472 CI 8/8 PASS. New qa-incident-hold.py
uses the existing offline QA host boundary, operator lock,
quiescent writer lease and update gate. It requires a standalone
synthetic database, an exact caller-pinned scope review hash and
explicit general-runtime/unknown classification. It publishes a
distinct owned incident marker before committing a protected
journal binding full logical SQLite history, inode, configuration
hash and review hash. Inspection requires an externally supplied
exact receipt hash, unchanged evidence and the original marker.
The tool has no release, restore, service stop or START action.

If publication is interrupted before the journal is committed,
the orphan marker remains and preparation/inspection fail closed;
manual review is required. This is not an authenticated incident
classification, signed pair, production hold or guarantee against
privileged noncooperating Docker access. The fixture is already
stopped; live quiescence and production WAL acquisition are not
provided by this tool.

187 focused tests and quality gate PASS, seven new tests covering
actual marker and journal files, replay, foreign or missing
marker, changed history, wrong receipt anchor and simulated
journal publication failure.

Actual offline LXC114 fixture /var/lib/svt-report-mode-tx-qa.YIGHUe:
four synthetic rows and one unresolved intent. Bound the retained
exact #493 scope review hash
dbcc40f10d2a7b2d2b394ce0bc8cd76c59e708d9e8b2f334c582998e71aff312.
Hold receipt SHA256
18d40b0acd61fcc74adb7f57c39988732dc7f06a0bafe8892a9ec9804d1ab99c.
Prepare/inspect results identical; duplicate prepare and wrong
receipt anchor refused. Real writer_lease refused with
SVT_UPDATE_PENDING; competing mode prepare refused. SQLite and
.env SHA256 unchanged. No Docker launch; exact hold marker retained.
PASS_REAL_QA_INCIDENT_HOLD_REVIEW. Orphan marker recovery was
unit-tested with simulated failure, not real SIGKILL in this run.

### Forward repair admission checklist

A future repair must have its own exact source commit, reproducible
release archive hash, independently verified source and OCI pair,
and a version strictly newer than the failed installed release.
The original failed pair and journal, current incident evidence,
repair diff and exact operator revision must be bound together.
Do not treat a newly signed but behaviorally unreviewed image
or an older compatible-looking runtime as a qualified repair.

Before any activation, require:
- explicit root-cause or failure-scope review with evidence;
- retained report reservation/reconciliation semantics, PHOTO
  identity, ordinary monitor behavior and SQLite schema compatibility;
- fresh current-DB rescue including WAL-committed history, validated
  under the stopped-writer boundary, no replacement by stale rescue;
- a real isolated forward repair rehearsal using the failed runtime
  and exact proposed repair, one database, full current history
  and report/status metadata, including unresolved intent refusal
  or a separately reviewed reconciliation outcome;
- crash tests before and after each ownership handoff, durable
  one-shot startup and readiness/history/delivery acceptance;
- an independently reviewed way to finish or retain the incident
  hold without unauthorized marker removal or automatic retry;
- separate owner approval for the exact repair and production
  operation, with current electrical and hardware readiness.

No actual forward repair candidate exists or is qualified yet.
The checklist is proposed admission policy, not a recovery
implementation or an exception to strict rollback. Next review
current signed pair and cutover-journal binding for the incident
evidence, and prepare qualification evidence only for a real
identified repair. Previously blocked integrated Docker trial
was not repeated. Production and narrow .29 unchanged.

## Historical signed-pair reference binding (2026-11-27)

Added qa-incident-signed-reference.py, an offline QA-only reviewer.
It requires independently supplied exact hold, scope, receipt,
cutover journal and historical online evidence SHA256 anchors.
It rechecks the hold before and after reference validation,
checks receipt aliases, verifies the closed synthetic journal
and retained source archive, recomputes the offline OCI chain
and compares the local image identity, revision and ordered layers.
It never starts, stops, retags, or releases a writer.

119 focused tests and quality gate PASS, including six new unit
tests covering receipt anchors, alias drift, the reference
verification calls, closed journal fields, hold drift and
non-authorizing result flags. The proposed integrated LXC114
reference-validation command was blocked by the execution safety
system and was not retried or bypassed. No real integrated
reference review is claimed for this revision.

The retained .28 signed pair is a historical QA reference, not
the failed or installed Report Worker pair. The cutover journal
is synthetic. Offline verification does not reverify signatures.
The held source scope describes the unsigned #493 snapshot and
must not be conflated with .28. There is no claim of installed
pair verification, failure attribution, owner approval or
production recovery qualification. All authorization flags false.
An actual incident needs a separately authenticated installed
pair, original cutover journal, writer identity, and failure
evidence before forward repair can be admitted.

Next: define the exact installed-incident evidence contract and
review missing admission proofs; qualify integrated checks only
when available. No production changes or release authorization.
