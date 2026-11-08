# Shelly Pro EM-50: standard Telegram channel status with YASNO

This is an optional standard **Bot API text-message enhancement**,
not a native Telegram HTML widget. It keeps the existing pinned
status message identity and its sendMessage/editMessageText update
and recovery rotation logic. The existing statistics, buttons,
power event context and per-phase measurements remain untouched.

The enhanced status appends a short, separately attributed
YASNO forecast for the DTEK Kyiv operator from an already
verified private cache. It never fetches the provider during
Telegram delivery and never converts a forecast to a physical
power event. The source is labelled YASNO / DTEK Kyiv, not
the protected DTEK website.

## Safe default and proposed settings

The feature is **disabled unless explicitly opted in**:

    TELEGRAM_YASNO_STATUS_ENABLED=1
    YASNO_TRUSTED_CACHE_FILE=/protected/mounted/path/yasno.json
    YASNO_GROUP=10.1

When the feature is disabled, the original Telegram text is
byte-for-byte unchanged. If the cache path is absent, no forecast
is appended. If the trusted cache is present but corrupt,
expired or has an unpublished schedule, a localized "schedule
unavailable or expired" line is used instead of invented outages.

The formatter accepts only a fresh verified local schedule.
Definite outages and possible outages are displayed separately;
an all-green forecast is described as "no planned outages",
never as actual electricity being present. Line count is
bounded, and adding forecast text cannot exceed Telegram's
4096 UTF-16-unit conservative message limit. When a message
is too long, the original status remains unchanged.

## QA preview and tests

    PYTHONPATH=.:tests python3 -m unittest tests/test_channel_yasno_status.py -v
    PYTHONPATH=.:tests python3 -m unittest discover -s tests -q
    scripts/quality.sh

A read-only test preview can call
telegram_channel_yasno.append_yasno_forecast with a synthetic
existing status and the protected test cache. It never sends
a Telegram request.

## Conditions before a production rollout

1. Review and merge the complete dependent QA change chain
   through the channel-fallback candidate. Avoid cherry-picks
   that omit a required validator or localization update.
2. Assemble and sign a new versioned image/release and retain
   the current working production 0.5.13-hw image and settings.
3. Verify the real bot container can READ the protected cache
   through a reviewed UID and read-only mount; preserve 0700
   directory and 0600 cache permissions. Do not chmod 777.
4. Install a protected refresh service only after unit/security
   review. The repository .example service/timer files are
   deliberately inactive.
5. Confirm the status message in an explicitly approved private
   test chat on an actual Telegram client; test update, outage,
   recovery, stale YASNO cache and unsupported widget fallback.
6. Explicitly approve one low-risk production rollout with a
   database backup and no message history deletion.
7. Observe health/ready, telemetry freshness, Telegram failures
   and status edit results after the controlled rollout.

Rollback: turn off TELEGRAM_YASNO_STATUS_ENABLED or restore the
previous image/configuration; leave physical Shelly telemetry,
SQLite samples and prior Telegram message IDs intact.
Disable the scheduled YASNO refresh if required using
systemctl disable --now shelly-yasno-refresh.timer.

## Outside this EM-50 release

- Native Telegram HTML widgets in a public channel require
  stable client/Bot API support; passing QuickJS tests does not
  mean the client can publish such messages.
- Physical Shelly Pro 3EM / 3CT63 tests remain separate and
  do not need to delay this EM-50-only release.
- Independent YASNO attribution must always be visible.
