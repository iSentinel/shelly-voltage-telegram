# EM-50 Telegram private-client acceptance — gated QA

The new candidate has a default-off public channel status/YASNO text
card. It still needs an actual client-visible Telegram send/edit
acceptance before release. A stored status message ID, getChat rights,
and unit tests are not client delivery receipts.

The separate scripts/telegram-private-acceptance.py is a manual,
explicitly gated private-chat test; it is not installed or scheduled.
By default it does nothing to Telegram. It never uses the production
channel chat ID or persisted message ID.

## Dry-run, no secret and no network

    python3 scripts/telegram-private-acceptance.py --help
    python3 scripts/telegram-private-acceptance.py --dry-run

An optional recipient/admin pair may be checked in dry-run. If
specified, both IDs must be the same positive user ID.

## Read-only live check: NO messages

After identifying a real administrator ID from the locally configured
TELEGRAM_ADMIN_USER_IDS, without displaying or recording it:

    python3 scripts/telegram-private-acceptance.py --check \
      --recipient-id "$PRIVATE_ADMIN_ID" \
      --admin-id "$PRIVATE_ADMIN_ID" \
      --token-file /run/secrets/telegram_token

This operation checks both getMe and getChat with Telegram. It
requires a matching identifier present in the trusted configured
TELEGRAM_ADMIN_USER_IDS list; group/channel IDs are rejected.
The returned chat must have type private AND an exact matching
numeric ID. No sending or edit methods are called.

Gateway acceptance on 2026-11-08 ran this script in the running
container through Python stdin without copying the script to the
container filesystem. It returned PASS CHECK exact_private_admin=true
writes=0. No Telegram message was sent.

## Future operator-approved private send/edit (NOT run)

Only after approval for a real private test recipient, run:

    python3 scripts/telegram-private-acceptance.py --execute \
      --recipient-id "$PRIVATE_ADMIN_ID" \
      --admin-id "$PRIVATE_ADMIN_ID" \
      --token-file /run/secrets/telegram_token \
      --confirm PRIVATE_EM50_SEND_EDIT_TEST

All safeguards are mandatory. Token is only read from a protected
0600 file. The command verifies bot identity and the exact private
chat BEFORE sending. It sends ONE silent, explicitly synthetic
EM-50 QA message, then edits only THAT newly created message using
its returned ID. No delete, pin or public post is permitted. There
is no retry on ambiguous send timeouts. A partial failure can leave
an original test message; never automatically post another.

Telegram API success is NOT proof of client visibility. The
operator must check the actual private chat and see the edited
text before marking the human gate PASS. Store only PASS/FAIL
and local timestamp, not tokens, private user IDs or screenshots
containing personal identifiers.

The installed production bot stays on 0.5.13-hw. The unpublished
0.5.14-hw candidate, its YASNO cache sharing and systemd jobs are
still QA-only. A separate signed release and rollback authorization
is required. Pro3EM/3CT63 physical tests remain nonblocking for
the existing EM-50-only text-card release.

## Regression

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_telegram_private_acceptance.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
