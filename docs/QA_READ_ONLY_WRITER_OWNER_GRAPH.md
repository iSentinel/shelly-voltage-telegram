# Read-only writer-start owner graph audit

**QA only — issue #340. This is not permission to maintain production.**

## Scope

The previous disposable writer-fence auditor checks Docker,
Compose's unit activity, boot prestart recovery, HTTP readiness
and the release-check timer. It does not enumerate every direct
path that could start, recreate or restore the writer.

This additive static auditor reads **only twelve fixed public
source/unit files** at known locations; it does not inspect
secrets, SQLite, private state, Telegram or process memory. The
default invocation is inert and performs no file reads.

The fixed code paths are:
- /opt/shelly-voltage/compose.yaml
- scripts/apply-update.sh, scripts/start-compose.sh,
  scripts/install.sh and scripts/rollback.sh
- /etc/systemd/system/shelly-voltage-compose.service
- manually invoked shelly-voltage-updater-apply@.service
- shelly-voltage-update-check.service,
  shelly-voltage-backup.service, shelly-yasno-refresh.service
- shelly-graphic-emergency-boot.service
- /usr/local/sbin/shelly-graphic-emergency-restore

Each file must be regular, not a symlink and at most 128 KiB.
Absent or unexpectedly altered known entry points are reported
UNVERIFIED, never silently treated as safe. Nothing is
executed from any inspected file.

## Classification: fail-closed

On the inspected installed runtime, the audit reports BLOCKED
with twelve findings:
- boot ExecStartPre invokes recovery through existing A mutex
- Compose ExecStart and **ExecReload** can start writer directly
- Compose ExecStop participates in writer stop/maintenance
- Docker/Compose restart unless-stopped bypasses pending at boot
- manual updater takes existing A only; B not globally adopted
- start-compose, installer and rollback can directly start writer
- conditional emergency boot restore and its implementation can
  restore unless-stopped policy and restart Compose

Read-only tasks are classified separately: update-check metadata,
online SQLite backup and YASNO forecast refresh. If those units
are changed, that benign classification is revoked and the
tool reports BEHAVIOR_UNVERIFIED.

**This static audit is not a proof that no other startup path
exists.** A successful textual search could be a misleading
comment, and unknown new files or external operators might exist.
Its result can be BLOCKED or REVIEW_REQUIRED, *never*
SAFE_TO_DEPLOY or maintenance authorization. Independent runtime
restart-owner fencing and owner approval remain mandatory.

## Usage

Default safe no-op:

    python3 scripts/writer-start-owner-graph-audit.py

Read only twelve public fixed paths on the target host:

    python3 scripts/writer-start-owner-graph-audit.py \
      --live-read-only --jsonl

-h/--help are supported. Exit code 0 is used **only** for
inert dry-run, 2 for a read-only BLOCKED graph, and 3 for a
read-only REVIEW_REQUIRED graph that *still grants no approval*.
It never starts/stops Docker or systemd, acquires updater locks,
copies/restores SQLite, modifies Telegram or writes state.

Run the fixed-file audit alongside writer-restart-fence-audit.py;
these tools have complementary static and operational scopes.

## Next validation gate

The proposed noncircular hierarchy uses two **distinct lock
inodes**. A reviewer must assess all direct owner paths including
Compose ExecReload, install.sh, rollback.sh and conditional
emergency restore before designing production B/A fencing.
PR #352 isolated Bash recover_pending() recovery under
B EX + A EX with an argument-validated Compose stop stub,
but it did not run a real production signed updater --version
transaction, real Compose reload, or physical host restart.

Production source 0.5.15-hw versus signed 0.5.21-hw runtime,
fresh writer-fenced whole-current SQLite + signed-source A/B,
owner maintenance authorization, independent physical-power-loss
durability and public Telegram pinned TEXT-to-PHOTO acceptance
are all separate release NO_GO conditions.
