# Installed incident evidence contract

Status: review contract; not an approved production incident operator.
The healthy installed supervisor review is evidence of identity at
the observed time, not proof of an actual runtime fault.

## Evidence boundaries

The existing read-only installed supervisor reviewer now records exact
installed receipt and original verified cutover journal SHA256, database
and owner lock device/inode, immutable container ID, local image ID,
and StartedAt. It still checks signed input parity, installed image
pin and revision, startup owner, unit reload state, and live writer lease.
At the end it rechecks installed pair, runtime identity, original
transaction path, all four evidence bindings and lease presence.
A changed identity or lost lease invalidates the observation.

This reviewer requires a healthy running supervised writer. It cannot
classify a stopped or failed writer, prove fault attribution, acquire
an incident hold or authorize a repair. The repeated observations
are not a transactional snapshot or continuous monitoring; a root
or noncooperating process can act outside this contract. Lock presence
does not authenticate the process holding that lock.

## Required evidence for an actual incident

| Evidence | Required binding |
| --- | --- |
| Installed source/OCI | Exact signed pair, retained bytes, independent verification |
| Cutover | Original durable journal, receipt, exact phase and pair |
| Writer | Immutable container and image IDs, StartedAt, restart status, ownership |
| Fault | Observed failure and timestamp tied to that writer, not a reference image |
| Current data | Current database inode, committed WAL history, pending report/status intents |
| Hold | Quiescent writer, existing gate, exact owned marker and protected receipt |
| Repair | Exact new source/OCI, compatibility and integrated recovery evidence |
| Authority | Separate owner-reviewed repair and production operation |

A healthy baseline may aid a later incident investigation, but is not
a substitute for contemporaneous fault and stopped-writer evidence.
Missing or contradictory evidence must block activation. The historical
signed .28 reference and synthetic incident hold cannot satisfy actual
installed-incident or failure-attribution gates. Do not intentionally
induce a production fault to obtain these proofs.

The eventual incident record must distinguish a prior healthy
observation from a failed writer and a subsequent stopped-state
capture. A changed StartedAt or identity must require fresh review.
No automatic rollback, stale rescue restoration or release of an
incident marker is authorized. The narrow .29 candidate remains
independent of these changes.

## Evidence, 2026-11-27

Exact #498 and narrow #472 CI 8/8 PASS. 124 focused tests and
quality gate PASS; five new installed supervisor tests cover
identity output, journal drift, owner inode drift, lease loss,
and nonregular evidence path refusal.

A separate read-only observation of actual healthy production .27
at approximately 18:49 Europe/Kyiv confirmed:
- original verified supervised journal and installed receipt agree;
- receipt SHA256 a4903c7dd555891c1721b06ff3f8124932470d0ae63fe0bbd9483d9478f720cf;
- journal SHA256 457c2a42949d933d4753cd1d8fd2d9d485afd2f2773ebd43df52b970d193393c;
- container c276cd84a65d053363e1ac145e96864a68d6ae6a542f9ef34b8e926c5a418121,
  image sha256:424cf946fd982c556913b6528afa61aa8e4531bf74f3f4bd29e0574b37119b49;
- StartedAt 2026-11-25T17:54:44.914438439Z, restart count zero;
- DB device/inode 64536/73766, owner lock 64536/76945, lease held;
- both observations identical, HTTP readiness 200, legacy alias present.

PASS_READONLY_INSTALLED_IDENTITY_OBSERVATION_ONLY. This was a
limited separate read-only observation, not execution of the
new complete reviewer on production. No production files written,
no fault injection, no signature reverification, no activation.

Next: define a closed evidence manifest for a real incident and
review its admission boundary using synthetic inputs; do not mark
an actual incident or repair qualified without a real identified
fault and integrated evidence. Earlier blocked integration calls
were not retried or bypassed.

## Closed synthetic incident manifest (2026-11-27)

Added qa-incident-manifest.py, a structural QA reviewer with a
closed schema for baseline, fault, stopped and hold records.
Each exact serialized record is anchored by SHA256 in the pinned
manifest and links to the previous record. The chain requires
strictly increasing integer UTC epoch timestamps, identical
container/image/StartedAt and receipt/journal fingerprints,
database and writer-lock device/inode, explicit synthetic scope,
and matching stopped/held history hashes. Unknown fields,
duplicate JSON keys, nonfinite constants, invalid hashes,
missing stages and invalid counts are refused.

The CLI requires protected private files in a dedicated offline
LXC114 fixture. It reads only five fixed JSON filenames.
The references to observation, WAL capture, rescue and hold
receipt are fingerprints, not verified underlying artifacts.
Claims of quiescence, classification, readiness and identity
are synthetic fixture assertions, not authenticated observations.
Timestamp resolution is seconds; same-second stages require a
future reviewed schema change rather than an invented timestamp.
A restart or changed inode requires a new review chain.
All authorization, actual incident and repair qualification
flags are explicitly false.

137 focused tests and quality gate PASS (13 new tests).
Actual offline fixture /var/lib/svt-incident-manifest-qa.VAAy3X:
valid four-stage chain, manifest SHA256
b15e6972675b6db4e053cbb1c6cb6ac9fa36e87deb2c3f13f836d174ed813c3a.
Changed record bytes refused; newly hashed chain with changed
StartedAt refused; original five evidence files restored from
synthetic fixture copies and exact checksums matched. The
reviewer itself writes nothing. Result
PASS_REAL_OFFLINE_SYNTHETIC_MANIFEST_CLI.

Next: evaluate which underlying artifacts can be independently
verified and which require a real incident or qualified repair.
No real incident, repair candidate, production hold, signature
reverification or integrated Docker qualification is implied.
Previously blocked integration commands were not repeated.
