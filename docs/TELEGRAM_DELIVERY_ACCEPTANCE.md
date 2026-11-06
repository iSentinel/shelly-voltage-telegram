## 2026-11-06 — natural low-voltage and Shelly outage #12

A genuine Pro EM-50 measurement triggered `warning_low`
at 205.0 V around 10:08:33 Kyiv. The warning was saved
in private SQLite metadata and the stable Telegram status
message identifier remained present. No new Telegram transport
errors were found during that check. These are **internal**
delivery indicators, not proof that a subscriber saw the
status-channel message.

Shelly connectivity was then lost from 10:16:51 to 10:20:01
(outage #12, 190.4 seconds). The last voltage sample before
disconnect was stale and must not be displayed as a current
measurement; do not equate connectivity loss with confirmed
mains failure. Two real readings during the reconnection
hysteresis were retained, followed by normal polling.
No zero-voltage readings were synthesized.

**Outstanding operator check:** inspect the status channel
for the naturally occurring low-voltage warning, offline
status and subsequent recovery. Verify the timestamps and
whether the same status was edited as configured. Do not
create a fake outage, edit the device's voltage thresholds
or claim that private API checks prove subscriber visibility.

## 2026-11-06 — transport health versus cumulative polling errors

During supervised testing of installed `0.5.13-hw`, Telegram
transport briefly lost connectivity at about 09:48–09:49 Kyiv.
The log showed 11 connection errors, one read timeout and five
status-update transport failures. A separate Gateway SSH timeout
occurred in the same interval. At 09:52 the Telegram API doctor
passed, and the 09:53 log check showed no additional failures
after 09:50. The cumulative failure total of 12 is historical;
it is not a measure of currently failed requests.

A source-only diagnostics enhancement adds two non-secret
watchdog JSON fields:
`telegram_consecutive_failures` and
`telegram_poll_success_age_seconds`. Corresponding Prometheus
gauges are `shelly_telegram_consecutive_failures` and
`shelly_telegram_poll_success_age_seconds`. Null in JSON or
NaN in Prometheus means no command poll has yet succeeded.
A successful polling cycle resets the consecutive streak
while the total counter remains monotonic. These fields are
read-only; they do not change Telegram commands, status edits,
API authorization, retries, or electrical thresholds.

The operator has confirmed that repeating the same harmless
private-chat button does not create duplicate messages. The
remaining visual acceptance concerns channel-message editing
during a natural transition, not artificially created alarms.

## 2026-11-06 09:05–09:08 — high-voltage private chat visual review

The operator supplied a screenshot of the **installed 0.5.13-hw**
Telegram bot showing `/preview`, `/status` and `/start`
responses in Ukrainian. The visible voltage around 244.9 V
and the displayed 251.8 V daily maximum are consistent with
the read-only Shelly/SQLite measurement history examined
through Gateway. The private preview explicitly confirmed
that it did **not** edit the public channel message.

**Partial visual PASS:** readable Ukrainian private-chat
status/preview responses and plausible electrical readings.
**Still pending:** press the *same* harmless status/menu
button twice to confirm PR #128's unchanged-edit handling
and inspect actual status-channel visibility/edit behavior.
The screenshot does not prove these operations or show
delivery to all subscribers.

The configured upper warning limit is 253 V and critical
limit is 255 V, with a 15-second alert dwell. Samples
below 253 V are shown as green by the present threshold
policy even when they are elevated relative to nominal
230 V. No threshold crossing was observed in the last
hour at 09:08, so the absence of an alert was expected.
Do not simulate a mains over-voltage or lower warning
thresholds merely to generate test notifications.

## 2026-11-06 08:58 — installed 0.5.13-hw Telegram callback hotfix

After the owner's production upgrade and first live monitor run,
three Telegram command-polling warnings were traced to the Bot API
reply `message is not modified` when a callback attempted an
identical edit. PR #128 (source `394f068`) now treats repeated
identical text and markup edits as successful no-ops, without
ignoring real failures such as `Forbidden`. The regression suite
completed 422/422 tests and GitHub CI passed on Python 3.11,
3.12 and 3.13.

The hotfix was installed on production at 08:55 Kyiv, and the
Shelly, SQLite and Telegram API read-only doctor checks passed.
The watchdog showed zero fresh failures after restart; this
alone does not prove the same button was clicked again.

**Administrator manual acceptance (still pending):** open the
bot's *private admin chat*, use `/status` or `/preview`, and
click the same harmless status/menu button twice. Confirm that
the response is readable, does not create unwanted duplicate
posts and does not show an error on a repeated edit. Separately
inspect the configured status channel for correct text and
timestamps. Do not induce power outages or publish the private
chat/channel identifiers. Report PASS, FAIL or NOT FOUND
with a redacted screenshot if available.

## 2026-11-06 — screenshot acceptance of natural outage #11

The operator provided a screenshot of the status-channel message after
Shelly Pro EM-50 recovered. The visible message shows the device voltage,
frequency, a 6 h 19 min interruption, a normalized-voltage event at
06:57 and a 10-minute event summary. This is **positive visual
evidence** that the earlier installed `0.5.12-hw` release showed
a recovery/status message to the operator. It is not a delivery
receipt for all subscribers and does not validate the later
`0.5.13-hw` installation.

Gateway/SQLite evidence places the **end of connectivity loss**
at 06:37:02 Kyiv and the separate **voltage normalization**
at 06:57:46 Kyiv (211.7 V). These timestamps refer to different
events and must not be conflated. A connectivity restoration alone
does not prove that electrical mains were restored: it might simply
mean the LAN or meter became reachable. Candidate translations
therefore explicitly say **Shelly connection restored** in RU/UK/EN
instead of claiming independent power restoration.

The displayed event summary covers 06:57–07:07. Its zero seconds
outside normal refer to that *post-recovery window*, not to the
preceding 06:37–06:57 readings.
The candidate now adds a localized scope note to any finalized
episode whose first recorded transition is recovery. It makes clear
that measurements before recovery are excluded, without fabricating
an unobserved abnormal duration. No missing low-voltage event duration
is inferred from that summary. The screenshot remains private in the
user conversation; do not add it to the public repository.

# Telegram notification delivery acceptance

This checklist distinguishes **bot API access**, **database state**, and
**message visibility**. They are separate checks.

## Read-only evidence from 2026-11-05

Following the natural restoration of Shelly after outage #10:

- The production recovery acceptance check passed and SQLite confirmed
  outage #10 was closed, new voltage samples existed, and the Telegram
  status message ID was rotated.
- Read-only Telegram Bot API calls `getMe`, `getChat` and `getChatMember`
  all succeeded. The status target was a channel, and the bot had
  administrator membership with permission to post and edit messages.
- No Telegram message was sent, edited, pinned, or deleted during this
  access check. No token, chat ID, or administrator ID was logged.
- These findings **do not independently prove** that subscribers could
  see the specific recovery message. Telegram Bot API offers no general
  `getMessage(chat_id, message_id)` endpoint for retrieving arbitrary
  channel history.

## Optional read-only voltage transition evidence

The current source provides a safe way to inspect stored electrical
state changes without sending Telegram messages:

```bash
python3 scripts/soak-report.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  --hours 3 --json
```

Review `voltage_transition_events` and
`voltage_transition_history_status`. These represent the limited
retained monitor history, **not** proof of Telegram delivery, message
editing or subscriber visibility. No channel ID, bot token or
subscriber information is required to run the report.

## Administrator acceptance for the 22:11–22:28 recovery

The read-only 15-minute recovery verifier for November 5, 2026
returned PASS at **22:28:14 Europe/Kyiv**. It verified a changed
fingerprint of the Telegram status message ID, but it did **not**
retrieve any message from the channel or prove subscriber visibility.

**Action for the authorized channel administrator:**

1. Open the configured Telegram status channel around 22:11–22:29
   on November 5, 2026.
2. Find the recovery/online status. Verify the displayed state and
   timestamp are plausible and that there is no duplicate or stale
   pinned status message.
3. Record the visible result as `PASS`, `FAIL`, or `NOT FOUND`.
   Store one private, redacted screenshot if possible. Do **not**
   publish Telegram tokens, IDs, subscriber data, or channel secrets.
4. If the status cannot be found, keep the delivery gate open and
   investigate read-only Bot API/send-edit logs. Do not induce an
   electrical outage just to create a new notification.

The same check is required after candidate `0.5.13-hw` is safely
staged and approved for live testing. Passing the old release's
message check does not prove the uninstalled candidate's delivery.

## Final manual visibility check

The authorized channel administrator should open the target Telegram
channel and find the recovery/status message corresponding to the
natural outage #10 restoration on 2026-11-05, around 19:24 Kyiv time.

Confirm that the message displays the restored/online state, that its
timestamp and voltage are plausible, and that no duplicate or stale
pinned status message obscures it. Save a private screenshot or a
redacted acceptance note if an audit trail is needed. Do not paste
Telegram bot tokens, chat IDs, or other secrets into issues or logs.

If the message is missing, capture the observation and investigate the
bot's send/edit API results and channel permissions **read-only** before
considering a diagnostic message. Do not alter or simulate the physical
power state to reproduce an outage.

## 2026-11-06 — installed 0.5.13-hw operator acceptance

The owner authorized a controlled production installation of
`0.5.13-hw` on LXC 110 at 08:39 Europe/Kyiv. Real Shelly Pro EM-50
polling, Docker health/readiness, SQLite integrity and a private
0.5.12-hw rollback snapshot passed post-install tests. The Telegram
doctor reported API access OK, but this does **not** establish that
a new 0.5.13-hw status was visible or edited in the channel.

The channel administrator can now test `/status` and `/preview`
in the **private chat**. Check voltage/time accuracy, Ukrainian or
selected-language labels, clarity of connectivity versus voltage
restoration, and whether an existing visible status is reused or
edited without unexpected duplicates. Compare the operator's
visible result with actual new version timestamps. Do not simulate
a mains outage or insert synthetic voltage readings.

Record the visible result as PASS/FAIL/NOT FOUND and keep any
screenshot private. A bot doctor PASS, a restarted container or
a new database record is not proof of subscriber-visible message
delivery. Do not mark Telegram delivery fully accepted until the
operator has checked it.

## Release gate

The runtime is now `0.5.13-hw` under supervised production testing,
but the last **published** release is still `0.5.12-hw`.
The historical `LIVE_VERSION=0.5.6-hw` acceptance marker remains
unchanged. Public promotion is separate from installation and
requires the operator's visible-message acceptance. Pro 3EM/3CT63
support also remains pending physical testing on those models.
