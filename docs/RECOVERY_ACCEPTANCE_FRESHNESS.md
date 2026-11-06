# Natural recovery: fresh same-phase evidence gate

An open Shelly connectivity-loss incident does not prove that mains
electricity switched off. Its closure also does not prove that the
power grid has recovered. Natural voltage samples are required.

Previously the full recovery-acceptance script could return PASS
when the global SQLite measurement counter increased, Telegram's
status message ID had changed, and /health and /ready were positive.
The counter was not phase-specific: a measurement from L2, a
stale L1 sample or an invalid frequency could still pass.

The verifier now obtains a consistent read-only SQLite snapshot
and requires a physically plausible sample (0 < V < 500 and
40 <= Hz <= 70) from the **same phase** as the captured outage.
Its timestamp must be within 45 seconds of observation, not in
the future, and at most 15 seconds before the recorded connection
recovery. This parallels recovery-preflight and does not alter its
PENDING decision while incident 15 is open.

The guard does not infer supply from group 10.1 schedules, never
creates sample rows and never sends Telegram messages. A PASS
from the local verifier does not prove Telegram delivery to a
subscriber or completion of the separate 15-minute stable status
acceptance. Physical acceptance is still required before release.

Seven synthetic read-only cases model incident 15 crossing Nov 6
into Nov 7. No production SQLite or running monitor is modified.
