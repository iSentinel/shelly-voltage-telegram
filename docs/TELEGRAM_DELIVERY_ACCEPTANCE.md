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
