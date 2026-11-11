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

A `PASS` (exit 0) verifies the 960x800 PNG, offline state, absence
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
and optional frequency, verifies the PNG is 960x800, and reports no
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

## Actual 17:30 cutover against planned YASNO outage

The source-only acceptance suite includes a reproducible Kyiv-local
2026-11-10 transition. The trusted YASNO group 10.1 plan switches
from on to off at 17:30, but Shelly continued measuring real voltage
(203.2 V at 17:30:04 and 197.4 V at 17:31:04). The status card must
remain ONLINE while the meter is connected and reporting fresh valid
samples, even when the schedule says planned outage.

When Shelly becomes unreachable, the card must instead report OFFLINE
without a current voltage/frequency/timestamp and must not display its
historical last-valid voltage (193.8 V) as live. The 48-slot YASNO plan
remains explicitly a forecast, independent of what Shelly measured.
Neither a planned blackout nor the loss of Shelly alone is conclusive
proof of why the meter became unreachable.

The eight scenarios in tests/test_graphic_scheduled_cutover.py exercise
this exact boundary, low-but-valid voltage, stale online readings,
offline historical-value secrecy, and absence of trusted YASNO data.
The read-only real-device observation was recorded separately; no
public Telegram post or production database value was modified as part
of this regression.


## Physical installation: Shelly ahead of ZUBR protection

Shelly is installed after the utility meter and before the ZUBR
protective cut-off module. Therefore, a downstream ZUBR trip does not
directly remove power from Shelly.

An unreachable Shelly could mean upstream mains loss, a fault of
Shelly or its local supply/wiring, or Wi-Fi/network connectivity loss.
Offline alone is **not proof of 0 V**, ZUBR activation, or a confirmed
grid outage. Fresh Shelly telemetry is authoritative even if YASNO
predicts planned electricity loss.

The Ukrainian offline PNG uses the qualified text
"ЙМОВІРНЕ ЗНЕСТРУМЛЕННЯ / ЗБІЙ SHELLY ЧИ МЕРЕЖІ".
A separate prefix identifies YASNO as a forecast, not a measurement.
The full text fits inside the 960x800 bitmap panel, and all required
Ukrainian glyphs are available. Six source-only regression cases in
tests/test_graphic_supply_topology.py enforce this, including stale
connectivity, unavailable live readings, and low but valid voltage.

Public deployment remains blocked until the existing Telegram media
identity is verified by a guarded operation and a fresh matching
source+SQLite snapshot passes the rollback readiness gate.

## YASNO planned off/on intervals and 24-hour battery grid

The public status text now derives all scheduled intervals from the
same validated 48 half-hour YASNO slots. Each consecutive known
offline period gets a red empty battery (🪫), while a period with
electricity forecast by the schedule gets a green battery (🔋).
Each text line includes start/end time and an exact whole- or
half-hour duration. An unknown/Possible interval receives ⚠️ instead;
the parser never interprets it as a confirmed outage.

For the supplied Kyiv DTEK group 10.1 day:

~~~text
🪫00:00 - 04:00 (≈4 год.)
🔋04:00 - 08:30 (≈4.5 год.)
🪫08:30 - 16:00 (≈7.5 год.)
🔋16:00 - 17:30 (≈1.5 год.)
🪫17:30 - 24:00 (≈6.5 год.)
~~~

The schedule has 18 planned hours off and 6 forecast hours on.
The text formatter shows up to eight periods and an explicit
overflow count if more exist. Never use this independent forecast
as a substitute for fresh Shelly voltage or loss-of-connectivity
measurements.

The PNG is a 960×800 Ukrainian graphic with 24 large color-coded
hour tiles in a 6-by-4 grid. When the forecast changes inside an
hour, the tile has two halves (for example, 08:30 and 17:30).
Below the tiles, the graphic shows the five chronological intervals
with small drawn green/red battery icons and durations. For unusually
fragmented days, the PNG summarizes overflow while the grid still
shows all 48 half-hour states. Stale/unverified YASNO data is hidden.
No inference of 0 V is made when Shelly becomes unreachable.

The source-only acceptance suite covers 24 labels, split hour tiles,
individual periods and safe truncation, 24:00, unknown slots,
off/on totals, and separate online/offline Shelly states.


## Midnight / DST rollover for offline cards

A Telegram image can remain pinned through Kyiv midnight while Shelly is
offline. Preview source data must therefore be checked against the rendering
clock, not only the original time of fetching the YASNO group 10.1 cache.
The previous day's 48-slot forecast is invalid at exactly 00:00:00 Kyiv
local time. A fresh and verified new-day forecast may be shown independently
of Shelly connectivity; neither one may supply physical live voltage.

The source-only regression in tests/test_graphic_midnight_rollover.py tests
23:59:59 -> 00:00:00 on November 10/11, Ukrainian DST rollback on October 25
(the local calendar day lasts 25 hours), spring-forward on March 29
(the local day lasts 23 hours), old-day source rejection, and the
absence of historic 193.8 V from an offline widget on both sides of midnight.
The text fallback and PNG must both discard yesterday's forecast at the
same instant; unsupported Telegram clients must not show an older schedule.
No network calls, live message edits, or SQLite writes occur. This source
acceptance does not authorize a public Telegram migration.


## Zero-voltage presentation safety

This Shelly is installed on the measured mains side ahead of ZUBR. When its
supply is lost it typically becomes unreachable; a synthetic 0.0 V value
must not become evidence of live supply. The Telegram widget encoder,
source-only PNG renderer and text fallback now require a fresh voltage of
at least 0.1 V to display live measurements (including L2/L3 fallback).
A zero or sub-displayable 0.04 V reading while connectivity says online
is shown as UNKNOWN, never as confirmed offline or an outage. Genuine
positive low readings such as 176.2 V remain accepted. This is a
presentation-only fail-closed boundary: it does not change the Shelly RPC,
production SQLite, outage event history, or bot's deployment defaults.
The physical recovery acceptance still requires independently fresh
hardware readings and a closed incident; no forecast can close it.


## Public photo-caption identity audit

The public Telegram embed normally includes tgme_widget_message_text
for a caption on a photo. If the same exact post has a
tgme_widget_message_photo_wrap or tgme_widget_message_photo marker,
the read-only audit classifies it as photo, including when a text
caption also exists. Text without photo markers remains text.
Unsupported document/video markers, ambiguous missing media, mismatched
channel/message IDs, unresolved sends and conflicting persisted SQLite
media identity remain BLOCKED. The audit stays read-only and returns
PUBLIC_POST_MATCH_FOR_REVIEW / FIXTURE_MATCH_ONLY; neither result grants
permission to write the production media-identity marker or to deploy.


## Forecast legend and daily totals (Design Polish)

The main 960x800 PNG adopts the useful 24-hour, two-half-hour-per-cell
schedule layout from the supplied DTEK example without replacing the
separate **historical** voltage/statistics report. A legend labels
green as expected power, red as expected scheduled outage, and amber
as a possible outage. The source publication time and Kyiv calendar
date are printed only after the trusted YASNO forecast passes current-day,
age, and source checks. Totals count exactly 48 half-hour slots with
unrounded whole/half hours; the forecast is never evidence of physical
power. Offline Shelly still displays unknown live voltage, not zero or
its last historical sample. This candidate is not installed in production.


## Historical voltage chart remains independent

The chart sent by the existing /chart command is a separate 1000x560
historical voltage report, not a YASNO forecast. It already leaves
long gaps unconnected instead of interpolating through missing Shelly
samples. Design Polish now prints the exact Kyiv date and clock time
of its last measurement in its header and labels samples older than
60 seconds as HISTORICAL. A recent sample is called RECENT, not LIVE.
The chart retains its original sample count and min/max statistics;
no outage, database, or Telegram channel metadata is changed.


## Strict media conversion receipt validation

Telegram Bot API editMessageMedia supports conversion of a text message
to a photo with the SAME exact message ID. The channel cutover must use
this edit path rather than creating a new post then deleting the pinned
text. The transport checks the returned message ID and chat ID,
a nonempty photo media object with a file_id, and the exact requested
caption before a successful conversion can be persisted in SQLite.
A 400 "message is not modified" is an accepted no-op for an already
known photo, but is NOT sufficient proof of a text-to-photo conversion.
If the response is ambiguous, including transport timeout or missing
photo data, the media identity becomes UNKNOWN, and unattended retries
must be blocked. No new channel status may be sent as a fallback.
The exact legacy message identity, independent backup, and rollback
plan remain separate prerequisites for public deployment.


## Existing unrelated pinned message and reversible fallback

The live getChat pinned_message is currently **not** the same as the
dedicated channel status message stored in SQLite. The exact public
status was separately found as a legacy text post, but this public
HTML proof alone is not a Bot API edit acknowledgment. Do not treat the
unrelated pin as the status; do not unpin/delete it to satisfy a migration
preflight. A verified, owner-approved in-place editMessageMedia can retain
the original status ID, and the later pin operation (if desired) must
be an explicit, independently checked step. Conversion itself must
not unpin or replace another message.

If a verified photo card requires a safe visual fallback, disable the
graphic flags while continuing to run the upgraded media-aware code:
it can edit the *same photo's caption* in place without sending a
new message. Do NOT assume restoring the old text-only source alone
is a safe media rollback. Full source restoration must be separately
validated against the already converted Telegram media type. Never
claim the offline forecast proves mains power or restore stale SQLite
that omits later historical samples.


## Strong legacy text confirmation receipt

Never persist a legacy text media marker solely because Telegram
editMessageText returned without an exception. Normal text updates
intentionally accept "message is not modified" as idempotent, which
is not sufficient for initial identity confirmation. The separate,
owner-approved confirmation now invokes strict verify_existing_text:
a genuinely changed text post must return a Telegram Message with
matching message ID, chat ID and exact requested plain text, and no
photo. Wrong/partial replies, HTTP 400 no-op, timeout and invalid
targets fail closed without writing SQLite media metadata. This is
not invoked by the automatic status loop. A controlled, paused-writer
migration and deliberately changed confirmation text are required.
Owner Approval is already granted for this one-time guarded cutover.


## Owner-approved v0.5.16-hw graphic release candidate

Production remains 0.5.15-hw until a separate guarded installation.
This branch now identifies the next source/release candidate as 0.5.16-hw,
which prevents an in-place code change being mistaken for a rebuild of
an already installed 0.5.15-hw runtime. The historical LIVE_VERSION
field remains unchanged. Promotion still requires completed exact-head
CI, controlled legacy text identity verification, source and SQLite
stopped-writer snapshot with complete table parity, and monitoring
of the existing Telegram status after the in-place photo edit.
Owner Approval was already granted for this specific migration, but
version numbering and CI never waive the technical rollout gates.


## Verification clock is not the YASNO publication clock

The trusted YASNO/DTEK endpoint can republish the same applicable daily
plan infrequently. A source updatedOn timestamp older than two hours is
not inherently stale if it is within the source parser's separate
36-hour publication bound AND the current-day ScheduleApplies record is
present AND the trusted authenticated-envelope cache was retrieved
within two hours. The graphic widget receives the *verified retrieval*
clock (retrieved_at), not published_at. For accuracy the PNG labels it
"ПЕРЕВІРЕНО", rather than implying YASNO created a new schedule at that
moment. The supplied test checks a four-hour-old publication against
a fresh cryptographically hashed cache, and still rejects stale fetch
or source publications beyond the original 36-hour limit. This never
turns planned schedules into physical measurement evidence.


## Owner Approval evidence in read-only rollout readiness

Run the exact rollout readiness preflight with the optional
--owner-approval-file parameter, pointing only to the existing
root-owned 0600 immutable PR300 scope record. A validated receipt
changes the result to owner_approval_required=false and, if the
technical gate passes, READY_FOR_TECHNICAL_REVIEW. This is NOT
a deployment command: deployment_allowed remains false even for
valid approval. It cannot waive the exact Telegram media identity,
fresh checksummed source and SQLite snapshot, recipient-visible
acceptance, or the prohibition on changing a different pinned post.
Invalid, linked, world-readable or wrong-PR records are BLOCKED.
No bot token or private channel ID is printed.


## One-shot existing text confirmation CLI (source-only)

The candidate scripts/graphic-confirm-legacy-text.py defaults to
a non-mutating inspection (--jsonl, without --execute). It refuses
to proceed while the Compose service OR Docker process is running,
without the exact protected PR300 Owner Approval record, if SQLite
media metadata is already present/ambiguous, or unless an
independent recent exact-version source+SQLite snapshot passes
whole-database parity. The token is read only in explicit execution
mode, from a regular, unlinked 0600 private file. The only possible
Telegram operation is one editMessageText call against the
already persisted status message ID. No send, delete, pin, unpin,
retry or new message ID is allowed. Its Ukrainian temporary text
marks the last voltage as HISTORICAL because the writer is stopped.
A fully verified Telegram Message reply is required before storing
the text media marker in SQLite. A network timeout or malformed reply
returns an explicit UNKNOWN edit outcome and blocks automated cutover.
Before the one outbound request, the helper commits
telegram_legacy_text_confirm_pending=pending in SQLite. If the
client crashes or the API reply is lost, the marker survives and
blocks both repeated confirmation and the graphic rollout readiness
gate. The marker is cleared only in the same transaction that stores
a strictly verified text media identity after a genuine Telegram
Message receipt. Do not delete the marker or repeat the edit until
the exact public status is reconciled by an independent recipient-side
read-only audit. A failing request may have succeeded remotely.

Example dry-run (when a matching-version stopped-writer snapshot
exists; never paste actual Bot tokens or channel/message IDs):

~~~bash
set -a; source /opt/shelly-voltage/.env; set +a
python3 scripts/graphic-confirm-legacy-text.py \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --snapshot /var/backups/shelly-voltage/upgrades/APPROVED_SNAPSHOT \
  --installed-version-file /opt/shelly-voltage/VERSION \
  --owner-approval-file /opt/shelly-voltage/validation/APPROVAL_RECEIPT \
  --token-file /opt/shelly-voltage/secrets/telegram_token --jsonl
~~~

The explicit --execute variant is permitted only within the
separately reviewed and quiesced change window after the dry-run
passes. This source code and its tests are NOT an authorization to
stop the live monitor, overwrite its database, or replace the
public channel status. After successful text identity confirmation,
deploy the media-aware code only with a known-good rollback and
recipient-visible photo/card inspection.


## Whole-SQLite parity in the shared rollout gate

The protected graphic rollout readiness script now requires a live
full-logical-SQLite digest match against the restored archive, using
the same strict validator as the one-shot confirmation CLI. A backup
that is recent, internally consistent and has valid SHA256 is still
NOT sufficient if it omits even one measurement, outage, metadata
item or SQLite header field. The CLI fails with
"rollback SQLite snapshot differs from live database" and never
sets deployment_allowed=true. As long as the writer keeps collecting
new readings, an older online warm backup will generally fail this
exact parity check. The production rollout therefore requires a
fresh stopped-writer source+SQLite snapshot checked before any
Telegram edit. Never repair this by overwriting current SQLite
with an older backup. Rollback itself requires a separate safe
reconciliation of Telegram message media.


## Pending confirmation blocks all status mutations

An uncertain single-shot legacy text edit persists
telegram_legacy_text_confirm_pending. The live Monitor checks this
marker before every status text/photo edit, new graphic status send,
status creation, post-outage replacement, pin/unpin operation and
status control cleanup. This guard prevents a later timer callback or
restart from racing the owner-approved confirmation. Unrelated
availability alerts and physical measurements are independent: the
marker does NOT suppress Shelly polling or corrupt history. It is
never cleared merely by a new worker restart. Only a verified
same-chat/same-ID/same-text Telegram receipt permits atomic clearing
together with the confirmed text marker. The preflight also refuses
graphic migration while the pending key exists.


## Dual-snapshot cutover and media-safe rollback

The owner-approved cutover requires two distinct writer-stopped
backups (the previous warm backup is not a replacement):

1. Stop the Compose service and verify that the Docker writer is truly
   stopped. Make snapshot A: exact installed 0.5.15-hw source and
   complete SQLite with authenticated SHA256, rollback dry-run and
   full logical parity against the stopped database. This is the
   original legacy-text recovery checkpoint.
2. With the writer still stopped, run the one-shot
   graphic-confirm-legacy-text.py first without --execute (dry-run),
   then once explicitly with --execute. It may update only the known
   existing status ID by editMessageText, requiring a Telegram
   Message receipt with exact chat, status ID and requested text.
   On uncertain result, stop. Do not retry or delete the pending
   marker automatically.
3. After confirmed text identity has been committed, create
   snapshot B from the still-stopped writer, containing the new
   telegram_status_media_identity=text binding. Verify all full-DB
   tables, row counts and SHA256 against newly modified SQLite.
   General graphic rollout readiness MUST use snapshot B, NOT A.
   Snapshot A is no longer byte-for-byte current after confirmation.
4. Install the exact-head CI-successful media-aware candidate only
   with a separately verified disaster recovery procedure and
   source+SQLite snapshot B. Never enable photo conversion if an
   owner or technical gate is blocked. Check the same original
   Telegram status message and that the different pinned message
   remains untouched.
5. After an in-place editMessageMedia converts that text to a photo,
   restoring legacy 0.5.15-hw source over the public photo is NOT a
   safe visual rollback. A compatible media-aware release with
   graphic flags OFF may update only the existing photo caption.
   Reverting source and SQLite cannot revert an external Telegram
   photo. For a full source rollback, reconcile the public message
   separately under explicit operator control.

Source-only rollback.sh now invokes
scripts/graphic-rollback-media-guard.py BEFORE stopping Compose or
touching SQLite. This read-only check blocks photo/unknown media and
both unresolved publication markers, even with
--confirm-db-replacement. It permits legacy unbound or verified
same-ID text states. A missing main SQLite file only counts as a
fresh installation when no orphan WAL, shared-memory or rollback
journal sidecar remains; otherwise the guard refuses a restore.
On BLOCKED, never bypass the guard or delete the pending marker;
reconcile the exact public Telegram post first.


## Media transition during writer stop

The rollback helper verifies Telegram media identity twice: before
stopping the Docker writer and again after writer shutdown, before
any rescue/source/SQLite restoration. This closes the stop-time
race in which a callback writes a photo/unknown marker after the
first read. A failed post-stop check blocks source/SQLite restoration.
With normal startup enabled, it attempts to restart the
still-installed current release; with explicit --no-start, the writer
remains stopped and the operator must restart the compatible current
version manually. A failed restart is an incident, not a successful
rollback. An isolated shell test mutates SQLite during fake
docker compose stop and verifies that source and measurements remain
unchanged after the second guard blocks.


## A/B snapshot tool (source-only)

scripts/graphic-cutover-snapshot.py captures two independent,
full-parity source+SQLite checkpoints only after the systemd Compose
unit and Docker writer have both been stopped. It does not stop/start
services and never contacts Telegram. Its default is read-only;
explicit --execute is required to publish archives.

Example operator commands for review only, with the writer already
stopped and the source-only candidate tested:

~~~bash
set -a; source /opt/shelly-voltage/.env; set +a
python3 scripts/graphic-cutover-snapshot.py \
  --phase A --source /opt/shelly-voltage \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --owner-approval-file /opt/shelly-voltage/validation/telegram-graphic-owner-approval-20261110-223420.json \
  --output-root /var/backups/shelly-voltage/graphic-cutover --jsonl
python3 scripts/graphic-cutover-snapshot.py \
  --phase B --source /opt/shelly-voltage \
  --database /var/lib/shelly-voltage/voltage.sqlite3 \
  --owner-approval-file /opt/shelly-voltage/validation/telegram-graphic-owner-approval-20261110-223420.json \
  --output-root /var/backups/shelly-voltage/graphic-cutover --jsonl
~~~

For the authorized maintenance operation repeat the A invocation with
--execute, perform the single approved Telegram exact-text confirmation,
then repeat B with --execute. A and B are different protected 0700
directories containing a 0600 source archive, complete SQLite gzip,
exact-final-path SHA256 sidecars and a versioned manifest. Source
archives exclude .env, tokens, secrets and local validation data.
Each archive must pass both validators, full current SQLite parity,
and the post-capture stopped-writer check. Incomplete staging is
deleted on error. A requires no Telegram media marker; B requires
verified text media matching the configured public status channel
and the exact persisted message ID. Photo/unknown media, unresolved
requests, invalid approval or active writer are BLOCKED.

The chosen archive root must be disjoint from both the installed
source tree and the entire live SQLite directory. Source-nested or
database-nested backups can accidentally include their own staging
files or mingle with WAL state and are always BLOCKED before writing.

The normal graphic rollout readiness must accept the B snapshot
and its exact source version before candidate installation.
Retaining A for disaster recovery never makes an old text-only
source compatible with a Telegram photo.
