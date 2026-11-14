# Source reconciliation: offline and partial-install recovery gate

This is a **read-only** audit, not a recovery executor. Never use it to
replace the current SQLite database or disable signed-image verification.

The owner-maintained LXC110 had a signed v0.5.21-hw container with older
v0.5.15-hw source files when a physical power outage was reported. The
signed rollback guard rightly rejects restoring v0.5.15-hw source and
its older SQLite into the running signed v0.5.21-hw image.

During a Shelly outage, Docker Healthy and /health=200 do not imply
meter readiness; /ready=503, a stale sample, or an open incident must
block source reconciliation. Restarting the container cannot restore
mains electricity.

## Safe read-only check

Run only with the already verified signed release source. The command
does not stop Docker, read Telegram credentials, modify messages or
write source/SQLite files.

    python3 scripts/source-reconcile-recovery-preflight.py \
      --source /root/shelly-v0.5.21-hw-verified-20261114/shelly-voltage-telegram-0.5.21-hw

The preflight checks protected updater identity against the real OCI
image, source version markers, read-only SQLite integrity/history and
fixed localhost /health and /ready probes. It deliberately **never**
sets source_install_authorized or public_photo_authorized to true.

## Recovery design: forward, not historical full rollback

For any future, separately approved maintenance attempt, first
fence and stop the writer and create a fresh source+full SQLite archive
with exact parity and validated recovery. If source installation fails
partway through, keep the writer fenced and preserve the current SQLite.
Reapply the same signed source release that matches the immutable
OCI digest, not older source and not a local Docker build.
Require a separate end-to-end service, source/image parity and boot
recovery verification before considering PHOTO conversion.

This document and audit do not implement a production recovery action,
authorize a source install, or remove the requirement for a tested
stopped-writer recovery procedure. A historical backup containing old
SQLite is forensic evidence, not the latest live database.
