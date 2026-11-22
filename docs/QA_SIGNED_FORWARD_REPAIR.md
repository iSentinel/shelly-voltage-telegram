# Signed forward-only repair — QA-only alternative to old unsigned rollback

The old v0.5.25-hw OCI digest has no independently discoverable Cosign
signature, and the public old source TAR has no Sigstore bundle. A local
image digest or SHA256SUMS does not authorize executing an old rollback.

A safer *candidate strategy* is to review and rebuild equivalent repair
functionality in a NEW version, produce a newly signed source TAR with
Sigstore and a signed/attested multiarchitecture OCI index, then test
and approve it as a new forward release. Do not retrofit an old release.
Until this new release really exists, the signed preflight must block.

The QA-only read-only tool in
scripts/qa-signed-forward-repair-preflight.py requires:

- strictly greater version than the current image and a distinct OCI digest;
- one exact Git SHA bound to source Sigstore, OCI signature and attestations;
- no application-code bind mounts on the specified runtime mount inventory.

This proves only identity and mount layout. It does NOT prove newest
SQLite/WAL/PHOTO/pending continuity, root-owned single writer transaction,
boot/reboot recovery, signed deployment receipt or user Owner Approval.
Every result reports release_authorized=false, rollback_authorized=false,
production_modified=false. No Docker, SQLite, secrets, backup or Telegram
actions are implemented in this tool.

Example read-only QA command using future genuine signed candidate files:

    python3 scripts/qa-signed-forward-repair-preflight.py --qa-only \
      --current-version 0.5.25-hw --current-digest sha256:CURRENT_64_HEX \
      --candidate-version 0.5.26-hw --candidate-commit FULL_40_HEX \
      --candidate-digest sha256:NEW_64_HEX \
      --archive /secure/candidate.tar.gz \
      --bundle /secure/candidate.tar.gz.sigstore.json \
      --sha256sums /secure/SHA256SUMS \
      --mounts-json /secure/mounts.json --jsonl

Never run legacy apply-update.sh --recover or restore older SQLite.
The production bot remains on the verified healthy runtime while these
release gates are incomplete.

## Crash evidence: committed WAL and Telegram pending state

The synthetic regression in tests/test_qa_forward_wal_pending_sigkill.py
uses the actual fenced-newest-rescue.backup_consistently implementation
with a real uncheckpointed SQLite WAL. A separate disposable process
is killed using SIGKILL after fsync of the rescue and after fsync of
its synthetic SHA receipt. The tests compare full logical SQLite
digests, all three phase rows, stored PHOTO identity, both pending
Telegram send markers and the cleanup retry queue. A later live
measurement makes a prior rescue stale; no old snapshot is installed.

This is **not** a successful real Docker handoff, Telegram API test,
hypervisor reboot or power-loss durability test. The synthetic QA
scripts cannot authorize a live cutover and do not contact Shelly.
