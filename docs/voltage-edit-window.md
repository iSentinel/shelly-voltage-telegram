# Voltage status edit window

## User settings

Administrator `/settings` offers 3, 5 and 10 minutes. The default is
10 minutes, configured by `VOLTAGE_STATUS_EDIT_WINDOW_MIN`.
`VOLTAGE_NOTIFICATION_COOLDOWN_MIN` controls sound independently.

A confirmed voltage event creates a post and starts a fixed deadline.
Further events, including recovery and another low or high voltage event,
edit that post until the deadline. Recovery does not close the window and
edits do not extend it. The exact deadline is included. A transition after
the deadline publishes a replacement, then unpins and deletes the old post.
Without a separate status channel, the same policy uses the main chat.

Edits are silent and never update the last audible notification timestamp.
New posts still obey sound cooldown and quiet hours. Recovery stays silent.
If a temporary Telegram edit fails, the bot keeps the current message.
A confirmed missing or uneditable message may be recreated.

## Persistence and upgrades

The existing SQLite metadata architecture is retained. No table rebuild or
schema version change is needed:

| Metadata key | Meaning |
| --- | --- |
| `telegram_setting_status_edit_window` | Selected duration in minutes |
| `voltage_status_edit_window_started_at` | Original event time in seconds |
| `telegram_status_message_id` | Current post in a separate status chat |
| `telegram_message_id` | Current post when using the main chat |
| `last_voltage_notification_at` | Last audible send; independent of edits |

An absent setting uses the environment default. On upgrade, an existing
message and a recent persisted voltage event can restore a missing window
start. Expired events never receive a new deadline merely because the
service restarts. Periodic recreation of a missing status clears the old
window start. Invalid or future timestamps do not allow message reuse.

## Verification

`scripts/quality.sh` passed with Ruff 0.14.2. The complete unit suite passed
158/158 tests without ResourceWarning. Tests cover low to normal, low to
high, high to normal, repeated transitions after recovery, all durations,
the exact deadline, expiry, restart, legacy metadata, invalid timestamps,
missing messages, temporary edit failures, quiet hours and sound cooldown.

The candidate was deployed to the existing runtime. Readiness and container
health passed, readings resumed, Telegram reported no failures, the status
message remained unchanged across the deployment restart, and SQLite
quick_check passed. Electrical transitions were tested with simulated unit
events; a physical voltage-transition acceptance run was not performed.
