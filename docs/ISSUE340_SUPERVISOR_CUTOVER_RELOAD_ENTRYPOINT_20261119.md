# Issue 340: audit the legacy ExecReload boot-owner bypass

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA plus read-only live audit, no production changes.

## Problem

The production shelly-voltage-compose.service has three entrypoints:
- ExecStartPre applies the legacy apply-update.sh --recover hook;
- ExecStart runs the legacy start-compose.sh;
- ExecReload ALSO runs the legacy start-compose.sh.

Even if Docker restartPolicy is changed to no and recovery is
removed from a future source unit, the loaded ExecReload could
start a competing legacy Compose writer. Reload is therefore a
separate path to uncoordinated startup and SQLite/WAL loss.

The earlier read-only audit collected and compared ExecStartPre,
ExecStart and ExecStop but ignored ExecReload. It could not detect
a differing effective reload command or report this bypass in the
strict supervisor-cutover mode.

## QA-only implementation

Read-only boot-ownership-audit.py now:
- includes ExecReload in the static unit parser, the live systemd
  property set and the effective loaded-command comparison;
- rejects unexpected static reload commands and unexpected
  effective reload commands with stable reason codes;
- in --for-supervisor-cutover mode, refuses ANY retained legacy
  reload entrypoint (from installed text OR effective systemd)
  with CUTOVER_LEGACY_RELOAD_OWNER_STILL_ACTIVE;
- retains the existing legacy compatibility path for unchanged
  installed units. PASS_STATIC_EVIDENCE_ONLY is not release
  approval, and strict cutover is always separately checked;
- never invokes systemctl reload, starts Docker, accesses secrets
  or alters SQLite, systemd, Docker, Telegram or release assets.

## Evidence

- Focused boot audit and dual-owner source tests: 53/53 PASS,
  Ruff0.14.2, project quality and maintainability gates PASS.
- Fake effective systemd fixtures and unit file tests cover
  unexpected reload path, missing source reload with still-loaded
  legacy ExecReload, and restart=no despite unsafe legacy routes.
- New strict audit code streamed via stdin and executed inside
  LIVE production LXC110 READ-ONLY, expected exit 2, BLOCKED.
  It reported six concrete reasons:
  BOOT_DOCKER_RESTART_OWNER_UNFENCED,
  CUTOVER_DOCKER_RESTART_NOT_DISABLED,
  CUTOVER_LEGACY_AUTO_RECOVER_UNSAFE,
  CUTOVER_LEGACY_RELOAD_OWNER_STILL_ACTIVE,
  CUTOVER_ONESHOT_COMPOSE_OWNER_STILL_ACTIVE,
  CUTOVER_SUPERVISOR_NOT_EFFECTIVE.
- Production remained healthy, restart count 0, /ready HTTP200;
  the live service, update hooks and database were not touched.

## Release remains blocked

A real new signed source TAR, source Sigstore bundle, signed OCI
index with the same Git SHA and a trusted deployment receipt are
still absent. The production cutover needs one atomic owner
handoff retiring old ExecStartPre, ExecStart, ExecReload and
Docker unless-stopped, while preserving the newest full SQLite/WAL,
PHOTO and pending Telegram operations. QA tests are not release
authorization.

## Read-only command

    python3 scripts/boot-ownership-audit.py \
      --installed-unit /etc/systemd/system/shelly-voltage-compose.service \
      --source-unit /etc/systemd/system/shelly-voltage-compose.service \
      --pending-marker /var/lib/shelly-voltage-updater/pending-update \
      --unit-name shelly-voltage-compose.service \
      --container shelly-voltage-bot \
      --for-supervisor-cutover --jsonl
