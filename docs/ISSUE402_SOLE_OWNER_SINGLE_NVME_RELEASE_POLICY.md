# Issue #402 — sole-owner single-NVMe release governance

**Effective 2026-11-17. Owner decision recorded via Gateway.**
This document supersedes the *governance and infrastructure requirements*
in older Issue #402 notes that assumed an independent code reviewer or
a second physical backup device.

## Owner's approved operating constraints

- Production has **one physical NVMe failure domain**. No second
  independent device will be acquired, installed or requested.
- The sole owner has expressly **approved this single-device risk**,
  including irrecoverable loss of the measurement database and all
  local backup archives after a catastrophic NVMe/host failure.
- **Only the owner can review and authorize the project's source code.**
  A second-person or external reviewer is not required. Automated QA
  and transparent source/OCI/backup evidence must support the owner's
  review of the exact release candidate.
- The owner has **not** approved a particular candidate commit, image,
  version/tag, production cutover/rollback transaction or public PHOTO
  promotion solely by granting the infrastructure-risk approval.
- This decision is recorded in the private machine-readable receipt
  `/opt/shelly-voltage/validation/issue402-sole-owner-single-nvme-policy-20261117.json`.
  Never add the private receipt or credentials to source control.

## Technical gates that are still required

1. Verify exact version/tag, immutable signed OCI image and source hash,
   and obtain the owner's specific source and deployment authorization.
2. Verify no other live writer; check Compose, updater and systemd
   recovery fences and the real running image identity.
3. Produce fresh WAL-consistent local prechange SQLite backup(s) and
   validate SHA256, integrity, status/message marker parity and an
   isolated offline restore. **Do not call a local backup off-host.**
4. Ensure no unresolved Telegram send intent, duplicate publication,
   mismatched media marker or ambiguous write; test rollback of those
   conditions on disposable SQLite and fake Telegram.
5. At activation, require fresh Shelly telemetry, `/ready=200`,
   closed connectivity outage, and no unchecked pending update.
6. Test a version-compatible rollback without launching a second bot,
   losing the sole writer, or overwriting newer SQLite state.
7. Public PHOTO is a **separate explicit owner decision**, after a
   recipient-visible private test and media-ID-safe fallback.

## Required policy exception for one NVMe

Old off-host and independent reviewer prerequisites are **not active
requirements for this deployment**. Do not bypass their old checkers
by changing `offhost_backup_verified` to true, forge a reviewer signoff
or falsify deployment metadata. If automation still blocks on an old
requirement, implement an explicit narrow owner-only/single-NVMe
decision path with fail-closed tests, preserving all other safeguards.

The local archives cover accidental deletion and software/database
failures but **not** catastrophic loss of the only disk.

## Current status

Owner approval of single-NVMe risk: **GRANTED**.
Owner-only source-review process: **APPROVED**.

Exact source candidate review: **PENDING**.
Signed immutable new OCI/source parity: **PENDING**.
Specific production activation/rollback authorization: **PENDING**.
Public Telegram PHOTO authorization: **NOT GRANTED**.

Do not deploy this QA source while Shelly remains offline, and do not
treat success of synthetic tests as live Telegram recipient acceptance.
