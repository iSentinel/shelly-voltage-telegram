# Issues 340/422/431: bounded public OCI signature verification retry

Date: 2026-11-19, Europe/Kyiv
Status: uninstalled source-only QA workflow. No public release created.

## Reason

A signed OCI image may take a short time to become discoverable
through GHCR's public anonymous registry interface. A one-shot
post-signing verification is fail-closed but could reject an
otherwise valid new release during brief registry propagation.
It must not be replaced with an ignored verification failure.

The tag-push-only image workflow now performs up to three
anonymous Cosign verify attempts under a newly created, empty
DOCKER_CONFIG directory. It checks the same exact immutable
digest from the already CI-gated build with these constraints:
- GitHub image.yml workflow identity at refs/tags/vVERSION;
- GitHub Actions OIDC issuer;
- exact workflow Git SHA, tag reference and repository;
- pinned installed Cosign v3;
- no insecure-ignore-tlog, no continue-on-error;
- all three attempts failing exits nonzero, preventing release
  job success. Retry delay is bounded, without background work.

The temporary empty Docker configuration and verification
output files are always cleaned by the shell EXIT trap.
Existing main/manual image building remains unsigned and
cannot execute the tag-only verification step.

## Test evidence

The original current v0.5.25-hw image, produced by successful
tag-build run 44218556630, has not been modified. That job
successfully called cosign sign on the exact deployed digest,
but local diagnostic Cosign returned "no signatures found",
and anonymous legacy signature tag and OCI referrers queries
returned HTTP404. This remains UNRESOLVED; no true public
cryptographic proof was obtained. The old published source TAR
also lacks Sigstore bundle.

The proposed future release gate was tested OFFLINE with a fake
Cosign executable (not real cryptographic signing):
- first verify success -> PASS after one call;
- first failure then second success -> PASS after two calls;
- all three attempts fail -> exit1, no PASS;
- branch ref instead of release tag -> exit6, no verify call;
- shell Bash -n and workflow YAML parse PASS.
- Focused source workflow policy tests 39/39 PASS;
  Ruff0.14.2, maintainability and formatting gate PASS.

This test does not authenticate any real new release.
Actual new signed source TAR and OCI index verification,
single-writer update, newest SQLite/WAL/PHOTO and Telegram
rollback are still required before production deployment.
