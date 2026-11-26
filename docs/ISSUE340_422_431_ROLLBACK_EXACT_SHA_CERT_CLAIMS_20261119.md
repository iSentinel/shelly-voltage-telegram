# Issues 340/422/431: exact old/new OCI certificate claims for rollback QA

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA (LXC114), no production update or release.

## Existing gap

The signed source/OCI main release verifier already required a
matching source commit. The rollback QA and legacy rollback-snapshot
preflights could report signed OCI identity based on only the
GitHub workflow URI at a version tag and the OIDC issuer, with no
requirement for the precise Git SHA in either OCI certificate.

Such a result is not sufficient for a real signed cross-version
rollback or source+OCI provenance pairing.

## Implemented fail-closed changes

1. scripts/verify-signed-release-pair.py (commit caa2e74):
   OCI Cosign verification requires --certificate-github-workflow-sha
   equal to the exact signed source --commit plus the tagged
   --certificate-github-workflow-ref and fixed repository claim.

2. scripts/qa-paired-rescue-preflight.py:
   --verify-signatures now requires BOTH --old-commit and
   --new-commit, each exactly 40 lowercase hex digits. Both OCI
   verifications enforce the exact source SHA, tag ref and repo
   certificate claims, in addition to the workflow identity,
   OIDC issuer and pinned immutable digest. The new commit must
   also equal source_commit from a private paired release receipt.
   Missing or mismatching evidence blocks before any Cosign call.
   Identity-only QA mode remains separate and does NOT claim
   verified signatures.

3. scripts/signed-rollback-pair-preflight.py:
   Its optional --verify-signature path now requires
   --expected-commit and checks that exact SHA, version tag ref
   and repository in the historical OCI Cosign certificate.
   Missing commit refuses verification. Snapshot identity-only
   checks still function without a commit and do not grant
   production rollback authority.

4. QA fixture and tests:
   The synthetic new private receipt includes source_commit.
   The isolated QA runtime shell passes two explicit dummy Git
   SHAs into the expected missing-Cosign negative test. They
   are TEST inputs, not actual release certificates.

## Evidence and limitations

- Ruff0.14.2, project quality/maintainability gate PASS.
- 57/57 focused primary signed-source/OCI policy tests PASS.
- 73/73 joined rollback old/new signature and receipt policy
  tests PASS; malformed SHA or mismatch are negative tests.
- No real new signed source TAR/OCI index exists. Positive tests
  use mocked Cosign results and synthetic release receipts.
- Old production v0.5.25-hw source TAR Sigstore bundle is absent,
  its historical OCI signing job succeeded, but anonymous
  independent signature verification remains unresolved.
- Even a genuinely verified OCI certificate proves only the
  signed image SHA, not that an old local unsigned source snapshot
  byte-for-byte equals a signed source TAR. Full signed old/new
  source+OCI pairing and WAL/PHOTO/Telegram preservation still
  require separate real QA acceptance before any production action.

## Example isolated read-only checks

    python3 scripts/qa-paired-rescue-preflight.py --help
    python3 scripts/signed-rollback-pair-preflight.py --help

Never run either QA-only preflight against LXC110 production paths
or interpret a signature-only PASS as deployment authorization.
