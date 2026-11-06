# Optional low mains voltage warning (software design candidate)

This branch introduces only a pure-Python advisory state machine.
It is **not connected to the deployed Telegram bot** and cannot
control Shelly, send messages, create measurements or alter settings.

The observed power restoration on 2026-11-06 was followed by valid
Shelly readings around 204–207 V. The current critical threshold
configured in the live service remains 180 V, recovery 185 V.
A separate configurable early-warning tier may improve visibility.

Proposed opt-in defaults (feature disabled until explicitly enabled):
- Warning when readings remain **below 207 V for 180 seconds**
- Clear only after **210 V or higher for 120 seconds**
- Reset pending dwell when valid samples are more than 45 seconds apart
- Unknown/offline, stale, 0 V, NaN or infinite samples cannot trigger
  a new voltage warning or falsely mark an existing one as recovered
- Repeated or out-of-order timestamps never emit duplicate events

The 207 V design threshold is 90% of nominal 230 V, not a claim
of a legally enforced instant cutoff. A warning is an advisory,
not a definitive diagnosis or an instruction to switch equipment.

TODO before enabling on hardware:
- Add guarded settings/validation UI, persistence and audit trail
- Add independent deduplicated Telegram presentation with cooldown
- User-visible warning text must distinguish an actual measured
  undervoltage from a network communication loss
- Operator acceptance of a naturally occurring low-voltage event
- Preserve existing 180/185 critical-alert behavior and settings
