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
  The *presence* of the marker is authoritative even if its value is
  empty or malformed: a corrupted marker cannot silently enable
  another send, legacy text confirmation, photo edit, or text rollback.
  The read-only preflight and JSONL audit apply the same strict rule.
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

For automated release checks, append --jsonl to produce exactly
one JSON Lines record with ok, status, reasons, owner_approval_required
and deployment_allowed. The record includes no chat IDs, message IDs,
secrets, or local file paths. Its exit code remains zero for a passed
preflight and two for a blocked one. Even when all static checks pass,
deployment_allowed is deliberately false because separate Owner Approval
is mandatory.

Filesystem races are handled as fail-closed decisions. If a snapshot is
renamed or disappears between the initial check and checksum/freshness
validation, the audit returns a generic BLOCKED reason, not a Python
traceback with a private path. Malformed SQLite status metadata, including
non-text values, is also blocked. Re-run the check only after verifying the
fresh snapshot and exact channel message type; never bypass the gate.

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

## Offline presentation: mains outage likely, not confirmed

On an unreachable Shelly, the PNG retains the measured connectivity
state OFFLINE, hides historic voltage and frequency, and uses
POWER OUTAGE LIKELY / NOT CONFIRMED. A disconnected Shelly commonly
correlates with a mains outage, but Wi-Fi or device faults can also
produce the same symptom. This wording is advisory, not evidence of
zero measured voltage. The YASNO forecast remains labelled as forecast
and must not override live telemetry.
## Ukrainian 5×7 raster captions (draft)

PNG labels now use built-in 5×7 Ukrainian bitmap glyphs, without fonts,
image packages or external API calls at runtime. The card shows
SHELLY / СТАН МЕРЕЖІ, НАПРУГА МЕРЕЖІ / L1, ГРАФІК YASNO / ДТЕК,
ГРУПА 10.1, and localized status labels. If Shelly is offline, the
measured voltage remains hidden and the warning is
ЙМОВІРНЕ ВІДКЛЮЧЕННЯ / НЕ ПІДТВЕРДЖЕНО. The source-labelled YASNO
slots remain forecast only and must never indicate actual mains status.
Units use Ukrainian В and ГЦ; Shelly and YASNO remain product names.
Localization does not enable Telegram publishing or change storage.

## Reproducible source-only offline PNG acceptance

Run the dependency-free local audit on a saved, sanitized JSON health
snapshot rather than querying Telegram or Shelly. The command must see
`connectivity=offline`; it **never** certifies live voltage, energy
availability, or an actual mains outage.

```bash
python3 scripts/graphic-offline-acceptance.py --help
python3 scripts/graphic-offline-acceptance.py \
  --health-file /var/tmp/health-snapshot.json \
  --output /var/tmp/graphic-offline-acceptance.png \
  --group 10.1 --jsonl
```

Optional `--forecast-file` is a local JSON object with exactly
`bars` (48 half-hour slots), `day` (Kyiv date), `updated` (Unix
timestamp) and `source` (`yasno`). Expired or mismatched forecast
dates are rejected. `--now` accepts an explicit Unix timestamp for
deterministic fixture tests. The script does not fetch any API URLs.

A `PASS` (exit 0) verifies the 960x560 PNG, offline state, absence
of live voltage/frequency/time fields and correct forecast separation.
A `FAIL` (exit 2) emits a safe reason code without credentials or
paths. Repeated runs with identical input reuse the same PNG. Existing
different or oversized files, links and unsafe input files are never
overwritten or followed. Existing PNGs are compared with bounded
streaming SHA256 checks rather than loaded unbounded into memory.
Neither outcome authorizes a public Telegram message or a
production installation.

## Read-only pinned-message identity audit

An independent Telegram Bot API `getChat` query exposes only the most
recent pinned message, not arbitrary older channel messages. The new
CLI requires an exact channel/chat/message ID match and unambiguous
text/photo fields. A different latest pinned message is **BLOCKED**
and cannot justify writing media identity metadata or rotating a post.

```bash
python3 scripts/graphic-pinned-media-audit.py --help
python3 scripts/graphic-pinned-media-audit.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --response-file /path/to/private-getchat-response.json \
  --jsonl
```

The `--response-file` mode is for isolated, networkless tests:
`FIXTURE_MATCH_ONLY` is *not* live evidence and never permits rollout.
An authorized, network-connected maintenance operator may use
`--token-file /path/to/private/bot-token` instead. That mode
calls only `getChat` and returns `MATCH_FOR_REVIEW` when the
**current** latest pinned message has the exact stored status ID,
channel ID and a single supported media type. Both modes leave
Telegram and SQLite unchanged and always report
`deployment_allowed=false`; no identifiers, message content or
bot token are printed.

If pinned IDs differ (as observed on the existing production channel),
the owner-authorized, exact-message text verification workflow must
remain separate from this CLI. Do not invent a marker, auto-pin a post,
send a duplicate or claim a mismatched pin is verified. The operator's
conditional approval to switch the public design does not bypass
snapshot, media identity or private visual acceptance requirements.

## Source-only online PNG acceptance

The offline and online acceptance CLIs use the same safe local file
checks. Neither contacts Telegram or Shelly: provide an existing,
sanitized health JSON captured by an authorized operator. This test
accepts online telemetry only if the sample timestamp is no more than
60 seconds old, voltage is a finite bounded measurement, and the rendered
status is ONLINE. A connectivity flag alone is never enough.

~~~bash
python3 scripts/graphic-online-acceptance.py --help
python3 scripts/graphic-online-acceptance.py \
  --health-file /var/tmp/health-snapshot.json \
  --output /var/tmp/graphic-online-acceptance.png \
  --group 10.1 --jsonl
~~~

An optional --forecast-file follows the same local 48-slot YASNO format
as the offline CLI; it must represent the current Kyiv calendar day and
pass source/freshness checks. PASS (exit 0) includes measured voltage
and optional frequency, verifies the PNG is 960x560, and reports no
Telegram sends. FAIL (exit 2) gives a path-free reason code. Offline,
missing, stale or invalid voltage samples are rejected. Existing
different PNG files, links and oversized inputs are not replaced or
followed. Repeating an identical preview is idempotent.

An online-preview PASS does not confirm that the real Telegram status
message is text/photo, or that a historical rollback snapshot
still matches the latest source/SQLite state. Public rollout requires
separate exact-message verification and fresh rollback validation.

## Exact public post evidence (read-only, no Telegram edits)

A public channel may have a different **latest pinned** message from the
stable voltage status saved in SQLite. In that case, getChat's pinned
message alone cannot verify the status media kind. Use the source-only
exact-public-post audit instead:

~~~bash
python3 scripts/graphic-public-post-audit.py --help
python3 scripts/graphic-public-post-audit.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --chat-env TELEGRAM_STATUS_CHAT_ID \
  --token-file /path/to/telegram_token \
  --jsonl
~~~

The live mode calls only Bot API getChat, checks the channel's numeric ID,
derives its public username, then GETs that exact status ID's t.me embed
page. It accepts only one matching data-post wrapper and a single
recognizable text or photo marker in the matched post. Surrounding
avatars, other posts, unknown types, linked input files, unresolved
publications and conflicting persisted markers fail closed.

For networkless tests, --html-file and --public-username accept a
bounded local HTML fixture. FIXTURE_MATCH_ONLY is never live proof.
PUBLIC_POST_MATCH_FOR_REVIEW from the live checks is useful independent
evidence but remains provisional: HTML is a public preview, not a
Telegram Bot API message object. Both modes leave deployment_allowed
false; neither writes SQLite, posts, edits, pins, or deletes messages.
No message ID, chat ID, body text, username, URL or bot token is printed.

When exact public HTML indicates text, the next operational requirement
is still the separately guarded, owner-authorized exact-message text
verification by Telegram editMessageText and a success response bound
to the existing chat/message. A timeout or ambiguous result must block
further conversion. Do not stamp a text marker based on HTML alone.
Refresh a matching source+SQLite rollback snapshot immediately before
any approved production rollout and perform recipient-visible private
acceptance before public status replacement.
