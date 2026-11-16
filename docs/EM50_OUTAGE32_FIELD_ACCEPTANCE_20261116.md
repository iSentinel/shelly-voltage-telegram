# EM-50 natural outage #32 — field acceptance evidence

**Date:** 2026-11-16 (Europe/Kyiv). **Incident:** #32, L1. **QA status:**
connection recovery **PASS**, full 15-minute connectivity continuity **PASS**,
separate 15-minute normal-voltage stabilization and Telegram cleanup **PASS**.
This field report does **not** approve an official software release.

## Environment and source identity

The production service was a single `shelly-voltage-bot` in Proxmox LXC110.
The emergency local image `shelly-voltage:issue402-v2-20261116` had image
ID `sha256:b1b2b37a17f8fb014c35e4375c862a29a02020e33a1276cbe471d5025290af74`.
Deployed `/app/app.py` SHA256 was
`e37d08836a1a1260506225e7c1f63c0bcac3a7b3d32da99f099e3366c56a7a42`.
The original image came from the pinned 0.5.21-hw OCI digest, and the
emergency patch changed only application text-status ID binding paths.
The local emergency image **has no signed registry digest**. GitHub [PR
#403](https://github.com/iSentinel/shelly-voltage-telegram/pull/403)
contains the source fix, but was still unmerged at this checkpoint.

During QA, production was not restarted, artificially disconnected,
modified in SQLite, or forced to send Telegram messages.

## Natural event timeline

| Kyiv time | Grounded observation |
|---|---|
| 13:39:32 | Last historical sample before loss, 213.7 V. |
| 13:40:14 | Outage #32 opened in SQLite. |
| 16:38 | Shelly offline, Docker healthy, `/ready=503`, status marker matched public Telegram #1124. |
| 17:03:53 | Outage #32 closed after real connectivity resumed. |
| 17:05 | Initial `recovery-acceptance.py verify` PASS; public [#1126](https://t.me/svitlobot_yordanska_7/1126) announced power return, [#1127](https://t.me/svitlobot_yordanska_7/1127) was the new status. |
| 17:13:40 | First normal-voltage stable baseline. The meter later dropped below normal, invalidating this timer. |
| 17:18 | Public #1128 displayed low voltage 206.1 V. Between 17:09 and 17:21, meter ranged 204.7–216.5 V, 31 samples were below 207 V. |
| 17:19:50 | Strict connection-window `status=pass`: 15.96 minutes, 89 L1 samples, maximum sample gap 10.52 s (limit 90 s). |
| 17:21:06 | New genuine voltage-normal event reset stable timer; [#1129](https://t.me/svitlobot_yordanska_7/1129) reported recovery. |
| 17:36:40 | `stable-verify` PASS: 15 minutes normal voltage, recovery timer and event/episode context cleared, status rotated. |
| 17:36:49 | Public [#1130](https://t.me/svitlobot_yordanska_7/1130) was the newly created ordinary text status; current DB ID and media marker both 1130. |
| 18:36–18:37 | Later Gateway/Telegram recheck: Shelly online 222.0–222.7 V, frequency 50 Hz, fresh sample, `/ready=200`, Docker healthy with zero restarts, SQLite `quick_check=ok`, 79,147 measurements, ID/marker 1130, no pending publication or new outage. |

## Protected QA artifacts

The following **private** JSON artifacts were captured under
`/root/issue402-hotfix-20261116/natural-recovery-baselines/` in LXC110.
Sensitive JSON contents are intentionally not copied to GitHub.
Field evidence was compared with protected copies under
`/root/shelly-issue402-recovery/natural-outage-32/` on Proxmox.
Both locations currently share **one physical NVMe failure domain**.

| Artifact filename | SHA256 |
|---|---|
| `outage-32-1794829214655.json` | `f29a37153f0c0806da1aabd5c08f808099119f59b3b9d389feaeb790a4cdd5` |
| `verified-outage-32-20261116.json` | `e5c2f02469ea935a15c26da2c9a7455357a5a2062c2bd6e834efcef90aa50184` |
| `connection-window-pass-outage32-20261116.json` | `ddd6215a15b95d9fca7b66c0368e332fb38834634eb13bef310cb2b5e841f1ac` |
| `stable-voltage-outage32-172106.json` | `047969050f14a571230af0e0e33cce75ab9e3e4d53cdb543de5a44cf5883ae80` |
| `stable-voltage-verified-outage32-172106.json` | `dde5f50b90798d285629f831272ef431ff7ea6cb022f132a6c3035b8ee49c7f9` |

All saved evidence files have mode `0600`. They contain fingerprints
rather than raw Telegram chat identity. Check names and actual hashes
before trusting any archival copy. A checksum is **integrity evidence**,
not a signature or independent physical backup.

## Reproduction and limitations

For a current event within the verification window, use
`scripts/recovery-acceptance.py` to take an outage baseline and verify
recovery. Run `scripts/connection-recovery-window.py` after 15 minutes,
and separately use `stable-baseline` / `stable-verify` for an authentic
normal-voltage transition, as documented in
[TELEGRAM_STATUS_IDENTITY_AUDIT.md](TELEGRAM_STATUS_IDENTITY_AUDIT.md).

**Important:** `connection-recovery-window.py` can return exit code
`0` while JSON `status=pending`. Only JSON `status=pass` accepts a
completed window. Likewise, `stable-verify` intentionally rejects a
fresh recheck more than one hour after the original voltage-normal event.
The saved 17:36:40 PASS remains contemporaneous proof even though a
live recheck after 18:21:06 refuses old evidence. Do not loosen the
one-hour bound or overwrite the original PASS.

Public Telegram posts prove visible delivery independently of SQLite
metadata, but do not prove that every subscriber received a push
notification.

## Remaining release blockers

- Publish an exact-source, tagged and Cosign-verified immutable OCI
  under explicit Owner Approval; PR #403 is not itself an authorized release.
- Reconcile the updater's 0.5.21-hw `deployed.json` to the official release
  only through a fully audited controlled deployment, preserving rollback.
- Obtain a fresh, WAL-complete, independently **off-host** backup and test
  restoration on an independent failure domain. Current copies share one
  Proxmox NVMe, and the release preflight correctly reports stale
  predeployment backup parity after ongoing measurements.
- Complete independent writer-fence, crash/boot update continuity, and
  emergency rollback acceptance. Keep graphic PHOTO migration separate.

**Outcome:** natural electricity-loss recovery and Telegram text-status
rotation are accepted for outage #32. **Official production release remains
NO-GO** until the above gates have independent evidence.
