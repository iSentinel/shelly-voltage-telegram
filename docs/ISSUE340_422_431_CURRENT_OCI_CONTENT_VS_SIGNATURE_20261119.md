# Issues 340/422/431: separate real OCI content from signed release proof

Date: 2026-11-19 (Europe/Kyiv).
Scope: read-only public GHCR and source-only QA. No Docker pulls.

## Real pinned production OCI content check

Production LXC110 uses immutable image index digest:
sha256:471981b896bd208969ad0ccc8977fe0b0db7712e0e3b1640b788d7c11d1b1c62

Public Git tag v0.5.25-hw resolves to immutable commit:
72fb94f8501fb5b0146881a74e059bb84888ca37

Ran the existing scripts/verify-published-image.py source-only
against the public GHCR registry, with the above digest and commit:
- linux/amd64: registry SHA256 bytes, revision, SPDX 2.3,
  SLSA build/provenance and image-manifest subject digest PASS;
- linux/arm64: the same content and metadata checks PASS;
- overall real OCI index and metadata validation PASS.
- A separate real GHCR read-only check against the SAME digest
  but deliberately WRONG commit (forty zeroes) failed as required,
  specifically Unexpected source revision on linux/amd64.

These metadata checks DO NOT verify a Sigstore/Cosign signature.
The source helper docstrings and successful summary now explicitly
state that OCI content passed but the signature was NOT checked.
The actual real signed-pair verifier still performs a separate
Cosign verify and validates the source TAR signature, then checks
both OCI platforms against the same exact Git commit.

## Public source release gate remains BLOCKED

The public v0.5.25-hw release has uploaded source TAR and
SHA256SUMS, but no source TAR.sigstore.json Sigstore asset.
The exact Git tag SHA was cross-checked using the public tag ref.
The new read-only scripts/qa-release-asset-inventory.py
returned exit=3, BLOCKED, missing the Sigstore asset. This
gate cannot authorize installation and its public JSON inputs
are not themselves cryptographically trusted.

The next real release must have full source TAR Sigstore
verification, signed OCI index verification, attestations and
an authenticated deployment receipt before production changes.

## QA evidence

- New standalone OCI signature-vs-content wording contract and
  signature/pair tests: 26/26 PASS.
- Public release inventory local synthetic tests: 11/11 PASS.
- Selected offline release, writer, rollback and boot-owner
  regression: 336/336 PASS.
- Ruff0.14.2, project formatting and maintainability PASS.
- No production SQLite, Telegram, Docker or systemd changes,
  no release/tag created, and no actual new signed image deployed.
