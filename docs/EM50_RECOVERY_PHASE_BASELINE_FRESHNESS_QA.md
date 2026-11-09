# Require an actual new same-phase sample to accept outage recovery

## RED: another phase could falsely confirm L1

The recovery acceptance gate verified that total measurements increased
after the captured outage baseline, then checked whether the most
recent L1 measurement was close to the outage closure timestamp.
The total counter includes all configured phases. Therefore, a
brief L1 outage could close without any new L1 measurement while
a new L2 measurement increased the global count. If a pre-outage
L1 value was still within the existing 15-second closure tolerance,
the verifier could incorrectly return PASS with optimistic simulated
/health and /ready responses.

An isolated fixture reproduced this exact failure: it captured a
baseline with an L1 sample at 00:39:53, an L1 outage starting at
00:39:55 and closing at 00:40:00, then inserted **only** a new
L2 sample at 00:40:03. The previous verifier returned PASS.

## Fail-closed verification

The acceptance gate now requires the last valid measurement for
the captured outage phase to be strictly newer than the recorded
same-phase timestamp in its baseline. A missing or unchanged phase
timestamp is rejected even if another phase advanced the total
measurement counter. Existing same-phase plausibility, connection
recovery time, HTTP readiness, outage identity and Telegram stable
status rotation checks remain mandatory.

Legacy baselines that omit this timestamp retain their existing
time-window checks. Current accepted baselines contain the
same-phase timestamp, including the real L1 outage ID 20 baseline
captured during the 2026-11-09 blackout.

## Independent QA

- RED: 1 unsafe L2-only recovery incorrectly returned PASS.
- GREEN: it now fails with an explicit same-phase baseline error.
- Existing valid L1 recovery still PASS.
- Freshness suite 12/12 PASS; recovery acceptance 32/32 PASS.
- Source-only full Python 3.13 suite: 1311/1311 PASS.
- Ruff 0.14.2, project quality, Bash syntax and the four
  installer dry-run fixtures PASS.
- Separate PR #291 cumulative installer branch requires independent
  disposable integration validation.

This is a Draft source-only acceptance fix on PR #293.
It does not manipulate the real outage, Shelly, Telegram, Docker,
or the production SQLite. The release remains blocked on
independent human source review, exact quiesced SQLite/source
backup, protected YASNO producer/reader, Telegram real-client
visual acceptance and signed deployment/rollback authorization.
