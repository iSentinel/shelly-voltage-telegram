# Issue 340: strict read-only supervisor cutover audit

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA and read-only interrogation of LXC110.

## Gap closed

The existing boot-ownership-audit.py has a compatibility purpose:
it validates that the CURRENT legacy oneshot systemd/Compose unit
has not drifted from the known installed --recover startup hook.
Its PASS_STATIC_EVIDENCE_ONLY result is therefore not a safe
approval to install the future signed single-owner supervisor.

Even with Docker restart=no, the legacy ExecStartPre still runs
apply-update.sh --recover and may rewind the newest SQLite/WAL.
Changing Docker's restart policy alone cannot close that risk.

The new explicit --for-supervisor-cutover audit mode separately
reports the following blockers, without reading credentials,
running update scripts or modifying any unit:
- CUTOVER_LEGACY_AUTO_RECOVER_UNSAFE, if the installed file OR
  effective systemd ExecStartPre includes --recover;
- CUTOVER_ONESHOT_COMPOSE_OWNER_STILL_ACTIVE, if either unit
  representation is still a oneshot;
- CUTOVER_SUPERVISOR_NOT_EFFECTIVE, if the installed AND loaded
  ExecStart are not the future supervised-container.py;
- CUTOVER_DOCKER_RESTART_NOT_DISABLED, unless restart policy=no.

The existing BOOT_DOCKER_RESTART_OWNER_UNFENCED reason is retained.
No static result in this audit authorizes installation or rollback:
supervisor_cutover_authorized=false, source_install_authorized=false,
rollback_authorized=false and production_modified=false.

## Evidence

- Ruff0.14.2 formatting, lint and project quality gate PASS.
- Boot ownership audit unit tests 38/38 PASS including:
  restart=no does NOT hide legacy auto-recovery or oneshot owner,
  loaded-vs-file startup drift is independently blocked, and
  independent Docker restart remains blocked.
- Ran this new script from source via stdin inside LIVE LXC110,
  without installing the audit or touching the service.
  Expected exit=2, status=BLOCKED, all five codes present:
  BOOT_DOCKER_RESTART_OWNER_UNFENCED,
  CUTOVER_DOCKER_RESTART_NOT_DISABLED,
  CUTOVER_LEGACY_AUTO_RECOVER_UNSAFE,
  CUTOVER_ONESHOT_COMPOSE_OWNER_STILL_ACTIVE,
  CUTOVER_SUPERVISOR_NOT_EFFECTIVE.
  No production changes; Telegram, Docker health and SQLite
  monitoring remain unchanged.
- Isolated LXC114 QA had no running Docker containers and no
  pending updater marker after the test.

## Still required

This is a read-only negative test, not a mechanism to retire the
old unit. Production cutover requires actual verified new signed
source+OCI pair, authenticated deploy receipt and WAL/PHOTO/Telegram
history continuity. Only after staged source and image acceptance
may the old legacy service and autonomous Docker restarts be safely
retired in one coordinated, separately authorized transaction.

## Example audit invocation

    python3 scripts/boot-ownership-audit.py \
      --installed-unit /etc/systemd/system/shelly-voltage-compose.service \
      --source-unit /etc/systemd/system/shelly-voltage-compose.service \
      --pending-marker /var/lib/shelly-voltage-updater/pending-update \
      --unit-name shelly-voltage-compose.service \
      --container shelly-voltage-bot \
      --for-supervisor-cutover --jsonl

Only inspect; do not invoke legacy --recover or restart production.
