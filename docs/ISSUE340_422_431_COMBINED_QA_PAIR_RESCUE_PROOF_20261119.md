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

## Actual LXC114 guest reboot with joined pending/OCI/source/WAL fixture

Date: 2026-11-19. The reusable isolated
tests/qa_paired_rescue_reboot.sh supports --help and
prepare|verify|cleanup; all actions require the exact disposable
LXC114 hostname and root. The fixture has no network, real
Shelly access, Telegram credentials or production volumes.

- prepare created a complete synthetic three-phase SQLite with
  saved pro3em/PHOTO identity and a networkless UID10001 writer.
  The guarded QA stage published root-owned pending, stopped the
  writer, captured its newest WAL, and validated a private old/new
  OCI/source identity-only fixture. Docker was restart:no.
- Proxmox pct reboot 114 restarted only the QA guest. Guest boot ID
  changed from 4376912b-7551-4d9e-ad56-dacad51796c3 to
  48bb2a6f-7161-4f01-af7b-bc78559d6bc3.
- After guest reboot, root pending remained durable, Docker stayed
  stopped with restart:no, source/OCI identity-only QA preflight
  still passed and all 6 committed L1/L2/L3 measurements and
  PHOTO identity survived without any added rows.
- Exact token clear followed by sole-supervisor startup PASS.
  SQLite history grew from 6 to 9 (a complete three-phase cycle)
  with quick_check OK and saved PHOTO preserved.
- The test cleaned the QA container, root pending, private pair
  fixture, temporary backup and source DB. Docker daemon remained
  active; LXC110 was not rebooted.
- Post-reboot quality gate PASS (Ruff0.14.2), 34 focused update
  and rollback compatibility tests PASS. Source-only QA release
  archive SHA256SUMS verified and the preflight and both reusable
  QA runtime scripts were included. Nothing was published/signed.

This is not a production updater SIGKILL test and does not prove
Cosign cryptographic provenance, exact new OCI/source signature,
rollback of a live database, or Python3.11/3.12 CI. Those remain
required prior to a separately approved production cutover.

Example QA setup and acceptance commands (LXC114 only):

    bash tests/qa_paired_rescue_reboot.sh --help
    bash tests/qa_paired_rescue_reboot.sh prepare
    # Reboot only disposable LXC114 from the Proxmox QA host.
    bash tests/qa_paired_rescue_reboot.sh verify
    bash tests/qa_paired_rescue_reboot.sh cleanup
