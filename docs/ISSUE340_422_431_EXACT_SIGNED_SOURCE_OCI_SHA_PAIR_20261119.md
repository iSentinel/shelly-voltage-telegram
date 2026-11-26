# Issues 340/422/431: exact-SHA signed source and OCI pair preflight

Date: 2026-11-19 (Europe/Kyiv)
Status: source-only QA. No new production release or service cutover.

## Required cryptographic pair

Source-blob Cosign verification alone does not demonstrate that
the signed source and signed OCI index came from the same commit.
A tag name or matching release version alone is not sufficient.

The proposed Source Release workflow already requires an exact
CI-tested Git commit for release builds. It now independently checks
the Fulcio GitHub Actions certificate claims on its signed blob:
- exact GitHub release.yml tagged-workflow identity;
- exact GitHub OIDC issuer;
- exact SHA of the tested checkout;
- exact refs/tags/v<version> workflow ref;
- exact iSentinel/shelly-voltage-telegram repository.

The installed Cosign v3.0.6 verify-blob help was checked read-only
and explicitly supports --certificate-github-workflow-sha,
--certificate-github-workflow-ref and
--certificate-github-workflow-repository.

The source-only scripts/verify-source-release-bundle.py accepts
--expected-commit with an exact 40-digit hexadecimal value.
This additional proof is validated; it sets source_commit_bound=true
only if the signature passes verification against the exact SHA.
The normal source updater authenticates source bytes but does not
by itself establish full source-to-OCI identity.

The separate read-only scripts/verify-signed-release-pair.py
requires explicit version, exact source commit, immutable OCI
digest, source TAR, Sigstore bundle and SHA256SUMS. It:
1. Requires signed source identity with that precise commit,
   tagged release workflow, repository and OIDC issuer.
2. Requires Cosign verification of the OCI index against the
   pinned image.yml tagged workflow and GitHub OIDC issuer.
3. Reads immutable OCI objects from public GHCR, then uses
   verify-published-image.py to prove linux/amd64 and linux/arm64
   OCI config revision matches the exact source commit and that
   SPDX/SLSA attestation subjects match image manifest digests.

The proof is read-only, always sets release_authorized=false and
rollback_authorized=false, and never starts Docker. An invalid
version, SHA, signature, digest, architecture, revision or
provenance fails closed.

## QA evidence

- Exact-commit source bundle, release workflow, download policy
  and joined signed pair tests PASS.
- New signed-pair policy 9/9 PASS with synthetic trust mocks.
- Selected integrated source/OCI, updater, rollback, fence and
  Pro3EM regression 227/227 PASS inside LXC114 offline,
  read-only-rootfs Docker with UID10001 and Python3.13.
- Ruff0.14.2 and project maintainability/quality PASS.
- Complete reproducible source release shell fixture PASS;
  the source and pair verifiers are mandatory archive members.
- Real v0.5.25-hw format source TAR combined with a dummy
  unsigned bundle and the known genuine v0.5.25-hw OCI digest
  was correctly BLOCKED before public registry use.
- No new source release was actually signed and no real new
  source+OCI pair was verified. All positive signature tests
  used isolated mocks, and no release was deployed.
- Earlier v0.5.25-hw OCI Cosign and public SPDX/SLSA attestation
  checks remain valid. Its existing source release does not
  have a source Sigstore blob signature.

## Outstanding production release blockers

A real new immutable signed release/source bundle/image digest
and an approved exact version do not exist yet. The production
legacy apply-update.sh --recover could rewind newer WAL/SQLite
history, and Docker restart and systemd still compete as owners.
The new guarded source+OCI preflight does NOT authorize this path.

Real release must use one durable root-owned pending and sole writer
owner; latest full SQLite/WAL/PHOTO plus pending Telegram-intent
rescue; verified exact source+OCI and deployment receipt binding;
actual signed version change and rollback under sudden SIGKILL
and guest reboot; and an exact immutable manifest. Owner Approval
does not bypass these technical gates.

## QA-only commands

    python3 scripts/verify-signed-release-pair.py --help
    python3 scripts/verify-source-release-bundle.py --help
    python3 -m unittest -q test_signed_release_pair
    bash tests/test_release.sh

Both verification commands are read-only. Never use synthetic
signed-pair QA results for production deployment.
