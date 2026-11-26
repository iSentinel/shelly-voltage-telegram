# v0.5.28-hw supervised cutover — operator review packet

Status on 2026-11-26: **NO-GO for production activation**.
This is a review packet, not an approval, signed release or executable
deployment script. The v0.5.28 release tag and artifacts remain immutable.

## Exact reviewed candidates

| Identity | Value |
| --- | --- |
| Current signed version | 0.5.27-hw |
| Current source commit | 33e30c271a4e7e311ca8c01247b6fb82ff39197c |
| Current OCI digest | sha256:2ec3e0e6ad4b483afe7ae1aa082583321305226971144cb6bba76c27c50d3c58 |
| Current archive SHA256 | d55ea710cc4d619b5ec603707cc757ea95a56d15a7cbe8d43b6821abae73f6bc |
| Target signed version | 0.5.28-hw |
| Target source commit | 59588e38b9a7ae59f14ac056451fe3c20f8a69d2 |
| Target OCI digest | sha256:03e83600d633301c59bbe2fa247d7982d95133ad4ca96907828688711c3de249 |
| Target archive SHA256 | 5776c9e06bb18d32968b6b4a083904c28b802f75fc327c9b95b5edfd6ff68ba0 |
| Separate operator patch commit | 8e1103a2e4b73f1a48fd45e1e9591a688bd19612 |
| Patched operator file SHA256 | 8c90e29a60b4a8132f8a3a69618d39d91fce4789bc64d13181afc4f6037620ef |

The separate operator checkout in PR #454 is not included in the
signed v0.5.28 archive. It requires its own exact owner review and
approval if selected for activation and recovery. Do not overwrite a
signed installed source file. All other operator scripts must come
from the reviewed checkout, with their hashes and dependencies checked.
An alternative is a new independently signed release; never retag .28.

## Evidence and current blockers

- PR #454: 117 focused tests, quality gate and both CI matrices PASS.
- LXC111: genuine signed images; early refusal of legacy symlink with
  unchanged container ID/start time, database inode and no pending update.
- Positive activation cutover-4jpsb0ku and rollback cutover-b01wjbg1 PASS.
  Rows grew 7548 -> 7575 and 7626 -> 7653, respectively. History, inode
  and synthetic PHOTO binding persisted. The new checker returned exit 0
  and auto_install=false. Rollback paused the incompatible timer.
- QA used a synthetic meter and empty Telegram credentials. It proves
  runtime mechanics, not recipient-visible live Telegram acceptance.
- On LXC110, the installed checker unit and timer hashes exactly match the
  signed v0.5.27 files. No drop-ins. The legacy timer is enabled and
  active; service failed with status 200. The root-owned symlink points
  literally to private/shelly-voltage-update-check; the private parent is
  root-owned mode 0700. Its deployed.json matches the trusted private
  receipt, but is owned by the dynamic UID and is not a trusted input.
- At review time all three live phases were offline, three outages open,
  readiness HTTP 503, SQLite quick_check OK, running container healthy,
  no restart and no pending update. The stock preflight refused activation.
  Do not conflate container liveness with physical readiness.
- Local backup restoration was rehearsed. Fresh prechange backup and
  restore validation are still required. Single-NVMe risk was accepted
  separately; no off-host protection is claimed.

Evidence: PR #452, PR #454 and Issue #453.

## Gate A — review and authorize exact operation

Obtain separate owner authorization for the exact target source hash,
commit, OCI digest, operator checkout commit and file hash, the legacy
checker migration and production activation. Publication approval alone
does not authorize activation. Bind any actual rollback separately to
the exact installed origin and signed older pair. Do not change the
Telegram media mode or pin policy as part of this operation.

## Gate B — fresh physical and identity checks

Confirm the expected single startup owner, running immutable pin, trusted
receipt and matching installed transaction. Reverify both signed pairs
using the original verification script and trusted Cosign policy.
Require HTTP 200 readiness, fresh valid voltage on all phases,
closed outages, no ambiguous Telegram intents, stable recovery per the
stock updater and a fresh read-only public PHOTO identity audit.
Require continuous three-phase coverage for the reviewed observation
window. Use the separate reviewed read-only operator helper
`production-voltage-window.py --database /var/lib/shelly-voltage/voltage.sqlite3 --jsonl`.
It requires all three phases, at least 60 readings per phase, at most
30 seconds between readings (including the window boundaries),
207–253 V throughout the last 900 seconds, no overlapping outage,
and the stock readiness and updater preflight. Exit 0 only confirms
the observation window; exit 3 blocks cutover. It never authorizes
deployment. Confirm backup space, no pending journal/fence, and no concurrent
maintenance. Recheck all gates immediately before switching the writer.

Make a fresh WAL-consistent local backup, validate checksum and
integrity, restore into a disposable location and confirm PHOTO and
history parity. The signed transaction also creates its stopped-writer
rescue. Never restore old history over a database that the candidate
may already have written.

## Gate C — explicit legacy checker migration

Follow SIGNED_ROLLBACK_PREFLIGHT.md under approved exclusive maintenance.
Record the original timer state and exact stock unit hashes. Stop and
disable only the old timer and stop its checker service. Confirm both
inactive and timer disabled. Recheck that no other operator can start
the legacy checker.

Verify all ancestors, exact symlink ownership and literal target, and
the real private directory. Preserve its contents and record the alias
target. Unlink only the verified alias, never its target. Create a
root-owned 0755 ordinary public receipt directory. Do not copy the
dynamic-UID deployed.json into that directory. Do not start the old
checker after migration. If conditions differ from the reviewed shape,
stop and investigate; do not use force, recursive deletion or permission
weakening.

If interrupted before activation, review whether to restore the exact
legacy alias and original timer state; do not restore it while a newer
transaction owns the pair. If interrupted after activation, follow Gate E.

## Gate D — signed activation

Use the separately approved and verified operator checkout and the
genuine signed target archive, bundle and checksum file. Run
signed-forward-cutover.py activate --supervised with the exact target
version, commit, digest, archive-sha256 and confirmed current photo ID;
provide the verified local archive, bundle and sha256sums paths.
Never substitute a mutable tag or an unsigned image.

Accept only the tool's verified terminal transaction, a healthy running
container with the exact target digest, active sole supervisor, retired
legacy owner, unchanged SQLite inode, preserved history and PHOTO identity,
fresh phase data and matching root-owned public receipt. Verify the new
checker service exit 0, timer active, installed version and auto_install=false.
Inspect live Telegram read-only; do not manually send or duplicate a post.

## Gate E — recovery and rollback

Inspect the recorded transaction and installed pair before acting.
Before candidate startup, recover only the previous coherent pair as
specified by the signed operator. Once the candidate may have written,
use recover --transaction with the same reviewed operator to finish
that pair; never rewind SQLite. If the legacy public path blocks
publication, perform only the reviewed migration then recover.

An authorized actual rollback must first pass check-rollback against
the genuine signed .27 artifacts, followed by rollback with the exact
.28 from-digest, target identity and confirmed photo ID. Confirm all
history, single ownership, photo binding and receipt parity. The
incompatible release checker must remain paused after rollback.

## Acceptance

Record exact source/OCI/operator hashes, transaction identifiers, backup
checks, phase coverage, live readiness, container uptime, public PHOTO
audit and release-check result in private operator evidence. Publish
only sanitized review evidence. Do not declare production acceptance
until all live gates pass. No automatic activation is authorized.
