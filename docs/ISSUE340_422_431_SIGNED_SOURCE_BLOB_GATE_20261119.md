# Issues 340/422/431: signed source blob gate

Date: 2026-11-19 (Europe/Kyiv)
Status: source-only QA, not a released artifact or production cutover.

## Threat addressed

The existing OCI release has a valid immutable Cosign signature,
SBOM and SLSA provenance on the exact deployed v0.5.25-hw digest.
Its public source tarball has SHA256SUMS, but that checksum file is
not a cryptographic signature and cannot independently establish
the source publisher's identity.

A source archive must not be accepted by the new updater solely
because the release SHA256 matches, even when the image is signed.

## Proposed publication workflow

After reusable exact-head Python3.11/3.12/3.13 CI and version/tag
checks, the Source Release workflow:
- accepts only an invocation for the matching refs/tags/v<version>
  ref, not a manual branch pretending to release a tag;
- uses the full-SHA-pinned sigstore/cosign-installer and OIDC
  id-token: write, with no secret signing key;
- signs the exact reproducible source tar.gz with Cosign v3
  sign-blob --bundle;
- independently verifies the signed blob, expected release.yml
  tagged-workflow identity and GitHub Actions OIDC issuer, without
  disabling transparency log verification;
- uploads the exact tar.gz, its .sigstore.json and SHA256SUMS only
  after the signature verification succeeds.

## Proposed fail-closed updater source check

scripts/apply-update.sh::verify_source_archive now requests all
three release assets from the exact tagged GitHub Release:
- shelly-voltage-telegram-<version>.tar.gz;
- SHA256SUMS;
- shelly-voltage-telegram-<version>.tar.gz.sigstore.json.

It calls scripts/verify-source-release-bundle.py before storage
preflight, Docker image pull and any source/SQLite changes.

The verifier requires a trusted root-owned Cosign v3 binary, a
single root/owner-controlled regular artifact set, exact release
version markers, unique matching SHA256, bounded TAR entries, no
links, no duplicate/canonical-path collisions, no private source
or secret paths, mandatory writer_fence.py and the source verifier
itself in release packaging, and keyless signature verification of
the exact source bytes against the trusted tagged release workflow.

This is a separate check from OCI signature/provenance. A PASS from
the source verifier still emits release_authorized=false and does
not authorize changing production. The final source/OCI commitment
pair and safe signed update/rollback transaction remain necessary.

## Verification actually performed

- Confirmed actual production Cosign v3.0.6 supports
  sign-blob --bundle and verify-blob --bundle.
- New source verifier policy tests 14/14 PASS.
- Combined source-download, release workflow and verifier tests
  27/27 PASS.
- Selected linked updater/rollback/source/workflow regression
  172/172 PASS inside LXC114 network-none, read-only Docker,
  UID10001, no production secrets; project quality and Ruff PASS.
- Reproducible test_release.sh shell fixture PASS; the newly
  mandatory verifier is in the source TAR and missing it blocks
  source packaging.
- Real format v0.5.25-hw QA TAR structure, checksum, all source
  members and version markers PASS. When an unsigned dummy bundle
  was supplied, the actual verifier returned BLOCKED, with
  release_authorized=false and production_modified=false.
- Earlier verified actual LXC114 synthetic writer SIGKILL and
  guest-reboot WAL/PHOTO recovery, and GitHub CI SUCCESS for
  source SHA a60393f5e4cc2cbe1eb823a1bce87f5b1acda471.
  That prior CI result does not cover these new changes.

## Remaining non-negotiable blockers

No NEW published source bundle is actually Cosign-signed and
verified. The older public v0.5.25-hw source release does not
contain such a bundle, so the new source verifier must reject it.
The SHA and release version of a future NEW tag must be selected
and signed only after exact-head CI and sole-owner updater/rollback
integration are accepted.

Production LXC110, its legacy systemd --recover hook, old signed
Docker image, SQLite/WAL, Telegram messages and updater state are
unchanged. Do not run the legacy recovery path or deploy from
these source-only QA checks.

## Safe read-only commands

    python3 scripts/verify-source-release-bundle.py --help
    bash scripts/apply-update.sh --help
    bash tests/test_release.sh

Never use a synthetic test bundle or source SHA256 alone as
approval for a real firmware/chatbot production update.
