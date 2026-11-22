# Issues 340/422/431: exact OCI certificate claims in signed pair preflight

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA, no production deployment.

## Gap closed

scripts/verify-signed-release-pair.py already required the source
Sigstore keyless certificate to bind to the exact Git SHA, release
tag reference and repository, while the OCI Cosign verification
only checked workflow URL and OIDC issuer.

After this change the same immutable index digest is additionally
required to carry certificate claims matching all of:
- --certificate-github-workflow-sha: exact --commit SHA
- --certificate-github-workflow-ref: refs/tags/vVERSION
- --certificate-github-workflow-repository:
  iSentinel/shelly-voltage-telegram

The verifier still requires the keyless image.yml identity, GitHub
Actions issuer, pinned root-owned Cosign v3 binary, signed source
TAR bundle, exact source SHA256, and matching OCI revision/attestation
for linux/amd64 and linux/arm64. No weak fallback or ignore-tlog
switch was added. Its PASS status never authorizes deployment or
rollback. The tests use a MOCKED Cosign subprocess and therefore
do not constitute real cryptographic release evidence.

## Validation

- LXC114 QA source-only Ruff0.14.2 and project quality gate PASS.
- Signed source, signed OCI, paired QA and tagged image workflow
  policy tests: 57/57 PASS, including an exact source SHA switch
  and failing Cosign certificate probe with no loose retry.
- Production LXC110 is not changed. Public source TAR Sigstore
  bundle remains absent on v0.5.25-hw and current OCI signature
  is not independently verified. The candidate new version
  v0.5.26-hw is not published.

## Next

The historical and QA-only rollback OCI signature paths also use
only tag workflow identity and issuer. Before a real signed rollback,
require actual old/new source commit provenance and enforce the
same exact SHA/ref/repository certificate claims on BOTH artifacts.
Never accept a source-version string as proof of Git commit.
