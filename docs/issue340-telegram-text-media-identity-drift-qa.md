# Issue #340 — Telegram text status identity drift after outage (QA ONLY)

**Draft; production rollout and public pinned PHOTO remain NO_GO.**

## Confirmed incident (16 November 2026, Kyiv)

Read-only Gateway LXC110 inspection after Shelly power restoration:
- Runtime OCI `0.5.21-hw` returned /ready 200, fresh live voltage,
  a successful L1 outage close, and no Telegram publish warning.
- The active `telegram_status_message_id` was **unchanged** from
  the intact 03:30 pre-outage SQLite backup.
- The `telegram_status_media_identity` marker described `text`,
  but its bound ID differed from the current status ID. The mismatch
  was **already present in the pre-outage backup**.
- In the running source, `status_media_kind()` returns `unknown`
  for mismatched bindings, so `legacy_media_guard()` correctly
  refuses status edits. `rotate_legacy_photo_after_recovery()`
  returns before a new post, without an actionable warning.
- This is not a Shelly measurement failure or evidence of a failed
  Telegram HTTP request. The live bot was intentionally left alone.

## Narrow prevention in this Draft

A successful Telegram `sendMessage` returns a confirmed new text
message ID. Existing text send paths stored the new ID but neglected
to replace the old format binding. The next edit therefore blocked
on a stale marker. Update the two values in **one SQLite transaction**
through `Storage.bind_text_status()`.

The four paths covered are the first status/recreate send, voltage
status replacement and post-outage recovery rotation. No PHOTO
activation, image upload, Telegram API format guess or automatic
repair of a pre-existing inconsistent marker is introduced.

For an already mismatched marker, remain fail-closed but add a bounded,
non-identifying warning recommending owner reconciliation. Do not
delete the marker, rebind it to an unverified ID, delete the old
Telegram message, or send a duplicate to guess the media format.

QA must use only disposable SQLite + a synthetic Telegram sender.
Cover successful text rotation, short edit/recreation, voltage event,
real PHOTO hold, pre-existing mismatch refusal, failed Telegram send,
and transaction rollback on failed media marker write.

## Live recovery decision (separate owner operation)

Do not write production SQLite or force new Telegram posts until an
owner-controlled recovery plan verifies the actual status channel,
active message format and ID, accounts for potentially successful but
unrecorded Telegram sends, coordinates all old bot writers, captures
current WAL-complete backups, and explicitly approves an exact
one-shot reconciliation. Normal release prerequisites remain the
global G/B/W/A freeze, signed current OCI and source, approved
pending-boot recovery, two independent WAL-complete restored copies,
Telegram long-poll teardown, physical blackout QA, independent
security review and explicit Owner Approval accepting possible
complete loss of the single NVMe storage domain.
