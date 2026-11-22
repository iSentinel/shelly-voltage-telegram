# Issues 340/422/431: read-only old/new cutover coherence

Date: 2026-11-19 (Europe/Kyiv)
State: isolated QA only. No actual signed image migration.

## Purpose

The older signed rollback source snapshot, incoming source+OCI
receipt, single writer fence and newest SQLite/WAL/PHOTO rescue
must refer to one consistent transaction before any image-changing
deployment can be considered. A matching release version alone does
not prove correct source bytes, a signed OCI pair, or safe rollback.

## Proposed QA-only preflight

The new scripts/qa-cutover-readiness-preflight.py requires an
isolated LXC114 root execution and explicit old-source snapshot,
old-signed snapshot, new root-private source receipt, source archive
directory, exact 40-digit hexadecimal target Git commit, current
root fence, database, newest rescue and matching pending token.

The read-only preflight uses the existing full-history and pending
checks, the old source/OCI snapshot paired-version checks and the
new supervisor receipt verification. It fails closed on:
- mismatched, altered, missing or untrusted root-private receipt;
- wrong pending token, absent WAL/PHOTO parity or running writer;
- old signed source version drift or new source target drift;
- same old and new OCI digest (not a real image change);
- changed source TAR SHA256 or Git commit mismatch;
- invalid mutable target commit and non-QA paths.

A successful result is explicitly PASS_QA_CUTOVER_COHERENCE_ONLY,
with source_signature_reverified=false, image_actually_changed=false,
release_authorized=false and rollback_authorized=false. It never
pulls, starts, replaces or rolls back Docker, clears pending, or
modifies a database.

## Evidence

- Focused source-only policy tests: 10/10 PASS, Ruff0.14.2 and
  project quality/maintainability PASS.
- Real isolated QA LXC114 synthetic old/new rollback fixture:
  old v0.5.25-hw, new v0.5.26-hw, separate private old/new source
  snapshots, different synthetic OCI digest identities.
- Same run exercised real Docker writer plus four actual SIGKILL
  checkpoints, newest WAL/PHOTO rescue, preserved Telegram pending
  send/cleanup queue, staged private source receipt with one matching
  transaction token and exact source SHA256.
- Read-only QA cutover preflight returned a positive coherence
  report for this synthetic pair while pending remained set.
  Mutable target commit refs/heads/main was rejected; production
  paths and missing Cosign were already fail-closed.
- After controlled pending release the SAME disconnected QA image
  resumed writing; L1/L2/L3 counts rose from 1 to 2 each with
  PHOTO and Telegram pending/cleanup state preserved.
- No production SQLite, live Telegram, systemd unit or Docker image
  was changed. All LXC114 artifacts, containers and markers cleaned.

## Limitations

The new source and OCI signature proof is MOCKED in this test. No
real new signed v0.5.26-hw image or Sigstore source bundle exists.
This is not evidence that an actual image transition, signed
rollback or production owner cutover was performed.

The production v0.5.25-hw service still uses a legacy boot recovery
hook and Docker autonomous restart policy. A real production release
requires a verified immutable new source+OCI pair, an authenticated
production receipt writer, only one systemd writer owner and an
actual new-image + rollback rehearsal preserving all WAL, PHOTO and
Telegram intent history under genuine process interruptions/reboot.

## QA-only commands

    python3 scripts/qa-cutover-readiness-preflight.py --help
    python3 -m unittest -q test_qa_cutover_readiness_preflight
    bash tests/qa_paired_rescue_runtime.sh

Only use the real QA shell script as root on isolated LXC114 named
shelly-docker-reboot-qa, with no network/Telegram access.
