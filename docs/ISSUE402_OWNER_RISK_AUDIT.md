# Issue #402 — read-only sole-owner NVMe risk audit

The owner has explicitly approved running this installation on a single
physical NVMe, including the possibility of permanent loss of both the
SQLite measurement history and all local backup archives. No independent
reviewer or second physical device will be used.

This does **not** approve a particular source commit, OCI, version/tag,
deployment, rollback action or Telegram PHOTO promotion.

## Read-only command

On the QA source checkout, use a protected root-owned decision receipt:

```bash
python3 scripts/em50-owner-risk-audit.py \
  --risk-receipt /opt/shelly-voltage/validation/issue402-sole-owner-single-nvme-policy-20261117.json \
  --candidate-commit 309d3c471cee45121662ad3eac1a3f62ffc1df0f \
  --jsonl
```

The source commit is **an unverified label**, not Git or OCI
attestation. The result must retain all of these distinctions:

- `owner_single_nvme_risk_approved=true`
- `sole_owner_review_model_approved=true`
- `offhost_backup_verified=false`
- `candidate_commit_verified=false`
- `source_review_approved=false`
- `signed_oci_verified=false`
- `release_authorized=false`
- `photo_authorized=false`

The private receipt must be regular, singly linked, owned by the
executing UID, mode 0600, unmodified, and no more than 8192 bytes.
Symlinked paths, malformed JSON, an unzoned future receipt, incorrect
types and contradictory PHOTO/release approval are rejected.

## Validate release isolation

```bash
python3 scripts/em50-owner-risk-audit.py \
  --risk-receipt /opt/shelly-voltage/validation/issue402-sole-owner-single-nvme-policy-20261117.json \
  --candidate-commit 309d3c471cee45121662ad3eac1a3f62ffc1df0f \
  --enforce-release --jsonl
```

Expected exit code: **2** for a valid owner risk waiver with **no**
release authorization. A malformed/missing decision returns **1**.
A valid governance-only check without `--enforce-release` returns 0.
Never treat the status `RISK_ACCEPTED` as deployment approval.

## Release limits

A signed immutable OCI, verified Git/tag/source and app hash, online
Shelly readiness, local WAL-consistent backup/restore, single-writer
fence, no pending Telegram sends, version-compatible rollback and
specific owner authorization remain independent release gates.
PHOTO additionally requires separate owner opt-in and recipient-visible
acceptance.

This tool never writes a production SQLite database, edits
`deployed.json`, changes any release flag, runs Docker or Telegram,
or publishes to a public channel. Synthetic QA uses disposable private
receipts without network access as UID root and UID nobody.
