# Shelly Pro 3EM / Pro 3EM-3CT63 — offline three-phase readiness QA

**Hardware preparation only. No actual new device was connected.**

This draft is stacked on the still-unmerged issue #340 safety QA branch
(Draft PR #372). It does **not** change production or install a new
firmware/container. The current candidate source already contains
`Pro3EMClient.read_phases()` using a single read-only
`EM.GetStatus` RPC (id=0), phase-aware state and polling, and
`read_hardware_snapshot()` for preliminary hardware diagnostics.

This stage adds regression coverage to actual functions extracted
from the current candidate `app.py` source using Python AST.
No app entry point, real network, token, Telegram or hardware is used.
The tests execute the **actual extracted functions**, not a separate
rewritten parser. An injected stub supplies only synthetic RPC payloads.

## Acceptance matrix (synthetic)

| Situation | Expected |
| --- | --- |
| Valid a/b/c voltage and frequency | L1/L2/L3 preserved separately |
| One EM.GetStatus RPC | Exactly one `id=0` request per cycle |
| L2 voltage missing | L1 and L3 retained, **no fake L2=0V** |
| L3 frequency missing | L3 not fabricated |
| Explicit measured 0V/0Hz | Preserved as a received sample, never invented |
| Boolean/negative/NaN/550V | Invalid data rejected |
| `errors`, `b_errors` or `total_errors` | Device fault rejected |
| Unknown error text | Redacted as `unknown_error` |
| Invalid `total_errors` shape/non-dict RPC | Fail closed |
| RPC exception | Mark all phases missing, write no sample |
| Partial phase response in normal poll | Record only available phases |
| Hardware diagnostic probe, 3 phases | Three distinct phase values |
| Hardware diagnostic probe, 2 phases | Incomplete status rejected |

Device-specific `errors` and `total_errors` are both inspected
by the existing candidate error parser. These are synthetic fault
inputs, not claims about the actual firmware response from the
hardware that has not arrived yet.

## Test execution

On the exact offline LXC114 guest with only loopback, original signed
QA writer stopped/restart=no, and original QA systemd units disabled:

- **18/18** new three-phase source-extraction unit tests PASS.
- **179/179** combined stacked QA unit tests PASS under both root and
  unprivileged UID65534.
- Ruff format, E/F/I/UP/SIM, new functions <=40 lines, and
  source lines <=79 characters PASS.
- No external Shelly/Telegram requests, no production SQLite or
  Docker modifications. No test objects left running.

```bash
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p test_pro3em_phase_contract_qa.py -v
python3 -W error::ResourceWarning -m unittest discover \
  -s tests -p 'test_*.py' -q
```

## After the actual three-phase Shelly arrives

1. Record the **exact model name, firmware version, RPC model ID**,
   local IP and observed `EM.GetStatus` field names for its three
   phases. Do not send passwords or tokens in diagnostic logs.
2. In an isolated approved test setup, run
   `SHELLY_METER_TYPE=pro3em` read-only `probe` with **no
   Telegram writes**, compare the returned L1/L2/L3 against
   measurements visible in the Shelly app and local electrical meter.
3. Confirm correct CT orientation/phase mapping, voltage ranges,
   frequency and installed hardware alarms. Do not simulate mains
   or phase loss by manipulating live wiring.
4. Test synthetic missing phase, stale reading, total device
   disconnect, restored connectivity, three distinct phase
   outage/recovery records and no false-zero storage.
5. Verify status widget and Telegram notifications render the
   correct three separate phases, with no repeated artwork
   regeneration when the status is unchanged.
6. Only after full state backup/restore, independently reviewed
   all-owner G/B/W/A deployment, Telegram long-poll shutdown,
   power-loss acceptance and **Owner Approval** consider enabling
   this mode in production.

The existing EM-50 is to remain the default live source until
the separate three-phase hardware acceptance is approved.

**Production remains NO-GO for the proposed new release.**
The signed running OCI has not been replaced and the live
production database/restart policy/Telegram were not modified.
