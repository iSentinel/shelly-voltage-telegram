# Issue #402 — owner-gated signed release cutover plan

> **Superseded governance gates — 2026-11-17:** The sole owner has approved one-NVMe loss risk and sole-owner code review. Second-person review and off-host physical device provisioning are not active requirements. See [sole-owner release policy](ISSUE402_SOLE_OWNER_SINGLE_NVME_RELEASE_POLICY.md). Do not claim off-host backup verification or bypass signed OCI, fresh backup, single-writer, rollback, readiness or specific cutover/PHOTO approval.

**Current decision:** **NO-GO** for an official signed replacement.
The emergency Telegram text-ID hotfix is stable, but local runtime
success is not an authorization to merge, publish, reboot or update.

## Verified production state (2026-11-16)

- LXC110 is running exactly one `shelly-voltage-bot`, healthy and
  continually editing public Telegram [status #1130](https://t.me/svitlobot_yordanska_7/1130).
- Local emergency image ID:
  `sha256:b1b2b37a17f8fb014c35e4375c862a29a02020e33a1276cbe471d5025290af74`.
- Running `/app/app.py` SHA256:
  `e37d08836a1a1260506225e7c1f63c0bcac3a7b3d32da99f099e3366c56a7a42`.
- Compose and runtime both point to `shelly-voltage:issue402-v2-20261116`,
  but updater `deployed.json` still identifies official `0.5.21-hw`
  digest `sha256:f880ffc9748ad97a34a5d1bcf5c0ec25fb33793240de70501fd9c46651a00639`.
- GitHub `main` contains both `VERSION=0.5.21-hw` and
  `RELEASE_VERSION=0.5.21-hw`. **No new version or tag is approved**.
- The separate **on-host checkout** still says
  `VERSION=RELEASE_VERSION=0.5.15-hw` and has stale 
  `app.py` SHA256
  `2651a21d3a447e5e839730eaa0bddbddb5bfe9afa89ec9e69bad97d1d7791aea`.
  It is not the running 0.5.21-hw OCI source. Never derive the release
  version or rollback source from this stale directory.
- LXC110 systemd uses `apply-update.sh --recover` before
  `start-compose.sh`. Both the on-disk boot pin and any pending updater
  state matter; a standalone `docker run` test is not boot acceptance.
- Proxmox and LXC snapshots have matching hashes but are on **one
  physical NVMe**. Gateway exposes only that one server. This is not
  an independently protected off-host recovery copy.
- The read-only [runtime drift auditor](EM50_RUNTIME_RELEASE_DRIFT_AUDIT.md)
  reports `DRIFT` with `unpublished_local_image`,
  `updater_pin_mismatch`, `registry_digest_not_verified`,
  `checkout_version_mismatch`, `checkout_release_marker_mismatch`
  and `checkout_source_hash_mismatch`;
  it correctly returns `release_authorized=false`.

## Required review order

1. Review the small production-compatible atomic text ID fix
   [PR #403](https://github.com/iSentinel/shelly-voltage-telegram/pull/403)
   against its actual running v2 image; account for the eight additional
   logging lines in the PR. Verify its 5 send paths, 14 deterministic
   identity tests and media rollback protections, and keep graphic
   PHOTO mode disabled.
2. Review the read-only status/audit and field evidence in
   [PR #404](https://github.com/iSentinel/shelly-voltage-telegram/pull/404),
   and this runtime release drift audit in
   [PR #405](https://github.com/iSentinel/shelly-voltage-telegram/pull/405).
   QA PRs are deliberately separate from the production app change.
3. Treat the long sequence **PR #390–#401** as a stacked, unmerged
   security-hardening series with dependent base branches, not as
   a prerequisite to force-merge a Telegram hotfix. Rebase/merge
   individually only after independent reviewer and integration QA.
   A prior attempt to copy the full #401 app.py onto old runtime failed
   because it imported `writer_fence.py` absent from the base OCI;
   automatic rollback restored the original image.
4. Select the next release `VERSION`, `RELEASE_VERSION`, and
   `v<version>` Git tag **through Owner Approval**, not by guessing
   or reusing the unchanged 0.5.21-hw tag. The project image policy
   permits signed stable tags only when tag commit and both version
   files match.

## Release gate checklist

- [ ] Independently reviewed and merged exact app source and tests.
- [ ] Owner explicitly approves version/tag and release scope.
- [ ] Reproducible official GHCR OCI image at an immutable registry digest,
      with GitHub Actions identity and Cosign v3+ certificate verified.
- [ ] Source SHA attestation matches the actual image content.
- [ ] Shelly online, outage closed, database `quick_check=ok`, recent
      measurements and no pending Telegram publication.
- [ ] Two fresh WAL-consistent backups under a proven global writer
      freeze, one restored and validated **off-host** on independent
      physical storage. Old predeploy backup parity failure is expected
      while readings continue, not permission to rewrite metadata.
- [ ] Existing runtime, updater metadata, Compose overlay, **host
      checkout VERSION/RELEASE_VERSION/app.py** and rollback references
      are recorded with checksums and protected. Resolve stale checkout
      only through the reviewed, owner-approved release procedure.
- [ ] Verify `/var/lib/shelly-voltage-updater/pending-update` is absent;
      any regular marker, directory, or dangling symlink blocks approval.
      Never run the real `--recover` pre-start script just to clear
      the audit, since it can stop Docker and restore SQLite.
- [ ] Single-writer fence and systemd `--recover` behavior exercised
      with a disposable QA environment and interrupted-upgrade cases.
- [ ] Signed release staged and checked without second bot, manual
      public Telegram sends, or a PHOTO-mode switch.
- [ ] Owner expressly authorizes the guarded activation and rollback.
- [ ] After cutover: one healthy container, `/ready=200`, telemetry
      current, Telegram status ID matches text marker, public post
      edited successfully without duplicates, and read-only release
      drift audit is `PASS`. Passing the audit is not sufficient
      without the remaining release gates.

## Read-only preflight commands

```bash
python3 scripts/em50-runtime-release-drift-audit.py \
  --expected-app-sha256 <reviewed-64-hex-sha> --jsonl

python3 scripts/em50-release-preflight.py --json

python3 scripts/image_release_policy.py --help

/bin/bash scripts/quality.sh
```

The first checker returns **0=identity PASS**, **1=DRIFT**, **2=ERROR**;
even exit 0 carries `release_authorized=false`. The existing release
preflight also distinguishes fully automated checks from manual gates,
and currently reports `release_authorized=false`. Do **not** use
manually edited `deployed.json`, retagging of the emergency image, or
backups stored only on the same NVMe to bypass these controls.

## Rollback caution

Do not mechanically restore the original 0.5.21-hw container after
publishing a new Telegram status: the original code may reintroduce
the stale media-marker failure discovered in Issue #402. A rollback
must be reviewed for **message-ID compatibility**, restored SQLite
consistency and pending Telegram send uncertainty, with no concurrent
bot. Prefer the approved, source-matched last-good image and a controlled
single-writer transaction.

## Acceptance evidence

Actual power outage #32 and two independent 15-minute windows passed,
with publicly verified Telegram status #1130. See
[EM50_OUTAGE32_FIELD_ACCEPTANCE_20261116.md](https://github.com/iSentinel/shelly-voltage-telegram/blob/qa/issue402-readonly-identity-audit-20261116/docs/EM50_OUTAGE32_FIELD_ACCEPTANCE_20261116.md)
in PR #404. These results demonstrate correct natural monitoring and
Telegram rotation, **not** a completed official release or off-host
disaster-recovery acceptance.
