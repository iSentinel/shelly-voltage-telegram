# Shelly hardware preflight (read-only)

Use `scripts/shelly-hardware-preflight.py` in a candidate source
checkout with the same Shelly environment settings as the deployed bot.
It sends only manufacturer **read-only** RPC calls:
`Shelly.GetDeviceInfo`, `Sys.GetStatus` and the relevant meter status.
It does not change the meter, Telegram, SQLite, service configuration,
relay state, firmware or notification settings. The probe validates
physical voltage, frequency and latency independently of the shared
validator: it is safe to run against existing production
`0.5.13-hw`, which predates that new QA-stage Python helper.

Run argument validation with zero device/network operations:

```bash
python scripts/shelly-hardware-preflight.py --model pro_em50 --dry-run --json
```

Run three actual telemetry samples with the configured credentials and
host (normally inside the future approved monitor container):

```bash
python scripts/shelly-hardware-preflight.py --model pro_em50 --samples 3 --json
python scripts/shelly-hardware-preflight.py --model pro3em --samples 3 --json
```

To run the QA-script source *without installing or replacing* any
production files, from the LXC that hosts the running container:

```bash
docker exec -i shelly-voltage-bot python - --model pro_em50   --samples 3 --json < scripts/shelly-hardware-preflight.py
```

This streams the script into an isolated Python process; the
already-running monitor container is not restarted or reconfigured.
Only use the `pro3em` adapter after connecting the corresponding
physical model. Do not run read-only hardware probes concurrently
at high rates.

`--help` documents all supported model adapter names. PASS means
that the selected RPC adapter returned a plausible, positive voltage
and 40–70 Hz frequency for all samples. Pro 3EM must provide
**all L1/L2/L3** phases from each atomic RPC. The report includes
device-reported model and firmware but deliberately excludes MAC,
host, usernames and passwords. Exit status 0 means PASS or dry run;
2 means FAIL. Sample count is bounded to 1–10, interval to 0–5 seconds.

This **does not** independently establish that the physical device
matches the selected adapter, electrical accuracy, stable 207–253 V
mains quality, subscriber-visible Telegram delivery, or physical
compatibility of Pro 3EM/3CT63 when those devices are unavailable.
Record actual hardware identifiers locally, but never publish MAC
or credentials in GitHub evidence.

Until an explicitly authorized release, the new preflight exists
only in the tested QA source candidate; the production
`0.5.13-hw` container is not assumed to contain this script.
