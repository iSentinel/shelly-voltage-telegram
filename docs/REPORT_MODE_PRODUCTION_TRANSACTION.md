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

## Append-only QA restart generations

Exact #476 (24df603cbf11b1e0b51b857313d727f0f31a950c) CI 8/8
PASS. Repeated isolated LXC114 guest reboot: init PID 1368060
to 1379307, stopped database and policy SHA256 unchanged, manual
supervisor restart accepted all 670 stopped rows and added history
to 694. All three phases fresh (.876s); third start refused.
Evidence: /var/lib/svt-report-mode-tx-qa.28KGsg.

This revision permits an explicit --seal-next-restart review of the
current accepted launch. It creates a new generation without deleting
or overwriting earlier policy, claim or acceptance. Each next policy
binds the SHA256 digest of the preceding generation's three protected
records. The latest contiguous generation is selected; gaps, orphan
records, changed earlier evidence and repeated claims fail closed.
Each acceptance binds the exact consumed claim hash, full stopped
history and current three-phase readiness. Older acceptance receipts
without claim_sha256 cannot authorize the next generation.
The prototype is bounded to eight restart generations and remains
offline QA-only. This is not protection against a privileged actor
rewriting an entire chain or bypassing the cooperating supervisor.

128 focused tests and project quality gate PASS. Actual isolated
Docker QA on the new code: initial launch plus two separately
approved restarts on the same container ID. Original generation
policy/claim/acceptance SHA256 remained unchanged. History at initial
start and the three restart claims: 694/724/757/793 rows; inode
and selected metadata preserved. First and second restart phase
ages .941s and .939s, three fresh samples each phase, three ordinary
synthetic alerts and no scheduled reports. A real SIGKILL after
generation three's durable claim but before Docker request left the
container stopped with unchanged StartedAt, the database and policy
SHA256 unchanged (793 rows), retry refused exit 3 and no acceptance.
Evidence: /var/lib/svt-report-mode-tx-qa.TKkf4l.

The revised-code reboot evidence is #476, not the new generation
revision; the SIGKILL evidence is from the new revision, not an
independent repeat on exact #476. Automatic boot enablement and
multi-generation reboot tests remain outstanding. No production
pause or deployment authorized; narrow v0.5.29-hw unchanged.

## Automatic multi-generation guest boot evidence

Exact #477 (068dcfde41bcc03f8563a203544aa319d4c04a7b), CI 8/8
PASS. On 2026-11-27, the isolated LXC114 fixture
/var/lib/svt-report-mode-tx-qa.OlfpNk ran the initial launch and
first explicitly reviewed restart, then sealed generation two and
cleanly stopped. A dedicated enabled systemd oneshot unit with
Requires/After=docker.service, Restart=no and an exact fixture
ExecStart/ExecStop was installed solely for the rehearsal.

First pct reboot changed init PID 1379307 to 1392382. Without
manual start, the enabled unit consumed generation two and started
the same Docker ID. The prior evidence SHA256s matched. Actual app
readiness and three fresh samples per phase passed (age .634s);
856 stopped rows were preserved. Three ordinary synthetic alerts
and no scheduled reports across the three launches. Clean exit 0.

After a second clean stop with no new generation seal, a second
pct reboot changed init PID 1392382 to 1393988. The enabled unit
automatically failed with exit 3: verified restart already claimed.
The same Docker container remained stopped with unchanged StartedAt.
SQLite SHA256, all recorded policy/claim/acceptance SHA256s and
database inode matched; quick_check OK. History across the fixture:
793/823/856/964 rows. No other container started. The dedicated
unit was disabled and removed and the disposable container removed.

This proves automatic boot behavior only for the unsigned, offline,
bounded QA fixture, including refusal of a consumed claim. No
production service, host reboot, production database or release
identity was changed. The enabled unit was a test fixture, not
an installation procedure for production.

## Next integration boundary

Before enabling production recovery, derive a separately reviewed
candidate with an explicitly versioned signed-pair policy schema.
Validate protected installed receipt, signed archive/image identity,
effective Compose environment and exact Docker mounts on every
startup. Preserve the existing updater and transaction gate lock
ordering and fail closed for foreign markers, uncertain claim,
legacy schema and signed pair drift. Test these boundaries against
synthetic receipts first, then an actual signed pair in isolated QA.
Do not substitute QA image IDs or the short QA readiness window for
production acceptance. Keep v0.5.29-hw narrow candidate unchanged.

## Versioned signed-pair policy review (QA only)

Exact #478 and #472 CI 8/8 PASS. The new
qa-report-signed-pair-policy.py is a read-only, offline QA verifier,
not a startup operator. Its closed schema v1 binds the exact private
receipt bytes, installed pair tuple, retained archive hash, private
configuration hash, effective Docker Config hash, container ID and
local image ID. It checks the existing pair proof fields and source
version, receipt aliases, matching OCI revision and repository digest,
explicit paused mode, stopped/no-restart/networkless container, and
exact two writable data/fence bind mounts. Duplicate JSON keys, foreign
pair, changed source, configuration or identity fail closed. The CLI
uses existing protected operator/writer/update gates and refuses an
update pending marker. Successful review explicitly returns
startup_authorized=false, deployment_authorized=false and
signatures_reverified=false. It does not create an authorization.

140 focused tests and project quality gate PASS. Positive pair-binding
tests use synthetic receipt/image metadata, not real signatures.
Actual LXC114 imported .28 image (fb88ffb1...c74ac) has the expected
revision but no RepoDigests; check_image refused it. A real disposable
container made from that image was never started; CLI refused missing
policy and invocation outside isolated QA. Evidence fixture
/var/lib/svt-report-mode-tx-qa.nuSJDJ. No digest was fabricated or
reattached. The final CLI pending-marker check was added after that
Docker trial; it was not separately exercised on the real fixture.

Next: preserve and verify OCI manifest/digest evidence through the
offline transfer, check protected signed source and installed
transaction end to end, validate additional security-relevant
HostConfig and production mounts, and bind this review to the owned
production transaction. The present strict two-mount QA policy is not
a production profile, does not prove the .env was resolved into the
inspected environment, and does not reverify signatures. Production
paused and all release/rollback guards remain unchanged.

## Preserved offline OCI byte chain

2026-11-27: online verification of the existing v0.5.28-hw signed
source/OCI pair returned PASS_SIGNED_SOURCE_OCI_PAIR_ONLY, with source
signature, OCI signature and attestations verified. Exact commit
59588e38b9a7ae59f14ac056451fe3c20f8a69d2. Skopeo retrieved the raw
index and copied the Linux amd64 child with --preserve-digests to
a directory. Index SHA256
03e83600d633301c59bbe2fa247d7982d95133ad4ca96907828688711c3de249,
manifest SHA256
c1e9ad704ce8ae377cadaaef4a48002f362279f5e74bd959d1aa840c25df4ee1,
config SHA256
fb88ffb1ee3deb33fa8c95b54d231d80ea0a7a9f0319513552f7ee2a555c74ac.
Ten compressed layers, total 80666954 bytes.

New read-only qa-offline-oci-proof.py verifies exact index/manifest/
config bytes, descriptor sizes, compressed layer digests, decompressed
DiffIDs, platform and revision, then compares ordered DiffIDs and
config ID with the local image inspect. It accepts only protected
paths and the dedicated offline QA boundary; pending markers refuse
review. The initial host-side review under /var/tmp was correctly
refused by protected-parent checks. Transferred raw bytes to
/var/lib/svt-report-mode-tx-qa.zIpIJT in LXC114. The full chain
matched the previously imported image, with RepoDigests still empty.
Tampered index and pending marker negative checks passed; no Docker
container started. 146 focused tests and quality gate PASS.

Online signature evidence:
 /var/tmp/svt-oci-offline-review.K6hZy2/pair-reverification.json.
Offline byte proof:
 /var/lib/svt-report-mode-tx-qa.zIpIJT/offline-proof.json.

The offline verifier requires an independently trusted index digest
and commit; it does not cryptographically reverify signatures itself,
does not inspect extracted filesystem bytes in Docker storage, and
is Linux amd64/gzip only. The verified byte chain is distinct from
the policy v1 RepoDigests requirement, which remains unchanged.
Next bind both proofs and protected transaction evidence, test the
exact signed-pair policy CLI, and validate effective configuration
and HostConfig. No startup or production authorization.

## Bound offline pair review, schema v2

Exact corrected #479 and #480 CI 8/8 PASS. New online exporter runs
the existing cryptographic source/OCI pair verifier and emits the
full version, commit, index digest and source archive SHA256, with
all three proof flags and no deployment authorization. On 2026-11-27
it reverified published v0.5.28-hw and produced evidence SHA256
af38f12db7357e3a7ab7b50778e4869caf7a8071d9364dada402fbade2714dd2.
Retained on connected host at
/var/tmp/svt-oci-offline-review.K6hZy2/online-evidence.json.

The separate offline schema v2 review requires the independent
operator-supplied evidence hash, exact full signed pair, retained
archive, closed QA configuration, stopped immutable local config
ID, ordered layer chain and selected restrictive HostConfig fields.
It preserves v1's registry pin requirement for v1; v2 checks an exact
local image ID and recomputes the offline chain instead. The online
evidence is a trusted operator-transferred record, not a signed
statement in its own right. The QA verifier does not reverify
signatures offline or authorize startup.

153 focused tests and quality gate PASS. Real isolated LXC114
/var/lib/svt-report-mode-tx-qa.zgLPef: actual signed source archive,
ten preserved OCI layers and imported local .28 image verified.
A synthetic installed-pair receipt was constructed for this fixture
from the verified online evidence; it is not a real installed
production receipt. The stopped container was created by exact local
image ID, no network, no restart, read-only root, all capabilities
dropped, user 10001:10001. Wrong external anchor and pending marker
were refused. RepoDigests remained empty. The disposable container
never started and was removed.

Remaining: production transaction/receipt provenance, effective
Compose plan and full security-relevant runtime configuration,
signed report-worker candidate. The fixture's exact single-line
.env and selected HostConfig are not a general production policy.
The existing signed .28 app does not implement the new report
worker pause; this is byte and policy review only, not runtime
acceptance. No production changes or release authorization.

## Protected QA journal and effective Compose provenance

2026-11-27: exact #481 and #472 CI 8/8 PASS. A read-only production
spot check found the installed .27 receipt equal to the retained
cutover journal pair, phase verified, both files mode 0600, and
retained archive SHA256 matching the receipt. This spot check does
not revalidate the complete production unit and lock contract.

The separate qa-compose-provenance.py requires the existing v2
bound pair proof and a second independently supplied SHA256 for a
QA provenance record. The record pins the policy, Compose source,
resolved Compose plan and token-scoped verified journal. The
journal must explicitly identify itself as a synthetic QA fixture,
match the exact receipt and retain the original source archive.
Resolve Compose with a fixed environment and compare the entire
closed single-service profile. Compare the inspected environment
against image defaults plus resolved overrides, the image-default
command/entrypoint/workdir/healthcheck/stop signal/volumes, and
additional selected security-relevant HostConfig settings.
This is a read-only review, not an installation or startup command.

161 focused tests and project quality gate PASS. Actual isolated
LXC114 fixture /var/lib/svt-report-mode-tx-qa.1PIzMX used real
Docker Compose create --no-build --pull never, exact imported
signed .28 image ID, original image command, verified source
and ten-layer evidence, synthetic journal and receipt. The full
resolved Compose plan and inspected config matched. Wrong external
provenance anchor, changed journal bytes and changed Compose
source bytes were refused. Container never started and was removed.
Provenance SHA256:
0033883c506dcc0c2aec76fd9f03641aaca053fe7b04a713d844b14d849aad99.

The QA journal was constructed for the test rather than emitted
by a genuine cutover. The externally supplied anchor was calculated
from this test fixture, not independently signed by an owner.
The exact QA Compose profile is intentionally restrictive, tied to
the tested Compose normalization and offline image ID, not a
production Compose policy. It cannot prove a container actually ran
the report worker or independently establish installed production
transaction provenance. The .28 image predates report pause support.
Next consolidate release scope and implement production-specific
read-only transaction/unit verification using existing signed-forward
guards, then qualify a separately signed report-worker candidate.
Do not enable production paused or loosen rollback.

## Exact same-runtime transition planning

2026-11-27: exact #483 and #472 CI 8/8 PASS. Added the read-only
qa-same-runtime-recovery-plan.py. It computes both private config
hashes for an explicit enabled/paused transition, preserving every
unrelated byte, and binds the full pair identity, previous container,
image ID and database device/inode to a canonical plan SHA256.
Only one explicit canonical assignment is accepted; duplicate keys,
interpolation, complex quoting and noncanonical mode assignments
fail closed. This conservative subset intentionally refuses absent
mode (including legacy default-enabled configuration). No private
configuration values are emitted. The CLI is restricted to a protected
offline QA fixture and does not write configuration or start Docker.

174 focused tests and quality gate PASS. Actual LXC114 fixture
/var/lib/svt-report-mode-tx-qa.JpdeTV used a synthetic multi-setting
.env, synthetic receipt derived from the verified .28 pair, real
stopped Docker image/container metadata and a disposable SQLite file.
Resolved Compose plans differed only in SCHEDULED_REPORTS_MODE.
Reverse transition reproduced every original byte. Original .env,
receipt and database SHA256 remained unchanged. Container never
started and was removed. Result PASS_REAL_QA_MODE_PLAN_NO_START.
Plan SHA256:
c206d4a6e208301107049400dd0778d60bfc08dbaadba287aa93bd6b2afea57c.

The supplied fixture identity is not independently authenticated by
the planner; it does not check signatures, lock ownership, live
configuration provenance, report support in the image, or owner
approval. A plan hash is not an authorization. The actual .28 image
predates the report-mode feature. Production has zero explicit mode
assignments and remains untouched. A future production operator must
bind its reviewed plan to fresh verified evidence, exact receipt bytes,
a signed feature-capable pair, owner authorization, full stopped
history and protected transaction gates. The default/absent mode
case requires a separate reviewed configuration policy.

## Independent public receipt blocker

Read-only LXC110 review confirmed the legacy root-owned public
symlink still targets private/shelly-voltage-update-check; private
directory mode 0750 and private receipt present. Old checker timer
active/enabled, DynamicUser checker failed with ExecMainStatus=200.
Production monitor healthy, restart count zero. No legacy checker
or path changes performed. Migration remains separately approved
maintenance, not part of same-runtime mode planning.

## Bind a reviewed mode plan to signed source capability

2026-11-27: exact #485 and #472 CI 8/8 PASS. New read-only
qa-review-mode-plan.py recomputes the canonical externally pinned
plan from protected receipt and .env bytes, stopped local container,
image ID and database device/inode. It checks the restrictive
offline host profile, explicit source mode, expected data/fence
mounts, independently pinned online evidence, retained source
archive and OCI byte chain. It requires the known static pause
branch in the exact archived app.py, Config default and report/monitor
loop methods. It does not execute the archive or establish behavior.
No runtime qualification, owner approval, startup or deployment
authorization can be granted by this verifier.

186 focused tests and project quality gate PASS. Static check
matches the current unsigned Report Worker source and refuses old
or incomplete pause branch. Actual LXC114 fixture
/var/lib/svt-report-mode-tx-qa.6TeIVC: real signed .28 archive,
ten preserved OCI layers, existing independently pinned online
evidence, synthetic receipt and stopped Docker fixture passed
identity checks but refused specifically because the signed source
does not contain the feature. Exit 3, original .env, receipt and
database SHA256 unchanged, container never started and removed.
Result PASS_SIGNED_OLD_RUNTIME_FEATURE_REFUSED.

The exact AST check is deliberately tied to the current implementation,
not a general proof of feature semantics. A positive end-to-end
review on a feature-capable signed pair remains outstanding. The
plan does not bind all process overrides or provide a production
transaction or actual operator authorization. Next review the
feature-capable candidate and owner-bound transaction contract,
then qualify signed runtime and separately approved migration.
Do not promote the QA verifier or permit production paused yet.

## Durable bidirectional QA mode operator (2026-11-27)

Exact #488 and separate #472 CI 8/8 PASS. Added
qa-durable-mode-operator.py, using existing protected QA boundary,
operator lock, quiescent writer lease, update gate, atomic file
publication and owned marker helpers. A token-scoped closed journal
records source/target configuration hashes and stopped history.
Prepare commits intent before the marker. Recover accepts only
the exact reversible old/new bytes and unchanged stopped baseline;
it publishes a missing marker only for the prepared old-config
state. Configuration remains fenced until explicit release.
Release writes the durable released phase before clearing only
the owned marker. It can finish an interrupted clear, never
launches Docker and grants neither startup nor deployment authority.
A subsequent transaction requires all previous journals released.

195 focused tests and quality gate PASS; nine new tests cover
bidirectionality, interrupted publication and clear, config/history
drift, foreign/missing markers and unfinished/released intent.
Real LXC114 fixture /var/lib/svt-report-mode-tx-qa.l9PsHm,
a disposable SQLite backup of the previous 204-row QA database,
three-setting synthetic configuration:
- SIGKILL 137 after journal publication before marker publication.
- SIGKILL 137 after target config publication before configured
  checkpoint; recovery preserved the exact marker and baseline.
- Existing writer_lease refused entry with SVT_UPDATE_PENDING.
- SIGKILL 137 after release checkpoint before marker clear;
  release recovery and repeated release succeeded.
- Reverse paused-to-enabled transaction reproduced original .env
  byte for byte, and SQLite file SHA256 remained unchanged.
Result PASS_REAL_DURABLE_BIDIRECTIONAL_MODE_OPERATOR. No Docker
container started during this test.

This is only a QA configuration operator, not the production
startup transaction. The pre-marker interval is protected by the
cooperating operator/writer/update locks during the call, but not
by a durable marker if killed there; recovery must refuse changed
history. A released record is not a START claim. The operator does
not yet bind the actual stopped container/image, authenticated
owner review, signed pair or exact start authorization and does
not cover every possible interruption within filesystem syscalls.
The next step is binding the operator to exact stopped container
identity and reviewed plan, then integrating the immutable
one-shot START gate with bidirectional transitions. No production
receipt migration, signing, or deployment authorization.

## Immutable one-shot mode START witness (2026-11-27)

Exact #489 and separate #472 CI 8/8 PASS. Added
qa-durable-mode-start.py. Under the existing offline operator,
update and quiescent writer gates, ARM binds an externally pinned
configured journal, exact target .env and stopped history, owned
marker, fixed QA image, never-started immutable container ID,
Config, HostConfig and mount hashes. START rechecks all bindings,
persists a one-shot claim before releasing the owned marker,
then issues Docker start by immutable ID under the operator/update
gates. The previous configuration-only release path now refuses
an armed transaction without matching claim evidence. Neither
ARM nor claim is authenticated owner approval.

202 focused tests and quality gate PASS. First actual isolated
fixture /var/lib/svt-report-mode-tx-qa.ONJAG4: real fixed witness
started by exact ID, repeated START refused after stop; same-name
same-image replacement with new ID refused before claim and
marker release. Database SHA256 unchanged. The replacement was
never started, removed, and the refused marker retained as evidence.
This first actual trial preceded the direct-release hardening.

Updated code, second fixture /var/lib/svt-report-mode-tx-qa.QjS5zL:
direct configuration-only release after ARM refused, marker retained.
Valid exact-ID START passed; repeated START refused after stop.
Reverse transition prepared/armed, then actual SIGKILL 137 after
durable claim and marker release but before Docker start request.
Retry refused, StartedAt remained zero, SQLite SHA256 unchanged.
Both disposable witnesses removed; no running QA containers.

The fixed witness command is a benign Python sleep, not Report
Worker runtime. This tests the launch boundary only. The image is
the unsigned exact-snapshot QA derivative. Anchors were computed
from the test fixture, not authenticated owner review. Only selected
HostConfig fields are semantically checked; full hashes detect
post-ARM changes but do not independently establish every initial
security setting or effective .env. The previous configuration-only
operator is still usable before ARM; root Docker bypass is outside
the cooperating QA contract. No atomic enforcement across all
possible launch paths, runtime acceptance or recovery from a
consumed claim is implied. Next bind real synthetic Report Worker
probe and effective Compose/runtime policy, verify fresh readiness
and history after launch, and review signed/owner-bound transaction.
No production startup or deployment authority.

## Fixed Report Worker START profile (2026-11-27)

Exact #490 and narrow #472 CI 8/8 PASS. Added
qa-durable-report-start.py, reusing the immutable one-shot ARM/claim
mechanism through an explicit trusted validator callback. The
existing witness CLI keeps its original fixed witness validator.
The new QA-only profile requires the exact unsigned QA image ID,
canonical single-line mode config, a closed JSON Compose source
and its resolved single-service plan, restricted HostConfig,
effective image-default environment plus the expected overrides,
and exactly three mounts (data/fence RW, synthetic probe RO).
It pins both probe files and Compose source/resolved hashes along
with the stopped container/config/host/mount and journal evidence.
Observation checks the claimed running ID, StartedAt, released
journal, probe/Compose/config evidence, original SQLite row prefix,
paused report metadata and fresh three-phase readiness and samples.
It is a point-in-time QA observation, not authorization.

209 focused tests and quality gate PASS (seven new unit tests).
The original exact synthetic probe bytes are retained as
tests/fixtures/report-worker/probe.py.txt (copy as probe.py in a
future isolated fixture) together with the already tracked
tests/test_app.py. Both hashes match the fixed profile. The
text extension preserves the original evidence bytes rather
than treating the historical one-off probe as maintained code.

Actual integrated Docker Compose creation and runtime START
were NOT tested in this revision: the proposed combined
integration tool call was blocked by the execution safety
system and was not retried or bypassed. No positive real
runtime acceptance is claimed. Exact Compose normalization and
actual container inspection remain unqualified for this profile.
The fixture input is synthetic, not signed release provenance;
the callback is internal trusted code, not a user-selectable
production launch mode. Observation does not yet create a
durable runtime acceptance or verify resumed delivery metadata.
Next complete the integrated runtime qualification when available,
then bind a reviewed signed feature-capable pair and owner policy.
No production migration, startup or deployment authorization.

## Durable launch and runtime acceptance receipt (2026-11-27)

Exact #491 and narrow #472 CI 8/8 PASS. The fixed Report Worker
QA profile now records a token-scoped launch receipt after the
single Docker START request returns successfully, under existing
operator/update gates. It binds the reviewed ARM hash, immutable
container identity, observed StartedAt and zero restart count.
Missing receipt or a different StartedAt/restart count blocks
observation; no implicit retry or reconstruction from a claim.

After existing live identity, Compose, probe, history and readiness
checks, the observer captures a full SQLite history snapshot and
persists the first acceptance receipt with its exact launch hash,
ARM hash, phase evidence and observed timestamp. Repeated fresh
observations retain the first receipt bytes and verify its accepted
row prefix and database identity. No acceptance authorizes startup
or deployment. Receipts are protected local files, not signed
owner approvals; direct root/Docker interference is outside the
cooperating transaction contract.

216 focused tests and quality gate PASS; seven new unit tests use
real receipt files and modeled Docker/history observations. Actual
integrated Report Worker START/acceptance remains untested. The
previous blocked integration call was not repeated or bypassed.
A crash after Docker START but before receipt publication leaves
an unaccepted launch requiring explicit review. A receipt records
a point-in-time observation, not continuous readiness or proof of
resumed delivery. Next: qualify integrated runtime when available,
bind final delivery evidence and separately approved signed pair.
Production remains on .27; no migration or activation performed.
