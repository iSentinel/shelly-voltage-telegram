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

## Disposable fault-injection rehearsal

An additional source-only tool rehearses **fictional** rollback recovery.
The CLI validates the real saved source/OCI pair first, with optional
Cosign verification. It then generates completely synthetic SQLite
databases in 0700 temporary directories:

- ten newer measurements plus a synthetic Telegram PHOTO message ID;
- six historical measurements, which must **not** replace newer history;
- an online SQLite rescue of all ten newest measurements;
- private synthetic deployed.json/override.yaml/source VERSION records.

It injects an exception at **three** checkpoints: after the rescue,
after the old pair is staged, and after the old (synthetic) SQLite
database is swapped in. At each point, simulated recovery validates
the rescue **before** replacing the temporary database, restores the
newer records and PHOTO marker, and returns to consistent fictional
source/receipt/Compose version state. An unfenced simulated writer,
corrupt/missing rescue, forged old receipt or mismatched image pin
blocks the rehearsal; failure to verify an old signature blocks before
creating fixtures.

Run it without a network operation:

~~~bash
python3 scripts/signed-rollback-disposable-rehearsal.py \
  --source-snapshot /var/backups/shelly-voltage/upgrades/SOURCE_NAME \
  --signed-snapshot /var/lib/shelly-voltage-updater/snapshots/SIGNED_NAME \
  --jsonl
~~~

To recheck the **old** OCI signature using Cosign first, add
--verify-signature. The tool does not pull, start, stop or replace a
Docker image. It makes no Telegram API calls, changes no live SQLite,
does not touch the live systemd/Compose state and removes synthetic
fixtures upon completion. It always returns
real_power_loss_tested=false, production_modified=false and
rollback_authorized=false.

**Limitations:** Exception injection is not a real SIGKILL, filesystem
crash, reboot or power failure. This is *not* an end-to-end signed OCI
rollback or a real sole-writer fencing implementation. It stages only
the already-verified version markers and prior immutable receipt/override;
it does not extract, install or run old application source. The actual
production guard still prevents an old source-only downgrade.

Further required acceptance: isolated, durable crash/restart testing,
correct stopped-writer and update-recovery fencing, a fresh
writer-fenced full-history production rescue at release time, signature
and source/image validation before a live start, and explicit owner
authorization. No automatic DB replacement or public Telegram edit.
