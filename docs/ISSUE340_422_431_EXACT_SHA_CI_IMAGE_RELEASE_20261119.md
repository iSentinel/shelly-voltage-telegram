# Issues 340/422/431: exact-SHA CI gate for signed image builds

Date: 2026-11-19. Status: source-only; no GitHub push/tag/promotion.

## Problem

The prior image.yml could build and sign a tag without waiting for
the Python3.11/3.12/3.13 matrix CI to finish on the exact same
source. A manual build could also resolve another branch between
testing and image checkout.

## Source-only workflow change

- ci.yml now supports workflow_call with an optional test_ref input.
  A resolve job checks out that ref and publishes a 40-digit Git SHA.
- Every Python 3.11/3.12/3.13 test job depends on the resolve job
  and checks out its immutable SHA, not a branch or mutable ref.
- The reusable workflow returns that tested commit to its caller.
- image.yml now has a required ci job that calls the reusable
  workflow with the same ref requested for the image. Its build
  job needs successful ci, checks out only needs.ci.outputs.tested_commit,
  and fails if actual HEAD differs from the tested SHA.
- The prior tag-only signing guard and image_release_policy.py still
  apply. No continue-on-error or skipped-CI fallback is allowed.

## Evidence

- Independent PyYAML BaseLoader validation PASS for ci.yml and
  image.yml; tested_commit output, exact ref binding and matrix
  [3.11, 3.12, 3.13] confirmed.
- New static workflow source-policy tests plus existing release
  policy tests: 15/15 PASS.
- 220/220 selected LXC114 isolated tests PASS, Ruff0.14.2 and
  project quality gate PASS. This is Python3.13 only.
- No image build, OCI signing, CI run or GitHub branch push occurred.

## Still blocking release

The modified workflow has NOT executed on GitHub against this
exact branch SHA. The full unittest suite still needs CI on 3.11,
3.12, 3.13. The local selected suite emitted ResourceWarning for
unclosed sqlite3.Connection objects; GitHub CI rejects warnings,
so this must be diagnosed and fixed before promotion.

The real updater and rollback still need unified signed old/new
source+OCI validation, latest WAL/PHOTO rescue and sole-writer
lifetime protection, with interrupted process recovery acceptance.
Owner Approval was given, but no concrete new signed image exists.

Do not push a release tag or update production from these checks.
