# 0.5.24-hw

The v5 card distinguishes Shelly connection from voltage severity. It
shows the active phases, uses the configured thresholds and language,
and displays the YASNO forecast separately from measured voltage.

- Preserve the Telegram status identity during text-to-photo conversion.
- Retry interrupted cleanup of known old posts after restart.
- Keep confirmed voltage alerts across restart and track both directions.
- Show stale readings as unavailable and calculate statistics for today.
- Save linked settings and their audit in one transaction.
- Require fresh readings on every active phase for readiness.
- Archive complete hours before pruning old measurements.

Graphical mode remains opt-in. Use `/graphic_preview` to review the card
in the administrator's private chat. See [graphical status](TELEGRAM_GRAPHIC_V5.md).
