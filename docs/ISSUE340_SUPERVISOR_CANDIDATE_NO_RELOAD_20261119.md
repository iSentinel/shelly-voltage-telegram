# Issue 340: candidate supervisor cannot reintroduce legacy reload

Date: 2026-11-19, Europe/Kyiv
Scope: source-only QA, never install candidate as a systemd service.

The live legacy oneshot Compose service uses ExecReload to run
start-compose.sh again, in addition to its unsafe --recover prestart.
The new strict boot-owner audit explicitly reports this legacy
reload path as CUTOVER_LEGACY_RELOAD_OWNER_STILL_ACTIVE.

The future candidate is separate from production and intentionally
named .unit so it is not directly installable. New source policy tests
enforce that the candidate:
- has no ExecReload or RemainAfterExit=yes;
- uses exactly one ExecStart and that command invokes the
  supervised-container.py gate, not raw Docker Compose;
- has exactly two ExecStartPre commands, with Compose create-only;
- declares Conflicts=shelly-voltage-compose.service;
- never runs apply-update.sh, --recover, or docker compose up.

Actual isolated QA evidence:
- The strict cutover and candidate policy suite: 51/51 PASS.
- Ruff 0.14.2 and project quality/maintainability gate PASS.
- systemd-analyze verify PASS on a temporary copy of the
  candidate renamed to .service inside isolated LXC114. That
  file was never installed, enabled or loaded into systemd.
- The candidate text had two ExecStartPre, one ExecStart,
  no ExecReload and no RemainAfterExit=yes.
- QA Docker idle and no production systemd/Docker/SQLite changes.

These are source-configuration checks, not a real owner cutover.
Actual new signed source/OCI artifacts, a trusted deployment receipt,
safe retirement of the old Compose unit and Docker restart policy,
and real cross-version WAL/PHOTO/Telegram rollback acceptance remain
required before any production activation.
