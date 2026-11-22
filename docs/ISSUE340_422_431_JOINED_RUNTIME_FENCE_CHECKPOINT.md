# Issue 340 + 422 + 431: joined source-only runtime checkpoint

Date: 2026-11-19. Status: QA ONLY / NOT OWNER APPROVED / NOT DEPLOYED.

## Source lineage

- Base public main: 72fb94f8501fb5b0146881a74e059bb84888ca37.
- Joined Draft PR424: bbfec60d9d4b260efa366d4bdbd7d2c14dfe7021.
- Joined Draft PR426: b7ee5b20b51f4e1d1ed0563dc341ee4ac01f463d.
- Joined Draft PR432: f5f53bd58d838e065a827f29c43a0868b274a8b0.
- Isolated host tree: /root/svt-joined-fence-20261119.
- No GitHub push, PR merge, tagged release or production installation.

## This increment

- app.py now acquires an exclusive Linux writer-lifetime lease
  before constructing and running Monitor, therefore before schema
  initialization, polling and Telegram threads.
- Actual Storage.connect() obtains the same filesystem transaction
  gate for ordinary SQLite reads/writes, checks pending-update
  after acquiring it, releases gate only after SQLite closes.
- writer_fence.py rejects duplicate owner, busy gates and unsafe
  symbolic or multiple-linked pending marker.
- Source release and rollback archive gates now require the new
  Python runtime dependency.
- Independent process and SQLite fixture tests check lock ownership,
  pending publication, symlink refusal and SIGKILL release.

## Verified in isolated LXC114

- Combined 117/117 selected tests PASS in a networkless,
  secretless, nonroot, read-only Docker container.
- Ruff 0.14.2 formatting/lint and maintainability PASS.
- Public source staging PASS with SHA256SUMS and FILES.SHA256.
- A generated flat rollback-source fixture passes validation.
- Python subprocess SIGKILL PASS. This is NOT a full real
  updater/systemd/Docker daemon SIGKILL or actual guest reboot.
- The QA archive retains baseline VERSION 0.5.25-hw and
  is NOT a signed/public replacement of that published release.

## Verified live, read-only

- LXC110 remains on signed v0.5.25-hw with healthy Docker,
  zero restarts, /ready 200, all 3 phases collecting.
- SQLite quick_check OK, WAL mode, zero open outages,
  zero synthetic 0 V samples, zero pending Telegram sends.
- Daily backup 2026-11-19 03:17 verified by SHA256 and
  decompressed SQLite quick_check; L1/L2/L3, saved pro3em
  and Telegram PHOTO identity retained.
- No live service restart, OTA, SQLite replacement,
  alert threshold change or Telegram public message action.

## Still BLOCKED: do not deploy this source

- Actual host updater does NOT yet share the new
  .svt-transaction.lock / .svt-update-pending protocol;
  its existing private pending path is distinct.
- Docker restart policy remains independently unless-stopped.
  A positive health-based systemd supervisor is still required.
- Installed startup --recover diverges from tracked GitHub unit.
  Real recover can overwrite newer SQLite WAL measurements.
- Exact-version signed OCI/source, current WAL+PHOTO rescue,
  send-intent reconciliation, second-writer race, Docker
  SIGKILL and real guest reboot all require joint acceptance.
- Exact-head Owner Approval remains required for release.

## Next safe stages

1. Implement real root-owned pending publication and clearance
   under the common transaction inode and visible data mount.
2. Fence updater stop and confirmed owner release, rescue newest
   full SQLite/WAL+PHOTO history and verify signed source/image.
3. Replace independent Docker restart only with a tested
   systemd watchdog and the complete update/recovery protocol.
4. Validate real LXC114 Docker restart/reboot A/B, full CI and
   signed release evidence before requesting production approval.

## 2026-11-19 next stage: shared updater marker primitive

- Added update_gate(path) to writer_fence.py; unlike a normal
  Storage connection it can inspect an already published marker
  while holding exactly the same transaction lock inode.
- scripts/writer-fence-marker.py provides publish/clear/status,
  bounded token syntax and stable exit codes. A new marker is
  fsync'd and linked without overwriting another transaction;
  the containing directory is synced. Existing marker/symlink/
  hardlink conflicts refuse changes. Clearing requires exact token.
- The CLI is a QA-only source primitive. The production updater
  and its private pending-update token are NOT wired to this CLI.
  No release authorization, live source change or OTA follows.
- Isolated LXC114 4/4 new marker CLI tests PASS; with all joined
  tests selected, 121/121 PASS. Ruff0.14.2 and maintainability
  PASS. No network, credentials or live SQLite mounts were used.
- NEXT: integrate this marker atomically into actual apply-update
  and rollback flows alongside a sole supervisor and latest WAL/
  PHOTO rescue; reject incomplete change and demand separate
  exact-head Owner Approval. Do not activate this partial code.
