# Issue #402 — natural recovery audit: exact and grace-window samples

**Source-only QA change.** The application and Shelly polling state machine
are unchanged. The read-only `em50-natural-recovery-audit.py` must not
claim that the whole recorded outage interval had zero measurements when
the final successful polls occur before the outage is officially closed.

## Recorded field evidence, 17 November 2026 (Europe/Kyiv)

- Incident **#33** lasted 10:01:49–12:27:32. One valid L1 sample
  (220.1 V) was recorded approximately 10 seconds before the closing
  timestamp, during confirmation of restored connectivity.
- Incident **#34** lasted 14:36:27–14:36:48 (21.1 s). One valid
  L1 sample (219.5 V) was recorded at 14:36:38, between the recorded
  start and end. The 60-second grace interval covers the whole event.
- No artificial zero-volt readings were observed; the natural recovery
  health, readiness and status identity checks passed. A recorded
  communication incident **does not prove a physical mains outage**.

The production `accept_phase_sample()` stores a successful reading
**before** it changes state to online and closes the event after the
configured number of successes. A real measurement can therefore
occur inside the recorded incident while connection recovery is
being confirmed. The previous auditor's
`measurements_during_offline=0` referred only to the interval
**before** the trailing grace window and was ambiguous.

## Corrected read-only report

`measurements_during_offline` now counts **every** sample whose timestamp
falls within the exact start/end boundaries for the event's phase.
It may be nonzero for a valid recovery.

New explicit fields:

- `measurements_before_recovery_grace`: samples between incident start
  and the final allowed recovery-grace interval. This is the count
  checked by the existing recovery PASS/FAIL predicate.
- `measurements_in_recovery_grace`: samples within the trailing
  grace interval and before the event's closing timestamp.
- `grace_covers_whole_incident`: true if the event duration is no
  longer than the configured grace window.

The existing zero-voltage guard and fresh-sample check remain enforced.
The new metadata does **not** authorize early recovery, fabricated
data, Telegram delivery or a production cutover. The auditor remains
SQLite read-only and emits neither messages nor write operations.

## Safe verification

```bash
python3 scripts/em50-natural-recovery-audit.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 --json

python3 -m unittest discover -s tests \
  -p test_em50_natural_recovery_audit.py -q
```

Synthetic test coverage includes a 21-second event entirely within
grace, a long event with one legitimate grace-window reading, and
an unexpectedly early sample that **must fail** recovery acceptance.
Run offline with disposable SQLite; do not test against a writable
production database or restart the live bot.
