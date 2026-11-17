# Release notes — 0.5.23-hw

## Scope

The proposed release promotes the previously owner-reviewed Issue #402
changes merged into main at c2f87f135e712d7326a9c869571049128f0dd71e.
The monitoring application app.py and Dockerfile remain byte-identical
to the accepted 0.5.22-hw source.

## Changes

- Retry signed Docker deployment-parity health after consecutive fresh
  Shelly readiness responses. A transitional Docker health=starting
  status is no longer an immediate failure; persistent failures are
  still rejected.
- Repair restricted group traversal for the separate YASNO refresh
  account after filtered source unpacking. Validate owner IDs, private
  modes and symlink absence before changing any permissions.
- Add a read-only backup-family inventory reporting integrity and
  historical/current parity separately; it cannot authorize releases.
- Expand source-tar, credential isolation, recovery and archive QA.

## Version / signing

VERSION and RELEASE_VERSION are both promoted to 0.5.23-hw.
Historical LIVE_VERSION remains unchanged. A stable signed image may
be built only from an immutable v0.5.23-hw tag on the exact reviewed
commit after GitHub CI and the release policy tests pass.
No unsigned/manual image is eligible for production.

## Deployment and rollback acceptance

Production requires a healthy Shelly 207–253V, fresh SQLite, exactly
one writer, a complete writer-fenced SQLite backup and disposable
restore parity, signed OCI verification, an unchanged Telegram text
identity, recovered YASNO group 10.1 and an exact rollback reference.
PHOTO remains disabled. Single NVMe loss risk is accepted but
off-host backup has not been demonstrated.

## Example commands

Non-mutating updater dry run:

    bash /opt/shelly-voltage/scripts/apply-update.sh --version 0.5.23-hw --dry-run

Signed deployment (only after official signing and guard checks):

    bash /opt/shelly-voltage/scripts/apply-update.sh --version 0.5.23-hw

Source installer dry run for verified release sources:

    SVT_ROOT=/opt/shelly-voltage bash scripts/install.sh --dry-run --non-interactive

Production source alignment is a separate guarded operation using
verified source assets and a persisted rollback snapshot.
