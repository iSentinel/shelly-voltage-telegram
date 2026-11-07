# 15-minute natural recovery: same-phase sample continuity

The existing stable-verify logic checked a healthy device, rotated
Telegram status, 15-minute elapsed timer, no new outage and a
normal-only voltage transition history. It also only checked
that the *total* SQLite measurement count increased. That is
insufficient for multichannel Shelly setups: readings from a
different phase, or data with multi-minute gaps, can falsely
pass while monitored phase L1 is stale.

Candidate change:
- Read only existing SQLite measurements for the **captured
  baseline outage phase**, bounded by the original stable-since
  timestamp and the real verification clock.
- Admit only plausible voltage (strictly between 0 and 500 V)
  and frequency (40 to 70 Hz), with timestamp no later than
  the observation clock.
- Require initial and consecutive readings no more than
  **90 seconds apart**, and a final sample no older than
  **45 seconds** at verification.
- Fail closed if no samples, the phase identifier is absent,
  the interval is invalid, or an extended telemetry gap exists.
- Keep all prior checks (15 minutes, unchanged outage, status
  replacement, transition history, no audible cooldown change).

The 90-second gap allowance accommodates several missed polls,
but does not prove voltage between samples. The acceptance
assertion is sampled continuity, not uninterrupted physical
mains supply or Telegram subscriber delivery.

Eight synthetic scenarios reproduce formerly false PASS cases
without network access, sending messages, or editing production
SQLite. Full physical and subscriber-visible acceptance must
still complete before release approval.
