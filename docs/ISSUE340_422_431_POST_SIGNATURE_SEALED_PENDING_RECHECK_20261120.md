# Issues 340/422/431: recheck newest WAL after remote signature verification

Date: 2026-11-20 Europe/Kyiv
Scope: source-only QA. Production untouched.

## Gap

The QA cutover already verified old/new signed source TAR Sigstore,
OCI index certificates and SLSA/SPDX attestations. Those checks may
contact public services and take significant time. A matching
transaction token and complete newest SQLite/WAL/PHOTO parity
checked BEFORE network verification could become stale if some
other process clears the pending marker, publishes a different
release receipt, or resumes a monitoring writer during Cosign.

A successful cryptographic check must not be presented as a fresh
cutover coherence proof without rechecking transaction state.

## Exact invariant

The strict signed QA cutover now calls
final_signed_pending_recheck() immediately after the old and new
source/OCI pair proofs, prior to any positive report.

It reuses the existing source-pinned
qa-stage-verified-pair-receipt.py sealed_pending_receipt() context
manager, which holds BOTH the shared quiescent-writer lease and
the update transaction lock. Under those locks it verifies:

- the same protected, root-owned pending transaction token;
- a stopped single writer and logical parity of newest committed
  SQLite rows, including L1/L2/L3, Telegram PHOTO and all WAL;
- an unchanged root-owned new exact-source Git SHA, OCI digest,
  source TAR SHA256, version and transaction-token receipt;
- an unchanged old rollback source version and immutable OCI digest.

The shared locks are released on return, and this is STILL QA
evidence only; no activation, Docker start, release authorization
or rollback authorization is performed.

## Tests and limitations

- LXC114 84/84 focused signed cutover, source stage, sole writer,
  pending and paired rescue tests PASS.
- Changed paired receipt after network verification is blocked;
  absent/unsafe pending is blocked before receipt acceptance;
  old OCI digest changed after signature verification is blocked.
- Identity-only (unsigned) QA reports never enter the signed
  transaction recheck or claim any cryptographic proof.
- Ruff0.14.2 and source maintainability checks PASS.

The test successes use mocked Cosign success, NOT a real
cross-version old/new fully signed release acceptance. The
public old v0.5.25-hw source Sigstore bundle is absent. Production
still has Docker restart=unless-stopped and old updater/rollback
hooks; never change them without the independent technical gates.
