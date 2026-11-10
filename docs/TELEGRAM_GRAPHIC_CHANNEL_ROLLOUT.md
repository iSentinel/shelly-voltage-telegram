# Telegram graphical status channel — safe rollout checklist

Status: **source-only design and transport QA**. This document does not
authorize public message changes, publishing, or a production upgrade.

## Existing status mutation paths

| Path | Existing operation | Required graphical behavior |
| --- | --- | --- |
| upsert_status_message | editMessageText / sendMessage | Match the stored status type; never blindly edit text on photo |
| rotate_status_after_recovery | sendMessage then delete old | Publish-first replacement; persist ID only after verified success |
| edit_voltage_status / replace_voltage_status | editMessageText / sendMessage | Keep episode deadline, alerts and one status message |
| maybe_finalize_voltage_episode | editMessageText | Safely refresh a photo caption and image with event summary |
| maybe_rotate_stable_recovery_status | sendMessage then delete old | Use same media mode, silent publish-first cleanup |
| ensure_status_pinned / delete_previous_status | pin / unpin / delete | No double pins; unpin only previously known pinned message |

## Invariants required before enabling graphics

1. The new channel feature stays **off by default** and is not set by
   installation, update, CLI examples or old database metadata.
2. Every public update path uses the same persisted status message identity
   and a known text/photo kind, bound to **both** exact chat and message ID.
   Missing, old or cross-channel markers are unknown; never edit text on photo.
3. Text-to-photo conversion uses editMessageMedia on the existing message
   when supported, and verifies the returned message ID and destination.
4. Media edits treat Telegram's "message is not modified" as success.
   Transient failures must not trigger a duplicate public post.
5. A rejected or ambiguous API response cannot advance persisted message
   state. Unknown media state requires a guarded reconciliation path.
6. The image must hide stale, invalid and offline physical readings and
   expired YASNO forecasts independently. Caption must remain accessible,
   localized and at most Telegram's 1024-character photo-caption limit.
7. Message edit frequency is bounded. Alert sound/quiet-hours behavior,
   voltage episode windows and recovery silence must remain unchanged.
8. New status rotations publish first, then persist the successful new ID;
   only then unpin/delete the old one. A failed delete is retriable.
9. Turning graphics off while the current post is a photo must not attempt
   editMessageText; use guarded caption-only fallback or owner-approved
   text replacement with source/SQLite rollback.
10. The private /graphic_preview cannot write public status metadata, pin,
    issue channel edits or trigger outages.

## Acceptance before production

- Test online, starting, stale, offline, recovering, low/high voltage,
  end-of-episode and stable-recovery status changes with mock Bot API.
- For photo status, update caption with editMessageCaption only after
  explicit destination, persisted ID, caption length and API response
  match; no implicit text edit or replacement on ambiguous failures.
- Simulate timeout, 400 not-modified, wrong chat, wrong message ID,
  unavailable photo, failed new publish and failed old delete.
- Verify SQLite message ID continuity, count of active public messages,
  zero synthetic 0 V, quiet hours, threshold sound cooldown and pin behavior.
- Run full Python regression, Ruff, installer/release/rollback fixtures and
  isolated, networkless graphic renderer tests.
- Independently verify a fresh full source+SQLite snapshot and rollback.
- Obtain **separate Owner Approval** before changing the production channel.
- Check Android/desktop recipient-visible card rendering and YASNO content.

PR300 now supplies rendering, private preview, source-only multipart edit
transport and validated **send_status_photo** with explicit destination and
quiet-hours handling. Neither public send nor public edit has a live caller.
Public status paths have **not** been switched. A pure
plan_graphic_status_action helper now selects send_photo, send_text,
edit_photo, edit_caption, edit_text or blocked based on a verified
existing message kind and successful PNG generation. This helper is not
called by production status paths. This is not release approval.
