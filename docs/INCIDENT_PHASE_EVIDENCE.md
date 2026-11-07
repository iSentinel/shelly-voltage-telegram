# Same-phase evidence for power loss incident reviews

The read-only `scripts/incident-evidence-review.py` is used to combine
an operator's report of mains loss with a separately observed Shelly
*connectivity* gap. It does **not** prove the exact mains switching time.

When the incident is attributed to L1, its historical last valid voltage
must come from **L1**. A newer L2 or L3 sample from a three-phase Shelly
must not be labeled L1 evidence, even if it is within 0–500 V and has
plausible frequency. No available historical L1 sample is a **FAIL**;
the tool never substitutes other-phase telemetry or fabricates zero.

Usage:

```bash
python scripts/incident-evidence-review.py --help
python scripts/incident-evidence-review.py \
  --incident /path/to/operator-confirmed-incident.json \
  --db /var/lib/shelly-voltage/voltage.sqlite3 --json
```

The tool reads SQLite with `mode=ro`, does not contact Shelly or
Telegram, and never writes source data. Its outcome is evidence
correlation, not subscriber-visible Telegram delivery, physically
verified restoration, nor authorization to merge or deploy.

Tests cover a valid L1 with newer L2, missing L1 despite existing L2,
and invalid other-phase values. All use throwaway fixture databases.
