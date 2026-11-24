# Issues 340/422/431: require old source Sigstore before signed cutover QA

Date: 2026-11-20 Europe/Kyiv.
Scope: source-only and isolated QA LXC114. No release, no production update.

## Identified release rollback trust gap

A full signed new source+OCI release proof and two old/new OCI
certificate checks do not establish that the OLD source archive
is authenticated. The legacy signed rollback snapshot includes
a SHA256 checksum for source.tar.gz, but that checksum itself
has no independently verified public Sigstore identity.

In particular, the deployed v0.5.25-hw OCI is verifiably signed,
but the old public v0.5.25-hw release lacks a matching, publicly
verifiable source TAR.sigstore.json asset. Treating a signed OCI
digest as authority to install an arbitrary checksum-valid old
source archive would be a rollback vulnerability.

## QA-only full pair preflight

scripts/qa-cutover-readiness-preflight.py now requires
--old-source-archive-dir /var/lib/svt-qa-old-source-20261120
when --verify-signatures is requested. Both the path and exact
--old-commit 40-hex Git SHA are mandatory. Identity-only checks
reject an extra old-source directory to prevent misleading modes.

After existing root-owned pending/sole-writer/Newest WAL/PHOTO
checks, the signed QA cutover verifier:

- binds the old immutable OCI digest and source version to the
  protected old snapshot receipt and the old-pair baseline;
- calls the actual pinned verify-signed-release-pair.py for the
  OLD source TAR, its Sigstore bundle, SHA256SUMS, exact old
  certificate Git SHA and old immutable OCI digest/attestations;
- rejects a missing old source bundle, changed old image, or
  any success report that lacks all three true source, OCI,
  and two-architecture OCI provenance flags;
- only then repeats the full NEW source TAR Sigstore and OCI
  proof against the protected new paired receipt;
- reports separate old/new source and OCI signature booleans,
  with no Docker switch, rollback, production mutation, or
  authorization.

Both verifiers remain read-only and can require public network
availability on actual full-signature runs. The test positives
use synthetic/mocked results and DO NOT show a real old source
signature has been obtained.

## QA acceptance

- 52/52 focused signed pair, latest rescue, cutover policy
  and negative-source tests PASS on LXC114.
- Missing old source staging directory blocks before Docker
  or cryptographic work; old invalid source probe blocks before
  new proof; old OCI digest drift and forged old source
  success evidence fail closed.
- Ruff0.14.2, Python project quality gates PASS.

## Outstanding production blockers

The old v0.5.25-hw source TAR Sigstore bundle does not exist
in the currently published release, so a real strict old/new
full-pair verification cannot succeed. Do not fake, waive or
relabel this missing proof.

Future work must decide whether a separately authenticated old
rollback artifact can be built and signed under the immutable old
source commit by an approved workflow, or provide a genuinely
authenticated alternative rollback recovery design; continue
QA solely with synthetic assets until satisfied.

The production legacy --recover, Docker restart=unless-stopped,
newest WAL+PHOTO recovery, hard-interruption and supervised
single-writer acceptance gates still apply. Owner Approval in
principle is not approval to bypass these technical gates.

## Complete matrix follow-up: isolate the database path in an old QA test

The initial LXC114 combined 590-test run exposed a preexisting
environment dependency in the separate legacy production updater
test: it inherited DATABASE_PATH from the process test environment,
so root-required could be returned for an unrelated QA database
before the tested live path was evaluated. Its setUp now explicitly
pins DATABASE_PATH to the canonical production value for that
purely read-only, guaranteed-to-refuse command test. No production
SQLite file is opened or modified.

36/36 focused updater, rollback and signed old/new source tests
PASS with this isolated environment. The original GitHub Actions
run 44649120368 for old source gate commit 1316420 completed
SUCCESS for Python3.11/3.12/3.13. A fresh exact-SHA CI run is
required for the additional test-harness isolation change.
