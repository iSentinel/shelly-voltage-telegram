# Issue340: sole Docker restart owner — source-only QA

Status: QA ONLY. Production LXC110 unchanged; no release approval.

scripts/supervised-container.py is a prospective single restart owner
for one already-created Docker container. It refuses independent Docker
restart policies, a mismatched root-owned fence mount, pending updates,
and an image not matching the root-private deployment digest receipt.
The QA mode requires a container named svt-qa-* and Docker network none.

The candidate is not an installer or a complete update transaction:
it cannot verify the source tarball/Cosign signature itself, activate
a new image, reconcile an uncertain Telegram send intent or restore the
full most-recent SQLite WAL database after interrupted rollback. The
production systemd unit, Compose restart policy, updater and rollback
are not yet switched to this candidate.

Actual LXC114 isolated QA, using only an existing signed local test
image, a synthetic database under /var/tmp, root-owned lock directory,
nonroot UID 10001 and no network or credentials:

- Independent Docker restart unless-stopped refused before start.
- An isolated real systemd Type=simple unit started the QA writer.
- Actual Docker SIGKILL killed the writer; the supervising unit
  started a new PID without Docker restart ownership.
- A root-published pending marker blocked further SQLite writes.
  The supervisor stopped without restarting; history stayed unchanged.
- After exact marker clear, systemd restarted and L1/L2/L3 grew.
- A real systemctl restart docker.service was performed in LXC114
  while pending was active and writer stopped; Docker restart:no
  preserved the stopped state and SQLite row count.
- After pending clear, systemd started the sole writer and triphase
  history increased again. Disposable systemd unit/container cleaned.

Next: actual LXC114 guest reboot with pending and without pending,
including positive supervised availability. Integrate full signed
release preflight/rollback and newest WAL+PHOTO rescue before Owner
Approval or any change to the production Compose/systemd units.

## Actual guest reboot acceptance (2026-11-19)

LXC114 only: hostname shelly-docker-reboot-qa, unprivileged=1,
onboot=0, own 4-GiB rootfs, no guest network or production mounts.
Temporary systemd sole-owner service was explicitly enabled for QA
and later stopped, disabled, deleted and daemon-reloaded.

Boot with pending:
- Before boot ID: ed846bb0-25fc-4349-b2a3-24c20f818a13.
- After boot ID: f92b0c42-988b-4b7d-a85c-9f63ab7b3b9d.
- Root-published pending marker remained durable across guest reboot.
- Systemd candidate unit inactive, Docker daemon active,
  QA container created/stopped with restart=no.
- 3 rows persisted, exactly L1/L2/L3 once, SQLite quick_check OK.
- No autonomous Docker or systemd SQLite writer started.

Clear and positive boot:
- Exact token marker clear PASS, supervised systemd start PASS;
  triphase count increased from 3 to 6.
- Subsequent reboot changed guest boot ID again to
  4376912b-7551-4d9e-ad56-dacad51796c3.
- Pending absent; enabled systemd service automatically active;
  QA Docker container running with restart=no.
- SQLite quick_check OK, total 267 rows (89 each L1/L2/L3),
  saved pro3em and original Telegram PHOTO marker still present.
- No Telegram calls, actual Shelly polling or live production writes.
- Final cleanup: temporary QA unit and container removed, pending
  absent, Docker service healthy. Root-only shared test fence
  directory retained for later offline QA.

Remaining release blockers:
- Source Compose/systemd currently still have the production
  independent restart configuration, not this QA candidate.
- Actual signed updater, rollback and source release still lack
  one joined pending/lease/newest-history transaction.
- Supervisor currently checks image-to-receipt digest but does
  NOT independently verify Cosign provenance or source archive.
- No production activation, GitHub merge, release tag or OTA.
