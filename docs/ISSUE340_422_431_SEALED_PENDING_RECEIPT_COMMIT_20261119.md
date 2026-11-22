# Issues 340/422/431: sealed pending-marker receipt commit

Date: 2026-11-19, Europe/Kyiv
Scope: source-only isolated QA, never a production cutover.

## Race closed in QA

Previously the newest full-history WAL/PHOTO rescue and root-owned
pending marker were inspected after source+OCI verification, but the
pending check ended before the paired deployment receipt was written.
Another updater could release pending during that narrow interval.

The QA signed-pair stager now places its final receipt write and fsync
within TWO shared database synchronization locks:
- the exclusive quiescent writer-owner lease, which requires that the
  sole writer is stopped and prevents a cooperating writer from
  acquiring ownership;
- the shared transaction/update gate, which excludes the cooperating
  marker clear/publish action and all fenced SQLite transactions.

While holding both locks, the stager reopens the root-owned,
single-link, bounded pending-marker without following symlinks,
checks the exact transaction token, and recomputes complete
live-versus-rescue SQLite logical history. Only then can the final
private deployed.json receipt be atomically linked and fsynced.
The environment fence setting is restored on return, including errors.

This is a QA-only receipt writer and does not remove the update
marker, switch images, grant release permission, or replace a database.

## Actual testing

- Targeted signed-pair stage tests 14/14 PASS, with ResourceWarning
  treated as an error; Ruff0.14.2 and project quality gate PASS.
- Real LXC114 Docker stopped-writer/protected-fence integration PASS.
  Four separate process SIGKILL points during the WAL rescue phase
  preserved pending and history; complete live/rescue L1/L2/L3
  contents and PHOTO identity matched. Stubbed POSITIVE source/OCI
  signature evidence was explicitly mocked (no real signed release).
- A second actual subprocess attempted the real writer-fence-marker.py
  clear operation while the paired receipt held BOTH shared locks.
  It remained blocked, was terminated as part of the disposable
  fixture, and the expected pending marker stayed intact.
- After normal test-only marker clear, the same disconnected QA image
  resumed writing, preserving old L1/L2/L3 history and PHOTO.
- All ephemeral QA containers, pending files and paired receipts
  were cleaned. No live SQLite, Telegram, Docker or systemd changes.

## What remains

Actual new signed source+OCI bytes and matching immutable manifest
are not available. The existing signed production v0.5.25-hw OCI
baseline is verified, but its public source archive lacks the new
Sigstore blob signature. This QA process cannot certify any real
new image transition or rollback; production keeps its existing
legacy --recover and Docker restart policy.

Need a production cutover transaction with sole systemd ownership,
exact old/new signed source/OCI pairing, full history/WAL/PHOTO and
pending Telegram-intent parity, mutually exclusive update/boot
authority and forced-interruption/reboot acceptance. The current
candidate unit is deliberately not installed.
