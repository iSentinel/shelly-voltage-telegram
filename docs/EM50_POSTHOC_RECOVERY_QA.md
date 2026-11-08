# Post-hoc EM-50 recovery telemetry audit (QA only)

A real power outage on November 8 2026 began as monitored Shelly
connectivity incident #18, and the production service resumed
recording after power returned. The event closed at 11:55 Kyiv.
The original outage baseline and private Telegram status-rotation
verification were not captured during this particular outage.

The existing connection-recovery-window.py acceptance correctly
REQUIRES those pre-event and Telegram rotation receipts. Do not forge
baseline evidence or claim that a database-only recovery check
substitutes for those prerequisites.

This standalone source QA script provides a narrower, independent
post-hoc audit of the actual persisted measurements:

    python3 scripts/em50-posthoc-recovery-audit.py --help

    python3 scripts/em50-posthoc-recovery-audit.py \
      --db /var/lib/shelly-voltage/voltage.sqlite3 \
      --incident-id 18 --json

The script opens an existing SQLite file with mode=ro. It never writes
to the database, sends Telegram, accesses a Shelly device or restarts
Docker. It checks that the requested event is the most recent,
that it is closed and 15 minutes have passed, and that the exact
same phase has valid samples throughout a full 15-minute window.
No gap may exceed 90 seconds. It rejects invalid voltage/frequency,
synthetic zero-voltage rows, future-dated samples, new outages,
and a missing/stale latest sample. SQLite quick_check must be OK.

Observed live LXC110 incident #18 acceptance:

- Event closed at 11:55:01 Europe/Kyiv.
- 89 real L1 samples collected during the next 15 minutes.
- Maximum intersample/boundary gap approximately 11 seconds.
- No invalid zero-voltage or future samples.
- A current valid L1 sample is available, with recent age.
- The tool returned status=pass, scope=telemetry_only,
  telegram_delivery_verified=false, release_authorized=false.

This tool intentionally has no Telegram delivery or release approval
logic. An operator-observed edited *private test message* is separate
from proof of public-channel status rotation during incident #18.
The latter remains unverified, and there is no invented baseline.

Tests cover dropped samples, wrong phase, unexpected new outage,
incomplete 15-minute history, stale/invalid/future samples,
bad incident timestamps, linked database and CLI non-authorization.

Regression:

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_em50_posthoc_recovery_audit.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
    bash tests/test_release.sh
