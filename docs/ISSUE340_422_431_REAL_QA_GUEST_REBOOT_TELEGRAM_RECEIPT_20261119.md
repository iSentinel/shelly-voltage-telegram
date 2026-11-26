# Issues 340/422/431: full WAL and Telegram intents after QA guest reboot

Date: 2026-11-19, Europe/Kyiv.
Scope: two real reboots of disconnected LXC114, not production.

## What was added

The existing root-only persistent reboot test now uses the
real Storage.reserve_text_post() and telegram_status_cleanup
table to seed one uncertain Telegram send and a queued old
message unpin/delete operation, alongside the PHOTO status
identity, message ID and L1/L2/L3 history.

Every pre- and post-reboot count check now additionally requires:
- the exact PHOTO media identity and stored status message ID;
- a persistent legacy uncertain send intent (send: token);
- the exact telegram_status_cleanup row, with chat, message ID,
  needs_unpin=1 and retry_at=1300.0.

The fault-injected rescue path now records the original guest
boot_id and requires a different boot_id during verify-fault.
Thus neither normal nor fault verification can pass without
an actual guest reboot.

## Actual isolated acceptance evidence

Scenario A:
1. Prepare root-owned pending and full SQLite/WAL/PHOTO rescue,
   with stopped restart:no UID10001 offline writer.
2. Reboot the disposable LXC114 guest using pct reboot 114
   from the Proxmox host, leaving LXC110 untouched.
3. Verify new boot_id, stopped Docker writer, retained pending
   marker and full history, including Telegram send and cleanup.
4. Clear only the exact marker token, start offline QA supervisor,
   confirm measurements resume: 9 -> 12 rows total across
   L1/L2/L3 and Telegram state remains stable.
   Result: PASS.

Scenario B:
1. Inject a controlled updater exception after full WAL backup
   but before a rescue receipt is persisted. Pending remains set.
2. Record original boot_id and reboot LXC114 again.
3. Verify new boot_id, rescue present, receipt absent, stopped
   writer and unchanged SQLite/Telegram state.
4. Reconstruct missing receipt ONLY after exact newest-history
   and SHA256 parity, keep pending until checks finish, then
   explicitly clear the right token and resume the QA writer:
   6 -> 9 total measurements.
   Result: PASS.

Both scenarios cleaned their disposable database, receipt,
pending marker, fixture and Docker containers. An existing
unrelated exited QA Docker container was not modified.

The pinned local runtime was used. Old/new source and OCI
signed pair evidence was synthetic/identity-only, not real
Sigstore verification, and no real signed new version was
deployed or rolled back. No Telegram API calls took place.

## Tooling note

The guest reboot emptied temporary /tmp, including the
non-persistent QA Ruff symlink. The installed root-only
/root/qa-ruff binary was confirmed Ruff 0.14.2, then
temporarily linked under /tmp/ruff for the QA quality gate.
The project quality gate and 11 targeted reboot/image-policy
tests passed; nothing was installed into production.

## Remaining production blockers

Production v0.5.25-hw still has the legacy startup --recover
hook and autonomous Docker restart policy. The candidate
sole-owner unit is not installed. Actual new signed source+OCI
bytes, a durable production receipt writer, and a verified
new-image and real signed rollback with newest SQLite/WAL/
PHOTO/Telegram and sole-owner activation are still required.

## Repeat commands

    bash tests/qa_paired_rescue_reboot.sh --help
    bash tests/qa_paired_rescue_reboot.sh prepare
    bash tests/qa_paired_rescue_reboot.sh verify
    bash tests/qa_paired_rescue_reboot.sh prepare-fault
    bash tests/qa_paired_rescue_reboot.sh verify-fault

Run prepare/verify on opposite sides of a genuine guest reboot.
Never execute the test against production.
