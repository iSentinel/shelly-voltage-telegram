# EM-50 runtime release identity drift audit

This is a **read-only** QA check for the source/version inconsistency
exposed by the incident in [Issue #402](https://github.com/iSentinel/shelly-voltage-telegram/issues/402).
It compares the *running* bot image and application source against its
resolved Compose boot pin, the updater's last official release record,
and the host checkout's `VERSION`, `RELEASE_VERSION` and `app.py` SHA256.
It **never** authorizes deployment, publication, backup replacement or merge.

## Invocation

Use a SHA256 obtained from reviewed release evidence, **not** a freshly
copied, untrusted value from the running container:

```bash
python3 scripts/em50-runtime-release-drift-audit.py \
  --expected-app-sha256 <64-hex-digit-approved-source-sha> --jsonl

python3 scripts/em50-runtime-release-drift-audit.py --help
```

The default inputs are local Proxmox LXC110 deployment paths:
`/opt/shelly-voltage`, Compose's
`/var/lib/shelly-voltage-updater/override.yaml`,
`/etc/shelly-voltage/yasno-reader.yaml`,
`/var/lib/shelly-voltage-updater/deployed.json` and container
`shelly-voltage-bot`. Paths can be overridden for isolated QA. No network
requests, Telegram calls or database accesses are performed.

All Docker calls use bounded subprocess arguments with `shell=False`.
The audit parses the resolved Compose JSON in memory and never prints its
environment values, credentials, Telegram chat IDs or tokens.

## Interpretation

| Exit | Status | Meaning |
|---|---|---|
| 0 | PASS | Docker/Compose/updater/source identity appears consistent. **Not** an approved release. |
| 1 | DRIFT | A runtime identity mismatch or missing official digest was detected. |
| 2 | ERROR | The audit could not safely read or validate the evidence. |

The result always includes
`release_authorized=false`,
`signature_verified=false` and
`offhost_backup_verified=false`. Those gates are explicitly
**outside** this utility's verification scope.

For the current emergency local image, the expected result is **DRIFT** with
`unpublished_local_image`, `updater_pin_mismatch`,
`registry_digest_not_verified`, `checkout_version_mismatch`,
`checkout_release_marker_mismatch` and
`checkout_source_hash_mismatch`. Do not make these diagnostics pass by
editing the updater's `deployed.json` or by re-tagging an image. Both
would conceal provenance rather than prove it.

## Field validation: outage #32 emergency hotfix

On 2026-11-16 the running emergency image was
`shelly-voltage:issue402-v2-20261116`; image ID was
`sha256:b1b2b37a17f8fb014c35e4375c862a29a02020e33a1276cbe471d5025290af74`.
Actual reviewed `/app/app.py` SHA256 was
`e37d08836a1a1260506225e7c1f63c0bcac3a7b3d32da99f099e3366c56a7a42`.
Updater `deployed.json` still recorded `0.5.21-hw` with original
digest `sha256:f880ffc9748ad97a34a5d1bcf5c0ec25fb33793240de70501fd9c46651a00639`.

The live LXC110 checkout remains at `VERSION=RELEASE_VERSION=0.5.15-hw`,
with host `app.py` SHA256
`2651a21d3a447e5e839730eaa0bddbddb5bfe9afa89ec9e69bad97d1d7791aea`.
The running container and GitHub `main` are on the 0.5.21-hw release line.
Running the expanded checker against the live deployment returned exactly
**six** drift reasons above and `runtime_healthy=true`, with exit **1**.
At the same time, LXC110 remained healthy and had been editing public
Telegram [status #1130](https://t.me/svitlobot_yordanska_7/1130)
without duplicate messages. This is a **release bookkeeping/signing
problem**, not a runtime delivery failure.

## Required gates before an official release

1. Review and merge the atomic Telegram-ID source fix separately:
   [PR #403](https://github.com/iSentinel/shelly-voltage-telegram/pull/403).
   Preserve the exact approved code and reproducible tests.
2. Publish a correctly versioned and immutable OCI image with the
   project's signed tag policy and Cosign v3+ verification.
3. Make a fresh WAL-complete backup at cutover, test independent
   restoration on a **different physical failure domain**, and retain
   the old known-good image and rollback plan.
4. Apply the normal owner-approved updater procedure with a frozen,
   single-writer transition. Verify source SHA, pinned Compose image,
   updater metadata and actual runtime source after activation.
5. Conduct boot recovery/writer fence and recipient-visible Telegram
   acceptance before treating the release as production-ready.

A green identity audit is **necessary but insufficient** for signed
release acceptance. The live emergency image should not be restarted
or replaced merely to make the audit return PASS.

## QA commands

```bash
python3 -m unittest discover -s tests \
  -p 'test_em50_runtime_release_drift_audit.py' -v

/bin/bash scripts/quality.sh
```

Tests use synthetic Compose JSON and mocked Docker stdout, including a
sentinel secret to verify privacy. The expanded 17 tests were independently
run in LXC114 as root and as UID nobody. They also reject missing version
markers or a stale host checkout before an owner-approved cutover. No test contacts production.
