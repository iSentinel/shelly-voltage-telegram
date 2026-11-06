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


## Read-only historical replay

A one-shot CLI verifies the proposed policy using existing SQLite
measurements without sending messages or writing any database rows.
It always simulates opt-in settings and does not change live settings.

    python scripts/warning-replay.py --help
    python scripts/warning-replay.py --db /var/lib/shelly-voltage/voltage.sqlite3 --hours 12
    python scripts/warning-replay.py --db /var/lib/shelly-voltage/voltage.sqlite3 --hours 6 --dwell-sec 300 --json

Limits: 1–24 hours, <=8,640 samples, read-only database access,
no symlinks, no background activity. PASS describes simulation only.
All reported event times use Europe/Kyiv.

**Important:** the existing Telegram alert-level mechanism already
has warning_low=207 V, alert_dwell=15 seconds and recovery_dwell=30
seconds, with alerts enabled on the observed live installation.
The proposed slower, optional state machine must *reuse* the existing
notification lifecycle if integrated; do not create a second public
notification for the same voltage transition.


## Proposed settings schema (not wired to the live bot)

Pure helpers policy_to_settings and policy_from_settings round-trip
six opt-in keys: early_warning_enabled, early_warning_below_v,
early_warning_clear_v, early_warning_dwell_sec,
early_warning_clear_sec, and early_warning_max_gap_sec.
The initial enabled value is 0. Unknown keys are ignored, but known
keys must pass strict type/range and hysteresis checks. This is a
**proposal for future audited SQLite metadata**, not a change to the
running Telegram settings UI, .env or production database.

overlaps_existing_alert flags the common case in which adding a second
channel notification would duplicate the existing 207 V level alert.
It does not disable existing notifications and makes no API calls.
