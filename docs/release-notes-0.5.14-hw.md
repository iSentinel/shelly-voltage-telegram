# Shelly Voltage Telegram 0.5.14-hw — release candidate

**Status: finalization staging, unpublished and not deployed.** This
unmerged review branch aligns VERSION and RELEASE_VERSION to 0.5.14-hw,
but the latest publicly released version remains 0.5.12-hw. Installed
production remains 0.5.13-hw. Do not claim Shelly is online or mains
electricity is present. No public tag or release has been created.

## Changes

- Preserve current SQLite history and the stable Telegram status through
  stopped-writer source+SQLite snapshots, archive verification, and rollback.
- Reject unsafe application roots, protected files and fresh installs that
  would reuse existing persistent SQLite without a migration.
- Keep five actually restorable snapshot pairs; ignore and preserve incomplete
  or corrupt folders rather than deleting genuine recovery history.
- Mark current HTTP voltage/frequency unknown while Shelly is offline,
  retaining historical measurements separately.
- Require a fresh valid *same-phase* sample since the captured outage
  baseline before declaring recovery complete.
- Prepare optional guarded YASNO forecast reader with protected cache,
  no additional Shelly control and no automatic Telegram sends.

## Blocking release and deployment acceptance

1. Independently review the complete stacked source and integrated diff.
2. Authorize a maintenance window and stop the SQLite writer safely.
3. Capture and verify a complete, exact-history source + SQLite snapshot;
   independently restore it without any Shelly or Telegram network calls.
4. Provision protected YASNO producer and read-only reader if enabled.
   Otherwise YASNO remains disabled and must not weaken the bot.
5. Wait for actual physical Shelly recovery and fresh L1 readings, then
   perform a read-only 15-minute stable-recovery verification.
6. Approve real Telegram client message continuity and the rollback path.
7. Authorize publication of the v0.5.14-hw tag and production upgrade.
   Creating the tag triggers public GitHub release/image workflows.

## Preview command on target (never modifies production)

    SVT_ROOT=/opt/shelly-voltage bash scripts/install.sh --dry-run --non-interactive

## Pinned installer preview after public release assets exist

    REPO=iSentinel/shelly-voltage-telegram VERSION=0.5.14-hw TAG=v0.5.14-hw bash scripts/install-release.sh --dry-run --non-interactive

## Actual install, ONLY after all gates are approved

    sudo env REPO=iSentinel/shelly-voltage-telegram VERSION=0.5.14-hw TAG=v0.5.14-hw bash scripts/install-release.sh --non-interactive

Nightly archives are historical and are not proof of exact-history parity.
Never rebuild or stop the active production container during the blackout
without an approved maintenance window, verified restore and rollback.
