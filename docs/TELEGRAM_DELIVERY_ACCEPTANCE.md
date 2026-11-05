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

## Release gate

The production monitor remains on published `0.5.12-hw`. Candidate
`0.5.13-hw` has passed isolated, synthetic-sensor Docker tests, but
visible Telegram delivery and live hardware acceptance for that
candidate are still separate requirements. Do not turn on automatic
installation or promote a release solely because API access and
SQLite recovery checks passed.
