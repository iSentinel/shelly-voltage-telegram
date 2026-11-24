# Issues 340/422/431: real coordinator SIGKILL during local image change

Date: 2026-11-19, Europe/Kyiv
Scope: isolated, offline, unsigned LXC114 QA only.

## Additional fault injection

Extended tests/qa_offline_image_switch.sh with one opt-in
--qa-crash-recovery mode. The parent test owns the QA-only
cleanup trap. Two independent Bash child coordinators perform:
- forward: publish the root-owned pending token, stop the first
  disposable Docker writer, persist the newest WAL-consistent full
  SQLite/PHOTO/Telegram rescue, then SIGKILL their OWN Bash process;
- reverse: publish the different rollback token, stop the second
  disposable Docker writer, capture a newer SQLite/WAL rescue,
  then SIGKILL their OWN Bash process.

The parent expects exit status 137 in both cases and refuses any
other exit or missing checkpoint evidence. It confirms that the
correct pending marker and newest rescue survived the killed
coordinator, while the previous writer is stopped. It verifies
that clearing pending with a wrong token fails, and the QA
supervisor refuses to launch the next writer until the exact
token is intentionally cleared.

After each controlled continuation, the same QA SQLite history
is used by the next image. All preexisting L1/L2/L3 records
remain a prefix, full PRAGMA quick_check returns ok, PHOTO identity,
uncertain Telegram send intent, and the status_cleanup queue
(including needs_unpin and retry_at) remain unchanged.

## Evidence

- Normal offline local image switch and reverse: PASS.
- Actual two SIGKILL children at forward and reverse coordinator
  boundaries: PASS, no loss of root pending markers.
- Wrong-token marker release blocked in both directions: PASS.
- Pending prevented activation by candidate QA sole-owner
  supervisor before either stage's explicit authorization: PASS.
- Complete old history, three-phase writer continuation, PHOTO
  and Telegram intent/cleanup: PASS. QA Docker image IDs were
  distinct, both built offline from a shared local runtime.
- Targeted unit/security policy suite 16/16 PASS; Ruff0.14.2
  and project quality gate PASS.
- All QA files, temporary image tags and running containers were
  removed. Production LXC110 data, image, systemd, Telegram and
  updater were unchanged.

No actual new signed release image or Sigstore source archive
was switched. This does NOT prove compatibility of two distinct
application-code versions, a real signed update/rollback, or
guest-reboot recovery. It never grants production release approval.

## Reproduction

    bash tests/qa_offline_image_switch.sh --help
    bash tests/qa_offline_image_switch.sh
    bash tests/qa_offline_image_switch.sh --qa-crash-recovery

Both runtime modes are root-only on disconnected LXC114 guest
shelly-docker-reboot-qa and refuse preexisting fixture paths.
