# Issues 340/422/431: reject OCI tag movement before source publication

Date: 2026-11-20, Europe/Kyiv
Scope: source-only release workflow, no tag or production mutation.

## Gap

A correctly signed immutable OCI digest can be verified while a
versioned GHCR tag is still mutable. If the tag later moves to
another digest, a GitHub Release could be published whose source
SHA matches the VERIFIED image digest but not the CURRENT public
version-tagged image.

## New source workflow prepublication gate

After anonymous Cosign verification and per-platform SLSA/SPDX/
commit provenance all pass, source release resolves the public
version tag again with a bounded independent GHCR request.
The second digest must exactly equal the already verified digest.
Missing or changed tag exits with status 9; it cannot create a
GitHub Release or claim "PAIR READY".

The recheck only narrows an ordinary tag-race window. It does
NOT cryptographically lock a mutable GHCR tag after this point:
full production cutover must remain pinned by immutable OCI
digest and authenticated source+OCI release receipt.

## Tests (isolated LXC114, source-only)

- Exactly two reads for a stable versioned tag: PASS.
- Second tag lookup changes to a different sha256: blocked exit 9.
- Second lookup fails/disappears: blocked exit 9.
- A signed digest with valid metadata is not sufficient when the
  final tag no longer points to it.
- Source policy, executable Bash mock and workflow suite:
  22/22 PASS; Ruff0.14.2, quality gate and Bash -n PASS.

No GitHub release, image, source tag or production deployment
was performed, and no SQLite/WAL/PHOTO/Telegram or Docker runtime
state was changed by these tests.
