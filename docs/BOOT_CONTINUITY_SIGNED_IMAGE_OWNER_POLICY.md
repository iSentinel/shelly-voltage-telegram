# Owner-reviewed signed-image boot continuity policy

Status: **DESIGN ONLY — PRODUCTION NO-GO**

Related: Issue #340 (boot availability), Issue #335 / PR #336
(SQLite history), PR #341 (read-only boot decision) and PR #342
(disposable transient systemd replay).

## 1. Problem and constraints

The installed Compose systemd service runs apply-update.sh --recover
as ExecStartPre. If a pending-update snapshot contains older SQLite
history than the current database, safe recovery must reject it.
But a nonzero ExecStartPre prevents the following ExecStart and may
keep monitoring offline after a real boot.

The normal startup script also has two paths: a private immutable
image override uses Docker Compose up with --no-build, while a missing
override can fall back to ordinary Compose up against a manifest
containing build: . An emergency boot policy must **not** silently
rebuild, pull or select an unverified image.

Finally, Docker restart policy, enabled Compose service, boot hook,
updater timer and other restart owners can race one another. A writer
stopped for snapshotting is not fenced if another owner restarts it.

## 2. Non-negotiable data and trust invariants

- Preserve the **entire current SQLite logical history**, schema,
  committed WAL, outage edges, metadata and sample timestamps.
  A historical archive with a valid checksum may still be stale.
- Never silently clear a pending-update marker, discard diagnostics,
  delete a WAL sidecar, fabricate deployment parity or replace current
  SQLite merely because a previous archive is available.
- Require an exact immutable OCI image digest matching private
  deployed receipt, image override and intended runtime. Independently
  validate the release-specific Cosign signature, trusted workflow
  certificate identity and OIDC issuer. Digest equality alone does
  not prove signature trust.
- Require an explicit, tested DB schema/migration compatibility
  contract for the exact writer image. A valid signature does not
  prove the application can safely use a newer SQLite schema.
- Permit **at most one writer**. Coordinate Docker, systemd Compose,
  updater timer, pre-start recovery and all other restart owners
  before any snapshot, restore or maintenance.
- Never treat successful QA, a verified digest or an audit outcome
  as owner approval for source installation, production recovery,
  Telegram PHOTO status or pinned-message migration.

## 3. Proposed decision states (not implemented)

| State | Observation | Decision |
| --- | --- | --- |
| CLEAN_OBSERVED | One healthy running writer matches the private pin; no pending marker | Report state; no new-start authority |
| RECOVERY_PENDING_STALE | Current logical SQLite differs from old snapshot | Preserve current DB/WAL and pending evidence, block historical rollback |
| RECOVERY_PENDING_UNKNOWN | Invalid checksum, missing/corrupt snapshot or pending marker | Block changes; retain all available evidence |
| PIN_UNTRUSTED | No override, mutable image, wrong mode, linked file or digest mismatch | Block boot continuation and local build fallback |
| SIGNATURE_UNVERIFIED | Digest present, trusted Cosign evidence absent | Block boot continuation unless an independently approved offline trust policy applies |
| SCHEMA_INCOMPATIBLE | SQLite schema cannot be proven compatible with candidate image | Block writer start |
| RESTART_OWNER_ACTIVE | A different owner can relaunch writer during maintenance | Block fencing-dependent operations |
| CONTINUATION_CANDIDATE | Signed pin, schema contract, one-writer ownership and data checks all pass | Candidate for independent review only, NOT automatic startup |
| STAGING_CONFIRMED | All replica boot and power-loss tests passed | Still requires explicit owner approval for production |

No state in this document invokes Docker, systemctl or Telegram.
CONTINUATION_CANDIDATE is never an authorization from PR #341.

## 4. Boot continuation design sequence for independent review

1. Inspect private deployed state and source markers without writes.
   Reject symlinked files, unsafe permissions and invalid receipts.
2. Independently validate exact immutable image signature. For an
   electricity outage with Internet unavailable, decide whether a
   protected, previously signed and owner-approved **offline trust
   receipt** can be used, including expiry, identity and revocation
   rules; otherwise fail closed.
3. Read current SQLite integrity, full logical WAL history and DB
   migration metadata. Compare against a separately reviewed schema
   compatibility range for the exact signed image.
4. Audit all writer restart owners, including Docker auto-restart
   before Compose and updater timers that may start later.
5. If recovery archive is stale, never roll back SQLite or erase
   pending diagnostics. Separate the data-preservation decision from
   the availability decision.
6. Only a **separately approved** continuation may select a known
   compatible, locally present, immutable signed image. It must not
   permit image build, remote pull, implicit recreate, new source
   installation or background updater races without independent
   review. If one writer already runs, do not create another.
7. Verify ready state, historical sample high-water mark, outage
   continuity and absence of artificial 0 V samples. Preserve all
   failure evidence for the operator.
8. Maintain the current Telegram channel/message ID and status
   medium. An unrelated TEXT-to-PHOTO promotion is prohibited in
   boot recovery.

Do **not** implement this by forcing a failing pre-start to exit
successfully, clearing pending automatically, or disabling watchdog
checks. Exact systemd ordering and restart-policy changes still
need an approved design.

## 5. Fresh source + whole-history SQLite A/B

Before any authorized production maintenance, fence *every* real
writer/restart owner. Then create two independently captured A/B
backup sets containing:

- consistent current SQLite snapshot with all committed WAL history,
  outage rows, schema, application metadata and timestamps;
- exact verified source release archive, immutable OCI digest,
  signed release identity and commit provenance;
- private Compose configuration, deployed/pending override state
  and systemd restart-owner settings;
- private evidence of the existing Telegram channel/pinned message
  ID and content type, excluding credentials from all reports.

Restore both sets **independently into disposable environments**.
Check SQLite quick_check, complete logical row/value hashes, schema
fingerprint, outage records and sample high-water mark. Historical
backup age alone is not enough. A/B must be current at the final
writer-fenced consistency boundary.

## 6. What has already passed

- PR #336 synthetic recovery acceptance: reject stale history before
  Docker stop, recheck after stop, preserve newer WAL and pending
  diagnostics. It remains unmerged and not installed.
- PR #341 read-only decision: 30/30 focused unit tests, project Ruff
  and GitHub CI Python 3.11/3.12/3.13 PASS. This script never
  authorizes boot or verifies a signature itself.
- PR #342 disposable systemd replay: 22/22 focused tests, CI PASS,
  eight transient systemd ExecStartPre cases PASS with synthetic
  SQLite and precise journal reason checking.
- External Cosign v3.0.6 read-only verification of the exact running
  v0.5.21-hw immutable OCI image, expected GitHub Actions release
  certificate identity and OIDC issuer PASS. Installed source remains
  v0.5.15-hw and is not reconciled.
- A **separate, networkless Debian 13 disposable LXC**, no shared
  mounts, no Docker and no copied production data, completed **five
  real full guest stop/start cold boots**: normal pinned test start,
  pending with new committed WAL, repeat pending, missing pin, and
  clean restart after restoring a fake test pin. Every boot yielded
  expected systemd behavior. Initial 120 synthetic samples grew to
  121; the final 220.8 V row and complete logical SQLite SHA256
  remained unchanged through all five cold boots, and pending
  diagnostic evidence was preserved.
- Production remained online and Healthy with no Docker restarts;
  no live source, database, timer, power or Telegram operations.

## 7. What remains NOT verified

- A physical Proxmox host power-loss or storage-cache failure.
- A genuine production-equivalent Docker/Compose/updater co-start
  under actual loss-of-power and repeated crash conditions, with
  tested schema-compatible writer image and signed offline evidence.
- Independently reviewed offline signature/trust continuation.
- Fresh writer-fenced *current* production source+SQLite A/B.
- Independent security review, authorized release maintenance,
  and Telegram pinned message/media continuity acceptance.

A disposable LXC cold boot verifies systemd startup and logical
history preservation. It does **not** prove physical power-loss
durability or a successful start of the actual Telegram writer.

## 8. Release gate

1. Independent owner/security review of the proposed boot policy,
   exact image trust contract and all restart-owner dependency graph.
2. A machine-tested DB schema migration compatibility contract.
3. A fully isolated writer/Compose/updater cold-boot replica with
   failure injection, no public Telegram contact or real power cut.
4. Independent fresh, writer-fenced and fully verified private A/B
   backups with a tested restoration plan.
5. Explicit owner approval for signed source/image parity repair and
   a controlled production maintenance window.
6. Separate staging and public-channel acceptance for any future
   Telegram TEXT-to-PHOTO transition.

Until those gates pass: **BOOT CONTINUATION NO-GO,
PRODUCTION SOURCE INSTALL NO-GO, TELEGRAM PHOTO NO-GO**.

