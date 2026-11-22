# Issue 340/422/431: strict old/new OCI signatures in QA cutover readiness

Date: 2026-11-20 Europe/Kyiv
Scope: isolated LXC114 QA source only, no production activation.

## Gap

scripts/qa-cutover-readiness-preflight.py previously joined an
old/new version receipt and latest SQLite-WAL-PHOTO parity, but
only in unsigned identity-only mode. The joined cutover preflight
had no way to insist on exact old and new OCI signatures, even
though the lower-level paired-rescue validator supported them.

## Source-only policy change

The cutover preflight now supports an explicit
--verify-signatures --old-commit OLD40_SHA mode while its existing
--target-commit NEW40_SHA binds the new paired release receipt.
The root-only QA guard checks exact SHA shapes before touching
the rescue evidence, and a stray --old-commit without
--verify-signatures is blocked rather than silently ignored.

The lower-level paired-rescue validator is invoked with both
exact commits; it requires trusted Cosign v3, tagged GitHub
workflow identity/issuer/repository/SHA certificate claims on
both immutable old and new OCI indexes and a new SHA matching
the root-protected paired source receipt.

The joined preflight only accepts the precise matching result
(PASS_QA_SIGNED_OCI_AND_WAL_ONLY with both signature booleans
true). Identity-only mode only accepts unsigned identity
evidence, with both signature booleans false. These modes cannot
borrow or confuse one another.

Signed QA status is now
PASS_QA_CUTOVER_SIGNED_OCI_COHERENCE_ONLY, with explicit
old_oci_signature_reverified and new_oci_signature_reverified.
source_signature_reverified remains false: this invocation
rechecks the protected source archive bytes and prior receipt,
but does not itself rerun keyless source Sigstore verification.
image_actually_changed, production_modified,
release_authorized and rollback_authorized are all false.
No Docker, SQLite, systemd or Telegram action is performed.

## QA evidence

- LXC114 focused cutover, paired-rescue, trusted signature,
  rollback and supervised-owner tests 113/113 PASS.
- Missing old SHA blocks before lower-level I/O.
- Stray SHA without signatures rejects.
- Forged signed status with a missing OCI proof blocks.
- Signed evidence presented to identity-only mode rejects.
- Correct two-SHA signed evidence reports explicit verification
  of both OCI signatures, without granting deployment authority.
- Ruff 0.14.2, project quality/maintainability checks PASS.

Successful test signatures here are mocked; there is still no
actual signed new source+OCI release pair to authenticate, and
the actual guarded rollback/sole writer cutover remains blocked.
Never run this QA helper against production LXC110 paths.
