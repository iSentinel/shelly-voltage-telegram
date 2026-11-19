# Issues 340/422/431: combined rollback identity and latest WAL proof

Date: 2026-11-19. State: QA ONLY, SOURCE ONLY, NO DEPLOYMENT.

## Why the combined preflight is needed

The old signed source/OCI pair and the newest SQLite/WAL/PHOTO rescue
were verified by separate candidate tools. A future new updater
cannot safely activate or roll back unless *both* are coherent at
the same pending-update boundary and signed release versions match.

Source-only scripts/qa-paired-rescue-preflight.py combines read-only
checks; it is deliberately incapable of performing an update,
rollback, Docker start/stop, signature verification, Telegram action
or database restore.

The command requires --qa-only and paths under dedicated
/var/lib/svt-qa-* trees, as well as the existing guarded stage
arguments for the disposable container, database, fence, output and
exact pending token.

- Stage preflight revalidates a stopped networkless nonroot Docker
  container, the root-owned pending marker and lifetime lock,
  the private rescue SHA256 and complete SQLite logical parity
  including phases L1/L2/L3, pro3em identity and PHOTO.
- Old pair preflight checks source/archive manifest versions,
  checksummed source/TAR safety and old OCI receipt/digest. The old
  image signature is NOT verified in this offline QA mode.
- The new root-private deployed receipt must match the new source
  RELEASE_VERSION. This must match the old pair target version.
- Output is evidence-only, with explicit false old/new signature
  verified, release_authorized, rollback_authorized and
  production_modified fields.

## Real isolated LXC114 acceptance

The repeatable tests/qa_paired_rescue_runtime.sh runs only with
the exact disposable LXC114 hostname and root. It has --help,
requires a Docker image already cached locally, disables the
synthetic writer's network, and cleans temporary resources and
pending markers after execution.

The fixture helper tests/qa_paired_rescue_fixture.py produces
root-private, synthetic old/new versions/digests only at its exact
/var/lib/svt-qa-paired-proof-20261119 location; these are not
real cryptographic signatures or valid release approval artifacts.

Accepted checks:
- Docker restart:unless-stopped refused; production DB path refused.
- Protected pending/owner stopped writer and preserved newest
  L1/L2/L3/WAL+PHOTO in a full SQLite rescue with matching SHA256.
- Missing receipt, duplicate receipt, changed L3 values and forged
  rescue hash blocked; exact QA receipt reconstruction succeeded.
- Combined old/new source/OCI identity plus staged full-history
  inspection passed; wrong new source target version was rejected.
- Tampered newest L3 rescue was rejected while pending remained,
  then restored in disposable QA; combined preflight passed again.
- Temporary QA Docker container and pair fixture removed; root
  marker absent after cleanup. No production writes or networking.

Source-only QA tests: 6/6 combined preflight unit tests PASS,
195/195 selected integrated tests PASS on isolated Python 3.13
runtime, Ruff0.14.2 and maintainability checks PASS.

## Still blocking release

No real Cosign verification of the old/new OCI/source pair has been
performed by this QA-only tool. No exact new signed OCI image or
source archive has been activated. The production updater and
rollback are not wired to the new root-owned owner/pending gates.
Real interruption across the signed updater transaction, newest
WAL/PHOTO post-reboot recovery, and exact-head Python 3.11/3.12/3.13
CI remain outstanding. Python3.11/3.12 are not installed on QA114.

Do not publish or deploy partial code based on identity-only checks.
Obtain separate Owner Approval after complete integrated acceptance.

## Reproducible commands

Dry-run the protected stage against an existing disposable QA setup:

    python3 scripts/stage-guarded-update.py --qa-only --dry-run \
      --container svt-qa-example \
      --database /var/tmp/svt-qa-example/history.sqlite3 \
      --fence-dir /var/lib/svt-qa-example-fence \
      --output /var/lib/svt-qa-example-rescue/newest.sqlite3 \
      --token qa-example-1

Run the complete self-cleaning QA acceptance inside LXC114 only:

    bash tests/qa_paired_rescue_runtime.sh --help
    bash tests/qa_paired_rescue_runtime.sh
