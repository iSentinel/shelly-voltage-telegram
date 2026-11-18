# Issue #422 — signed rollback pair identity preflight

## Purpose

The existing rollback.sh deliberately blocks old source snapshots when
the live immutable signed OCI image has a newer release identity. Do not
bypass SNAPSHOT_SIGNED_VERSION_MISMATCH with an unsigned tag.

Two different snapshot families must be paired:

- install.sh captures the old source archive, old/new version manifest,
  SHA256 sidecars and SQLite under /var/backups/shelly-voltage/upgrades/.
- apply-update.sh captures the protected prior deployed.json and
  override.yaml plus an independent SQLite archive under
  /var/lib/shelly-voltage-updater/snapshots/.

The new scripts/signed-rollback-pair-preflight.py checks both families
without stopping, running, changing or restoring the live container.
It never authorizes rollback.

## Read-only usage

Default offline mode:

~~~bash
python3 scripts/signed-rollback-pair-preflight.py \
  --source-snapshot /var/backups/shelly-voltage/upgrades/SOURCE_NAME \
  --signed-snapshot /var/lib/shelly-voltage-updater/snapshots/SIGNED_NAME \
  --jsonl
~~~

Add --verify-signature to verify the old pinned OCI digest with Cosign,
using the original GitHub Actions image workflow identity and GitHub
Actions OIDC issuer. The signature option uses the network and is still
read-only. Missing, rejected or timed-out signatures fail closed.

The script checks owner-owned 0700 directories, receipt/override mode
0600, no symlink or hardlink substitution, two matching archived source
version markers, manifest old/new versions, an exact old signed digest,
a matching Compose image and signed snapshot target, and SHA256 sidecars
for the source and both SQLite archives.

- PASS_PAIR_IDENTITY_ONLY: all local identities and SHA256 checks match,
  but signature_verified=false.
- PASS_PAIR_SIGNED_IDENTITY_ONLY: same plus a successful Cosign check of
  the old immutable digest, with signature_verified=true.
- BLOCKED: stable reason and exit status 2. Either PASS returns 0.

Every status retains release_authorized=false,
rollback_authorized=false, sqlite_restore_verified=false and
production_modified=false. A signature is necessary but not sufficient
evidence for a version-paired rollback.

## Actual read-only historical verification, 2026-11-18

Historical source snapshot 20261118-035302-0.5.24-hw-fRiBTO7o
and old protected signed snapshot 20261118T015250Z-0.5.25-hw
agree on prior release 0.5.24-hw and target 0.5.25-hw.
SHA256 checks passed and Cosign verified the old immutable digest.

The separate isolated SQLite restorations passed but contain
**90064 and 90063 measurements**, respectively: these are not identical
DB states. Never silently replace newer live measurements with either
old snapshot, even when signatures and checksums pass.

## Still missing before any real rollback or signed upgrade

1. Specific owner approval for the exact source, signed release and
   production activation/rollback transaction.
2. A verified old OCI image and safe staging; do not assume the old
   digest is already available in the local Docker cache.
3. A fresh stopped-writer complete-history rescue backup and isolated
   restore/parity acceptance before any database replacement.
4. A disposable version-paired rollback rehearsal that restores coherent
   source, protected receipt and Compose override, preserves sole-writer
   fencing, Telegram PHOTO identity and pending send intents, and handles
   restart/power-loss failure.
5. Verified post-restore readiness, fresh Shelly telemetry and explicit
   refusal to publish duplicate Telegram channel messages.

### Networkless tests

~~~bash
python3 -m unittest discover -s tests \
  -p test_signed_rollback_pair_preflight.py -v
scripts/quality.sh
~~~
