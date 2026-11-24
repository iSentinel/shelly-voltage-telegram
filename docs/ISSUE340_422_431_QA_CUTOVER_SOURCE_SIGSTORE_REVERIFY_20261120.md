# Issue 340/422/431: reverify full source Sigstore/OCI at cutover QA gate

Date: 2026-11-20 Europe/Kyiv
Scope: LXC114-only read-only QA, no cutover or production update.

## Gap and resolution

The earlier exact old/new OCI signature checks in
scripts/qa-cutover-readiness-preflight.py relied on a previously
staged protected source+OCI receipt for the new release. The
cutover preflight compared the source TAR bytes with that receipt,
but did not repeat the original keyless Sigstore authentication.

In explicit --verify-signatures mode, this preflight now:

- validates the exact old Git SHA and new receipt Git SHA;
- validates the root-protected pending token, stopped QA writer
  and complete latest SQLite/WAL/PHOTO parity;
- requires both exact-SHA old/new OCI certificate checks;
- reads the protected new release version, immutable OCI digest,
  Git SHA, source TAR, Sigstore bundle and SHA256SUMS from the
  QA-only private release stage;
- re-runs the actual pinned read-only
  scripts/verify-signed-release-pair.py verifier against the
  staged ORIGINAL archive bytes and its Sigstore bundle;
- requires the full release evidence result and matching exact
  version, commit and digest plus source signature, OCI signature
  and both-platform attestations; refuses any incomplete proof;
- reports source_signature_reverified=true ONLY when this
  full check returns PASS. This flag is false in identity-only QA.

The preflight is always QA-only and non-authorizing:
image_actually_changed=false, production_modified=false,
release_authorized=false, rollback_authorized=false.
No live Docker, Telegram, SQLite, systemd or updater action.

The true source+OCI verifier can only succeed with trusted
Cosign v3, real signed TAR assets, matching immutable OCI digest
and published multiarch provenance. Positive unit tests use
mocks; a new authenticated release is still missing.

## Isolated acceptance

- In LXC114, 87/87 focused cutover, paired source/OCI and
  old/new rollback policy tests PASS.
- Mismatching private receipt commit blocks before source probe.
- Missing source Sigstore bundle rejects the signed cutover.
- A forged success lacking source_signature_verified refuses.
- Correct source TAR path, Sigstore bundle and exact Git SHA
  are forwarded to the pinned verifier, with no relaxed fallback.
- Ruff0.14.2, project formatting and quality gates PASS.

## Production blocking risks

The latest public v0.5.25-hw release still has no source
TAR.sigstore.json bundle. Both old/new immutable signed sources
and images have NOT been jointly authenticated for a real
cross-version rollback. The legacy updater and autonomous
Docker unless-stopped restart policy remain unsuitable for
sole-owner production cutover. Do not change LXC110 runtime
until those requirements are satisfied.
