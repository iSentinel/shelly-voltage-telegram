# Issues 340/422/431: block partial public signed-source releases

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA; no release or production changes.

## Problem confirmed

The source TAR/Sigstore and OCI workflows are separate GitHub
Actions jobs. A source GitHub Release could previously be created
after source signing but before the tagged image digest was
publicly discoverable, cryptographically verified or associated
with the same CI-tested Git SHA. The existing v0.5.25-hw public
source release has a TAR and SHA256SUMS but no Sigstore source
bundle, and the old public OCI signature is independently
unverified even though its image signing workflow returned success.

No new source publication should be considered complete until
its OCI image is publicly signed and can be checked by anonymous
clients, including matching linux/amd64 and linux/arm64 provenance.

## Source-only implementation

scripts/qa-public-oci-tag-index.py:
- resolves only exact vMAJOR.MINOR.PATCH-hw tags using an anonymous
  public GHCR pull token, never Docker or production credentials;
- fetches the OCI multiarch index with a 2 MiB response limit;
- requires the exact advertised Docker-Content-Digest to equal
  SHA256 of the fetched index bytes;
- rejects missing index members, mutable tags and unexpected
  manifest media types;
- returns only the immutable digest with an explicit
  content-only, non-signature, non-authorization status.

.github/workflows/release.yml:
- after the source TAR Sigstore sign/verify but BEFORE creating
  GitHub Release, the source job resolves the exact version-tagged
  OCI index digest using the new helper;
- uses an empty temporary DOCKER_CONFIG and pinned Cosign v3 to
  verify the public OCI signature at that exact immutable digest;
- requires GitHub Actions OIDC issuer, image.yml workflow identity,
  exact source Git SHA, release tag ref and repository claims;
- runs existing verify-published-image.py against both platforms,
  requiring the matching immutable Git revision, SLSA and SPDX;
- performs bounded retries while an asynchronously signed image
  becomes publicly discoverable; all failed attempts exit nonzero
  and prevent the GitHub Release creation;
- never falls back to a mutable tag, ignores verification errors,
  or uses a signing successful step itself as trust evidence.

## Actual non-intrusive verification

- Anonymous public GHCR v0.5.25-hw tag returned HTTP 200 with
  Docker-Content-Digest exactly equal to its raw JSON SHA256:
  sha256:471981b896bd208969ad0ccc8977fe0b0db7712e0e3b1640b788d7c11d1b1c62.
  This matches the pinned OCI currently deployed on LXC110.
- The live public tag resolver returned exactly this digest and
  rejected the mutable tag "latest", with no Docker pull.
- Isolated LXC114 Ruff0.14.2 and project quality gate PASS.
- 6/6 resolver tests PASS.
- 40/40 source-release workflow policy/executable Bash and resolver
  tests PASS, including: complete synthetic success, missing
  public signature, wrong metadata/provenance, missing image tag,
  mutable digest and wrong release ref/commit.
- These successful tests use fake Cosign and Python APIs:
  they DO NOT prove a new signed source+OCI pair exists.

## Remaining release acceptance

A future tag must truly publish the new signed source TAR, source
Sigstore bundle and SHA256SUMS together, alongside its independently
verifiable signed OCI index built from the same immutable Git SHA.
Actual two-version signed update and rollback with a single live
writer preserving newest SQLite/WAL/PHOTO and Telegram state still
must pass in isolated QA before any production deployment.

No Git tag, GHCR image or GitHub Release was produced by this task.
No production /opt/shelly-voltage executable or Docker/systemd/SQLite
state was changed.
