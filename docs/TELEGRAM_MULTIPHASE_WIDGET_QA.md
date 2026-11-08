# Three-phase widget and private preview — QA

The production monitor supports `pro3em` with independently tracked
L1, L2 and L3 state. The previous Telegram widget prototype showed
only L1. This QA change adds **independent** L2/L3 fields to the
public-data payload, plain-text private caption, and HTML preview.

- Optional `p` map contains exactly L2 and L3 when configured on
  the selected `pro3em` adapter. Other meter models do not add it.
- All three phases are bounded and freshness-checked independently.
  A phase is live only when connectivity is online, timestamp is
  within 60 seconds, and voltage is finite and 0–400 V.
- Offline, stale, or unconfigured secondary phases display `—`.
  No previous voltage is silently passed off as live.
- A disconnected L1 does not hide fresh L2 or L3.
- The payload only includes approved status/voltage/frequency/time
  fields. Unknown RPC keys, secrets or device identifiers are omitted.
- The HTML uses fixed labels L1/L2/L3, and shows secondary tiles
  only when a three-phase state exists. English captions use V/Hz;
  Russian/Ukrainian use their localized units.
- The widget sandbox HTML size is 9256 bytes raw, 3430 bytes zlib9.
  Limits: 32768 raw, 4096 zlib. Public data remains <=1024 bytes.

## Offline acceptance

```bash
PYTHONPATH=.:tests python3 -m unittest tests/test_widget_multiphase.py -v
PYTHONPATH=.:tests python3 -m unittest discover -s tests -p 'test_*.py' -q
scripts/quality.sh
```

Result: 1018/1018 offline tests PASS. This is not evidence of physical
Pro 3EM or Pro 3EM-3CT63 testing, recipient-visible Telegram rendering,
or approval to change production.


## QuickJS runtime acceptance (in GitHub Actions)

`tests/widget-sandbox-scenarios.ts` is copied inside a pinned upstream
Telegram widget-sandbox checkout and executed with its real QuickJS
`runWidget` runner, not a browser DOM mock. It probes the resulting DOM
for single-phase presentation, independent L2/L3 readings,
stale-secondary suppression and L1-offline isolation.

The matching workflow is `.github/workflows/widget-sandbox.yml`.
This verifies sandbox DOM behavior only; real Telegram iOS/Android
clients and recipient-visible delivery are still release blockers.
