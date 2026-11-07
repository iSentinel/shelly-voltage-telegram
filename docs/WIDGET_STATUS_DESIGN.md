# Telegram widget and community outage source — QA prototype

This is a staged, opt-in design experiment. No Bot API sending code, production
configuration, Shelly polling or actual Telegram messages are changed.

## Proposed UX
- One prominent measured L1 voltage and frequency, visible only if the
  last Shelly sample is recent (60 s maximum) and connectivity is online.
- Connection status stays separate from electrical supply interpretation.
  Offline means no connection, not a proven outage; stale values become dashes.
- A 48-slot (30-minute) community outage forecast has its own timeline.
  It is labelled as unofficial and never generates Shelly power events.
- Use Telegram theme variables, no external fonts, images, libraries or
  requests; retain readable Russian text and support older-client fallback.
- The message caption must contain a meaningful plain-text current status for
  clients unable to render widgets. Production text messages remain primary.

## Protocol constraints
The upstream widget-sandbox describes messageMediaWidget (layer 233),
Bot API 10.4 (documented beta), profile 1 and SDK v1. Widgets are at most
32,768 bytes uncompressed and 4,096 bytes zlib-compressed. Shared public_data
is at most 1,024 UTF-8 bytes. Widget JavaScript has no direct network access.
Bot sends in private chats/groups; direct bot posting to channels is
not supported under the documented protocol. A channel can receive forwards.
Check Telegram client rollout and Bot API availability before any live test.

## Source boundaries
- Local cached JSON: Baskerville42/outage-data-ua (community mirror, not DTEK).
- Time zone: Europe/Kyiv, exactly today's data, max 2-hour snapshot age.
- Fail closed on errors, schema mismatches, unknown half-hour states or group.
- Run the adapter without a network call:
  python3 outage_data_ua.py --file /path/kyiv.json --region kyiv --group 10.1
- Generate a state string with telegram_widget_data.widget_public_data.
  The widget HTML reads it through TelegramWidget.publicData only.
- The existing DTEK official-source gate and independent hardware QA stay open.

## Validation
- PYTHONPATH=.:tests python3 -m unittest tests/test_outage_data_ua.py
- PYTHONPATH=.:tests python3 -m unittest tests/test_telegram_widget_data.py
- PYTHONPATH=.:tests python3 -m unittest tests/test_telegram_widget_html.py
- Run the official widget-sandbox conformance harness before rollout.
- Actual Bot API beta/private-chat send and recipient-visible acceptance are
  NOT covered by these static and Python tests.


## QA follow-up (8 Nov): stale schedules and fallback captions
- Public-state schema 1 carries \`l\` (RU/UK/EN) and \`e\` (end of the
  corresponding Europe/Kyiv local calendar day).
- The client revalidates measured voltage after 60 seconds and the community
  forecast after two hours **or** the next local midnight. An expired schedule
  disappears instead of being mistaken for a current grid outage.
- Widget captions use \`telegram_widget_fallback.widget_fallback_text\`:
  offline/stale voltage and old community windows cannot be displayed as live.
  This is a pure formatter, not a Bot API send operation.
- The HTML uses bot language and Telegram theme colors. Other clients retain
  the existing status text; no silent production or channel changes.
- Live Telegram widget runtime compatibility and Bot API beta still require
  explicit sandbox acceptance; these changes are Python/static QA only.
