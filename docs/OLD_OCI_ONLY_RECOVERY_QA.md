# Old OCI image-only recovery — non-authorizing QA evidence

This is a **candidate alternative**, not an exception to signed-source
rollback requirements and not a production recovery command.

The original public release `v0.5.25-hw` currently has a source archive and
`SHA256SUMS`, but no `.sigstore.json` source bundle. On 2026-11-20,
an independent read-only Cosign verification of the live immutable digest
`sha256:471981b896bd208969ad0ccc8977fe0b0db7712e0e3b1640b788d7c11d1b1c62`,
using the exact release tag Git SHA
`72fb94f8501fb5b0146881a74e059bb84888ca37`, failed with
`no signatures found`. Local image presence and its OCI revision label are
**not** a replacement for cryptographic proof.

`scripts/qa-old-image-trust-preflight.py` is a read-only, fail-closed
probe for a separately signed old OCI image. It requires a concrete old
version, full Git commit, and OCI digest, then uses the existing public
Cosign identity and multiarchitecture image/attestation verifier. It
never starts Docker, reads private secrets, touches SQLite, or authorizes
an update or rollback. The current old digest is expected to be **BLOCKED**
until the required public proofs are genuinely available.

Example **read-only QA** command:

~~~bash
python3 scripts/qa-old-image-trust-preflight.py --qa-only \
  --old-version 0.5.25-hw \
  --old-commit 72fb94f8501fb5b0146881a74e059bb84888ca37 \
  --old-digest sha256:471981b896bd208969ad0ccc8977fe0b0db7712e0e3b1640b788d7c11d1b1c62 \
  --jsonl
~~~

Before any future image-only rollback protocol can be accepted, prove:

1. Genuine old signed OCI index, exact release source revision and OCI
   attestations, not just a tag or local image label.
2. Explicitly approved self-contained image runtime: no mutable host code
   bind mounts, pinned release configuration/schema compatibility.
3. A root-protected old image-only rollback receipt and no independent
   source-install or legacy `--recover` path.
4. A single writer-fenced Docker owner, immutable image transition and
   durable transaction/pending receipt.
5. The **newest** live SQLite database and WAL, complete L1/L2/L3 history,
   meter selection, Telegram PHOTO identity and pending send intents,
   never an old snapshot rewind.
6. Real separate-process SIGKILL, restart and hypervisor reboot QA with
   no duplicate Telegram sends, plus full post-recovery health.
7. Exact immutable new signed source+OCI release, technical acceptance
   and explicit Owner Approval before a production cutover.

A positive probe establishes only item 1 and **still** has
`release_authorized=false` and `rollback_authorized=false`.
Never retrofit a signature by uploading a different unsigned source
under the old release name. Keep production unchanged.

## Read-only self-contained image mount evidence

A separate `scripts/qa-image-runtime-mount-audit.py` examines **only**
the bounded JSON array exported from `docker inspect --format '{{json
.Mounts}}'`. It rejects application-code bind mounts, unexpected
mounts, writable secrets, missing/wrong database mounts, source path
substitution and duplicate destinations. An old image-only rollback
cannot be described as code-self-contained unless the actual runtime
layout is independently checked.

For the current live image, only export a sanitized mounts array, never
the full inspect output (which can include environment variables):

~~~bash
umask 077
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
docker inspect shelly-voltage-bot --format '{{json .Mounts}}' > "$tmpdir/mounts.json"
python3 scripts/qa-image-runtime-mount-audit.py --qa-only \
  --mounts-json "$tmpdir/mounts.json" --jsonl
~~~

Use a private temporary directory and remove the file after the check.
This structural proof does **not** authenticate the OCI digest, the
host paths, a saved release receipt or the newest database. It can pass
while the separate old OCI signature check is blocked. Both reports
retain `rollback_authorized=false`.
