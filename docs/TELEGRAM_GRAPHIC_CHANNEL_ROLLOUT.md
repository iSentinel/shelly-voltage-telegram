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
   An unbound legacy text post must be independently verified via an
   owner-approved, explicit editMessageText before recording its type.
   Never auto-confirm a previously unknown or timed-out conversion.
4. Media edits treat Telegram's "message is not modified" as success.
   Transient failures must not trigger a duplicate public post.
5. A rejected or ambiguous API response cannot advance persisted message
   state. Unknown media state requires a guarded reconciliation path.
   Before text-to-photo conversion, record an explicit unknown format for
   the exact chat and message ID; only a verified successful edit records
   photo, so a timeout never leaves stale text metadata for a photo.
6. The image must hide stale, invalid and offline physical readings and
   expired YASNO forecasts independently. Caption must remain accessible,
   localized and at most Telegram's 1024-character photo-caption limit.
7. Message edit frequency is bounded. Multipart sendPhoto and
   editMessageMedia use the same per-bot API lock as JSON methods.
   A transport exception must always release that lock. Alert sound,
   quiet-hours behavior, voltage episode windows and recovery silence
   remain unchanged.
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

## Source-only integrated lifecycle (not deployed)

The PR300 candidate now routes all main public-status mutation paths
through guarded photo/text operations **only when both** settings are
explicitly 1: `TELEGRAM_GRAPHIC_CHANNEL_ENABLED` and
`TELEGRAM_GRAPHIC_OWNER_APPROVED`. It also requires a separate configured
public status channel. The production release has neither the code nor these
settings. Environment flags do **not** constitute actual Owner Approval.

- A new status publishes one photo if its renderer succeeds, or a normal
  text status on rendering failure. Message identity and media kind are
  recorded after Telegram returns the new message ID.
- Existing verified text/photo posts are edited in place; unknown,
  unverified, cross-chat or stale status identities refuse public mutation.
- Every possibly ambiguous *new* Telegram send stores
  `telegram_graphic_publish_pending` **before** making the request.
  Retries and text rollback block while this marker exists. Clear it only
  after independent owner reconciliation of Telegram channel history and
  SQLite; never clear it just because a timeout has elapsed.
- A per-monitor reentrant status lock serializes ID checks and all media
  mutations against simultaneous status refresh and voltage callbacks.
  The Telegram API session lock separately serializes actual HTTP calls.
- The voltage-episode edit window, periodic update cadence, notification
  cooldown and silent recovery path retain their existing outer policies.
- Switching off the flags while a verified photo remains updates only
  its caption. It does not convert it to text, resend, repin or delete it.
  This rollback guard applies only to installations with a dedicated
  status channel; private/text-only installations retain their legacy
  behavior unchanged. This is not the final visual rollback.
- Unknown legacy text posts are never auto-confirmed. A separate,
  expressly owner-authorized preflight must establish their exact media
  type before graphical publication can proceed.
- A private `/graphic_preview` is still the only acceptable visual
  recipient test before a separately approved maintenance window.

## Read-only migration preflight

The source-only tool `scripts/graphic-migration-preflight.py` never calls
Telegram and never modifies SQLite. It requires an existing database and
reads the expected chat from an environment variable; it does not print
the chat ID or message ID. Blocked conditions exit with code 2.

Example for an authorized maintenance operator with an already configured
`TELEGRAM_STATUS_CHAT_ID` environment variable:

```bash
python3 scripts/graphic-migration-preflight.py --help
python3 scripts/graphic-migration-preflight.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3
```

A `PREFLIGHT: PASS` means only that SQLite metadata is internally
consistent. **It does not authorize deployment**. For the existing
production 0.5.15-hw database, the channel ID is known but the media-kind
marker is not yet verified, so migration remains blocked until a
separately approved Telegram/SQLite reconciliation.

## Power-outage renderer acceptance

The synthetic acceptance suite
`tests/test_graphic_offline_end_to_end.py` validates the health-state
encoder and PNG renderer together, without Shelly, Telegram or the internet.

- An offline health state with historical 216.9 V does not include
  current voltage or frequency in public widget data.
- A forecast predicting electricity cannot override an offline meter.
  Missing forecast information does not imply restored power.
- A recovering or stale meter remains unknown until a fresh successful
  sample is available.
- A new verified online sample takes precedence over a forecast predicting
  an outage. Synthetic zero in an offline snapshot cannot be displayed
  as a live voltage.

These tests use invented fixtures, not user telemetry. Physical restoration
requires actual fresh Shelly measurements and a separate owner-approved
maintenance window; the graphical public channel remains disabled.

## Combined migration and rollback readiness audit

The additional `scripts/graphic-rollout-readiness.py` is a read-only
predeployment **gate**, not a deployment or an authorization. It combines
verified channel-bound status metadata with a fresh, independently
checksummed rollback snapshot of **source and SQLite**. It uses the
existing source/SQLite archive validators and reveals no Telegram IDs.
Checks run without Telegram networking or a database write.

```bash
python3 scripts/graphic-rollout-readiness.py --help
python3 scripts/graphic-rollout-readiness.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --snapshot /var/backups/shelly-voltage/upgrades/SELECTED-SNAPSHOT \
  --installed-version-file /opt/shelly-voltage/VERSION \
  --max-snapshot-age-hours 2
```

A status of `BLOCKED` (exit 2) is expected with the current production
legacy text status, which lacks a verified media marker. An older upgrade
snapshot is also not accepted as proof of a fresh predeployment copy.
The rollback source archive must carry a VERSION matching the exact
installed release; a historic 0.5.14-hw source backup cannot be treated
as a ready rollback for an installed 0.5.15-hw. No version is inferred
from archive directory names, modification times or caller input.
Do not clear markers or fabricate timestamps to make the check pass.

A `READY FOR OWNER REVIEW` result (exit 0) confirms only that the
selected archive and status metadata passed these static checks.
Fresh SQLite parity across a *stopped* writer, the approval record,
Telegram message verification and recipient-visible acceptance remain
mandatory before any channel change. Actual installation and rollback
are separate operations; this script never performs them.

## Production activation still blocked

Prior to activation, the owner must approve a versioned release and a
specific maintenance plan, including source+SQLite snapshot, verification
of the current channel's message ID/type, safe migration, post-change
recipient-visible confirmation, offline/recovery tests and actual rollback.
No source-only candidate is authorization to edit public Telegram messages.
