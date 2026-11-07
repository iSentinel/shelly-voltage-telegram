# Separate warning-level dwell without slowing critical alerts

This Draft PR makes the warning-level Telegram confirmation time
configurable **independently of critical alerts**.

## Existing compatibility

- Existing global alert dwell (ALERT_DWELL_SEC) continues to govern
  critical events and the separate 180 V low-voltage alarm.
- A missing new setting inherits the previously saved alert_dwell,
  not merely the environment's default. Existing behavior is unchanged.
- Normal recovery retains ALERT_RECOVERY_DWELL_SEC.
- Both L1 and optional L2/L3 share the same new warning dwell.
- Changing the old global dwell also updates the warning dwell
  **only while** it has not been separately customized.
- Invalid saved warning dwell falls back to the existing alert dwell.

## Telegram settings

The existing administrator voltage settings page gains localized
presets for warning confirmation: 15, 60, 180 or 300 seconds.
The selected value is saved through the existing audited metadata
path under telegram_setting_warning_dwell.

No new Telegram notification type is introduced: warning and
critical changes still use the existing status edit, cooldown,
mute, and delivery policy. No changes to DTEK, Shelly RPC,
hardware limits, or physical mains control.

## Validation

Run in a separate disposable checkout:

    python3 scripts/check-quality.py
    python3 -m unittest discover -s tests -p 'test_warning_level_dwell.py'
    python3 -m unittest discover -s tests -p 'test_*.py'

This is an opt-in change: the production bot remains unchanged
until isolated testing and explicit release review.
