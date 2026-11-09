# Shelly hardware identity verification — QA only

The read-only `shelly-hardware-preflight.py` previously checked only
that `Shelly.GetDeviceInfo.model` was nonempty. A selected Pro 3EM
adapter could therefore report PASS while talking to a Pro EM-50.

## Pinned official identification table

| Adapter | Exact model identifiers | Source |
|---|---|---|
| pro_em50 | SPEM-002CEBEU50 | https://www.shelly.com/blogs/documentation/shelly-pro-em-50 |
| pro3em | SPEM-003CEBEU | https://kb.shelly.cloud/knowledge-base/shelly-pro-3em |
| pro3em | SPEM-003CEBEU63 | https://www.shelly.com/products/shelly-pro-3em-3ct63 |

When the selected adapter belongs to one of these two families,
any mismatching, missing, or unexpectedly suffixed model identifier
fails **before** phase measurements. The error never includes the
device-provided value. The `model_identity_verified` field reports
whether the selected model's identifier was checked.

For other supported models, independent identity attestation remains
**not implemented**: `model_identity_verified=false` even if a
read-only RPC sample succeeds. This must not be interpreted as
physical model acceptance. The existing
`physical_family_independently_verified=false` also remains.

Pro 3EM-3CT63 can be used with single- or three-phase installations,
but the currently selected `pro3em` monitoring adapter requires
all three readings; further hardware installation-mode acceptance is
pending. No control RPC, Telegram message, or database write is made.

## Validation

```bash
python3 scripts/shelly-hardware-preflight.py --model pro3em --dry-run --json
PYTHONPATH=.:tests python3 -m unittest tests/test_shelly_hardware_preflight.py -v
```

Actual physical testing of Pro 3EM and 3CT63 is still required before
production approval; these tests use mocked identity and phase RPCs.
