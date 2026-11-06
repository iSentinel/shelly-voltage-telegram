## 2026-11-06 11:22 — correct production promotion block during Shelly outage

- **Production** remains installed `0.5.13-hw`, healthy but
  Shelly offline under open outage #13; /ready 503.
  Source main includes additive live/cached readings
  for L1/L2/L3 (PR #139, 437 tests and CI PASS) but
  these are not installed during the open outage.
- **Pre-deployment checks:** installer dry-run PASS, no
  changes made. Read-only source-candidate promotion
  check returned expected FAIL/exit 2 against the
  exact outage #13 pre-recovery baseline: no closure,
  no qualifying fresh data, no confirmed Telegram
  status rotation and not ready. No public release
  metadata was changed.
- **Backups:** 3/3 real retained daily archives verified
  checksum and SQLite restoration, latest within 36 h.
  Private preflight report remains stored with mode 0600.
- **Next:** verify actual natural Shelly recovery,
  stable status and subscriber-visible Telegram channel
  separately. User-estimated 17:00 Kyiv is not an
  actual verified restoration.

## 2026-11-06 11:18 — phase-safe JSON merged, outage #13 still open

- **Production:** installed `0.5.13-hw` with PR #128,
  still running/healthy without restart while Shelly
  offline, /health 200 and /ready 503. Last real reading
  10:30:08 Kyiv, outage #13 open from 10:30:51.
  SQLite checks and 3/3 backup archive checks PASS.
- **Candidate source:** PR #137 plus PR #139 provide
  backward-compatible `live_voltage`,
  `live_frequency`, `sample_is_stale` and
  `sample_age_seconds` for root status and
  independent configured L1/L2/L3 phases. Offline phase
  readings are null rather than stale voltage.
  PR #139 passed 437/437 isolated tests and CI for
  Python 3.11/3.12/3.13; merged as `f95a169`.
  This code is **not deployed** while the outage is open.
- **Pending release gates:** natural recovery of Shelly
  validated against private outage #13 baseline, separate
  stable 15-minute recovery status and visual Telegram
  channel acceptance, physical Pro 3EM/3CT63 tests,
  and owner approval before public release promotion.
  A tentative local schedule suggests restoration
  around 17:00 Kyiv; it is not a guarantee.

## 2026-11-06 — live outage #13 and stale-safe status candidate

- **Deployed:** `0.5.13-hw` on LXC 110, still healthy while
  the physical Shelly remained offline. At 10:45 Kyiv
  outage #13 was open, /health=200, /ready=503,
  database quick_check OK, 21,950 valid samples and no
  fabricated zero-voltage rows.
- **Source-only improvement:** PR #137 (`61b9f53`) exposes
  `live_voltage`, `live_frequency`, `sample_is_stale`
  and `sample_age_seconds` in status JSON, preserving
  existing last-known fields for backward compatibility.
  434/434 unit tests and GitHub CI Python 3.11/3.12/3.13
  PASS. Not installed while Shelly is offline.
- **Restoration expectation:** user-supplied approximate
  17:00 Kyiv schedule, not a guarantee. The existing
  hourly read-only recovery check was updated to use the
  valid outage #13 baseline instead of old #7 evidence.
- **Still gated:** accepted read-only recovery verification,
  subsequent separate 15-minute stable status check,
  operator-visible Telegram channel edits and explicit
  public-release approval. No live restarts or simulated
  electrical events.

## 2026-11-06 — live low-voltage recovery, release still in test

- **Installed production `0.5.13-hw`:** physical Pro EM-50
  naturally crossed the configured warning-low threshold
  and logged `warning_low` at 10:08:33 (205.0 V). No
  190 V critical-low crossing was recorded.
- **Connectivity event:** outage #12 lasted 190.4 seconds
  (10:16:51–10:20:01 Kyiv). It was a measured Shelly
  connectivity interruption, not proven electrical mains
  loss. Docker stayed healthy; readiness correctly changed
  to 503 during the outage and returned to 200 after recovery.
  Real recovery-hysteresis readings were preserved, SQLite
  remained healthy, and no synthetic 0 V records were made.
- **Source/CI:** PR #134 added deterministic 207/190 V
  threshold and recovery dwell regression tests; 432/432
  network-disabled unit tests and CI Python 3.11/3.12/3.13
  passed. No production deployment or restart was necessary.
- **Acceptance boundary:** internal event persistence and
  zero Telegram errors do not prove that the alert was
  displayed in the subscribed channel. Pre-recovery
  baseline was not successfully captured for outage #12.
  Public release metadata remains unchanged until channel
  review and explicit promotion approval.

## 2026-11-06 — Telegram transport recovery and source-only observability

- **Installed runtime:** `0.5.13-hw` with PR #128 remains the
  production-tested installation on LXC 110. Gateway checked
  Docker healthy, readiness 200, real Shelly online,
  SQLite integrity OK and no open outages. No new installation.
- **Observed network interruption:** twelve cumulative command
  polling failures (11 connection errors and one read timeout)
  occurred at about 09:48–09:49 Kyiv while Gateway SSH was also
  temporarily unavailable. Read-only Telegram API doctor passed
  at 09:52; no further transport failures were observed after
  09:50 at the 09:53 log inspection. These are not identical-edit
  errors and should not be interpreted as twelve ongoing faults.
- **Source-only improvements:** PR #132 improves backup freshness
  validation without changing retention checks (428/428 tests).
  New opt-in Telegram observability fields distinguish total
  polling failures, consecutive failures and time since the last
  successful poll (430/430 tests, Ruff PASS). These code
  enhancements are not yet installed.
- **Release boundary:** keep public `RELEASE_VERSION=0.5.12-hw`
  until channel-visible message/edit acceptance is complete.
  Owner-reported private-chat repeated-button test is PASS;
  public channel delivery under natural transitions remains pending.

## 2026-11-06 — installed 0.5.13-hw Telegram no-op edit hotfix

- **Deployed source:** `0.5.13-hw` with merged PR #128
  (`394f068`) on LXC 110. After the initial installation,
  a benign Telegram `message is not modified` response
  had raised three command polling warnings. The hotfix
  treats only unchanged text/markup edits as success;
  unrelated Telegram errors still propagate.
- **Regression:** 422/422 isolated tests PASS, Ruff quality PASS
  and GitHub CI Python 3.11/3.12/3.13 PASS.
- **Observed live state:** second installer PASS at 08:55 Kyiv,
  Docker healthy/0 restarts, Shelly online/50 Hz, readiness
  HTTP 200, SQLite integrity OK, 21,429 measurements by 08:58,
  zero artificial 0 V and no open outage. Watchdog errors
  after hotfix restart: 0. This is *not yet* independent live
  confirmation of repeated identical edits.
- **Rollback:** private pre-hotfix `0.5.13-hw` snapshot contains
  21,413 measurements; source/SQLite checksums, bounded
  restore and rollback dry-run PASS. The earlier `0.5.12-hw`
  snapshot also remains available. Neither rollback was executed.
- **Configuration:** the six recorded configuration/secret file
  hashes were unchanged. Status edit policy remains 10 minutes,
  status pinning off.
- **Outstanding acceptance:** administrator-visible Telegram
  `/status`, `/preview` and repeated callback tests, real
  channel visibility/edit checks, and further naturally
  occurring monitoring. Public `RELEASE_VERSION=0.5.12-hw`
  and historic `LIVE_VERSION=0.5.6-hw` have not been changed;
  production testing is not public release promotion.

## 2026-11-06 — 0.5.13-hw installed for owner-approved production testing

- **Running installation:** 0.5.13-hw from source commit
  `fd45c42fb01aad7035443a142bba4e9aa4bf56d5` on Proxmox
  LXC 110, installed at 08:39 Kyiv. Docker healthy, 0 restarts,
  HTTP `/ready=200`, Shelly Pro EM-50 online with live 50.0 Hz
  voltage readings. New measurements continue, SQLite quick_check OK,
  no open outages and no fabricated zero voltage samples.
- **Postinstall preservation:** all six monitored configuration
  and secrets files had unchanged SHA256 values. Telegram status
  edit window remains 10 minutes; default pinning remains off.
  Read-only bot doctor: Shelly, SQLite and Telegram all OK.
- **Pre-upgrade recovery:** private 0.5.12-hw rollback snapshot
  created during the installer; source/SQLite SHA256 checks PASS,
  bounded SQLite temporary restore PASS (21,315 samples),
  `rollback.sh --dry-run --no-start` PASS. No live rollback
  has been performed.
- **Release metadata:** `VERSION=0.5.13-hw` is the installed
  test build. `RELEASE_VERSION=0.5.12-hw` is still the public
  release; `LIVE_VERSION=0.5.6-hw` is a historical acceptance
  marker. Do not mark the candidate fully approved or publicly
  release it solely because installation and hardware polling work.
- **Pending operator check:** view `/status` and `/preview` in
  the administrator's private Telegram chat, inspect the status
  channel for visible text/edit correctness, and verify behavior
  during natural transitions. Telegram API OK does not prove
  subscriber-visible delivery.
- **Private report:** on-host 0600
  `validation/2026-11-06-post-install-0.5.13-hw-acceptance.json`;
  no private data added to this repository.

## 2026-11-06 — candidate rollback SQLite preflight complete

- **Candidate source:** `0.5.13-hw`, including merged PR #124
  (`a53bf7f`). The source installer now checks its freshly
  created SQLite backup, and rollback verifies a bounded temporary
  SQLite restore **before** any Docker or database changes. The
  earlier source archive safety and private-evidence protection
  remain intact.
- **Validation:** 419/419 tests PASS in isolated offline Docker,
  Ruff 0.14.2 and function-length gate PASS, and GitHub CI
  Python 3.11/3.12/3.13 PASS. A deliberately corrupt SQLite
  archive with a correct SHA256 fails before service-stop steps.
- **Real snapshot:** the private `0.5.12-hw` rollback point
  passed checksum, safe source inspection, SQLite temporary
  restore with 20,902 measurements, and rollback dry-run.
  No live rollback or candidate installation occurred.
- **Latest production Gateway observation (08:22 Kyiv):**
  installed `0.5.12-hw`, Shelly online, 238.2 V / 50.0 Hz,
  Docker healthy/0 restarts, readiness HTTP 200, SQLite OK,
  21,218 samples, 0 fake 0 V rows and no open outages.
- **Pending:** owner-approved production installation,
  independently verified `0.5.13-hw` Telegram delivery/edit
  and on-device behavior, plus a real controlled rollback
  acceptance. Do not mark the candidate hardware-accepted
  solely from source tests or snapshot verification.

## 2026-11-06 — pre-rollback archive validation and clean snapshot

- Production `0.5.12-hw` remains installed and healthy on
  LXC 110, with real Shelly online, HTTP readiness 200, zero
  Docker restarts, SQLite integrity OK, and no open outages.
- PR #120 merged: retain private operator config backups and
  acceptance reports during rollback, and keep them out of new
  source snapshots. The current 0.5.12-hw rollback source
  snapshot has 75 safe entries, with the required
  `.env.example` and no local `.env.*`, `validation/`,
  caches, links or build outputs.
- PR #121 merged: before stopping services, source archive
  contents are checked for traversal, absolute paths,
  symbolic/hard links, special files, privileged modes and
  excessive size. Extraction also skips operator-owned
  config and evidence from historical archives.
- PASS: both SHA256 snapshot sidecars and the actual private
  0.5.12-hw rollback `--dry-run --no-start`. Disposable
  restore of 20,902 backed-up samples previously passed.
  Malicious traversal/link fixture archives were rejected.
- PASS: 413/413 isolated full-suite tests, pinned Ruff and
  Python 3.11/3.12/3.13 CI for both source PRs.
- PASS: newest actual daily SQLite backup, 36-hour freshness
  and restore check (1/1), plus read-only current
  `0.5.13-hw` preflight against natural outage #11.
- **Not performed:** actual production rollback, 0.5.13-hw
  installation, its live Telegram send/edit/visibility
  acceptance, publication of a new release, or physical
  Pro 3EM/3CT63 validation.

## 2026-11-06 — secure rollback preparation and release packaging

- **Installed runtime:** production `0.5.12-hw`, healthy/ready,
  real Shelly online and no Docker restarts. `0.5.13-hw` is
  still a candidate; no install, rollback or public release was performed.
- **Rollback evidence:** an explicit, private `0.5.12-hw`
  source + SQLite snapshot passed SHA256, source-archive content
  checks, 20,902-sample temporary restore, SQLite quick_check and
  rollback dry-run. It retains public `.env.example` but none of
  the historical `.env.*` backups or private `secrets/` data.
  No real rollback has been attempted.
- **PR #117 merged:** the installer snapshot/source-copy pipeline
  now filters all private environment backups; 412/412 tests,
  Ruff and Python 3.11/3.12/3.13 CI PASS.
- **PR #118 merged:** public release packaging filters the same
  private files, rejects unsafe staged entries and passes a
  reproducible fixture with fake credentials; no package was
  published. Python 3.11/3.12/3.13 CI PASS.
- **Isolated candidate hardware simulation:** 0.5.13-hw source
  `77bf2b7` passed a fresh mock Pro EM-50 QA cycle in LXC 111,
  including 196 V/49.9 Hz sampling, two recovery cycles,
  offline `NaN` metrics, 162 samples, two closed outages and
  zero artificial 0 V rows. The temporary candidate was removed.
- **Remaining release gate:** owner-authorized 0.5.13-hw physical
  run, direct Telegram message/edit/visibility acceptance and
  controlled rollback procedure. A QA simulation and old release
  screenshot are not sufficient evidence for candidate promotion.

## 2026-11-06 — operator-visible Telegram recovery; source UX improvements

- **Operator visual check PASS for deployed 0.5.12-hw:** screenshot
  shows a Telegram channel recovery/status message, plausible voltage
  and frequency, a 6 h 19 min connectivity outage and an event summary.
  This confirms one visible message, not subscriber-wide delivery.
- **Separate timestamps:** Shelly communication recovered at
  06:37:02 Europe/Kyiv, while the recorded voltage recovery event
  occurred at 06:57:46 / 211.7 V. Summary 06:57–07:07 covers
  the time **after** normalization only.
- **Source-only 0.5.13-hw:** PR #114 changes the RU/UK/EN reconnect
  banner to indicate Shelly connectivity rather than claiming mains
  restoration; PR #115 adds a concise note about recovery-first
  event-summary coverage. Source checksum parity PASS.
- **CI:** full isolated suite 409/409 then 410/410 PASS, pinned Ruff
  quality PASS; GitHub Python 3.11, 3.12 and 3.13 CI PASS for both.
- **No deployment:** current 0.5.12-hw production healthy/ready,
  zero restarts, status edit window 10 minutes and pin disabled.
  Private operator-evidence JSON is retained on LXC 110, screenshot
  not added to GitHub.
- **Remaining gate:** candidate 0.5.13-hw still needs approved
  physical Telegram/delivery acceptance, exact-version rollback
  evidence and explicit owner authorization before installation.
  Real Pro 3EM / 3CT63 acceptance is a separate hardware task.

## 2026-11-06 — real Shelly restoration, outage #11

- Real Pro EM-50 connectivity outage #11 lasted from 00:17:46 to
  06:37:02 Europe/Kyiv. The deployed monitor `0.5.12-hw`
  automatically resumed polling, remained healthy with 0 restarts,
  and returned `/ready` HTTP 200.
- PASS: exact natural-outage recovery baseline comparison, new SQLite
  samples, SQLite integrity, 0 artificial 0 V records and Telegram
  status-message ID rotation. No Telegram send/edit was executed
  during acceptance; actual message visibility remains unverified.
- PASS: read-only `0.5.13-hw` candidate pre-promotion check
  against the real outage #11 baseline; 0 errors. Read-only
  Telegram/Shelly/SQLite doctor checks all returned OK.
- PASS: candidate installer and existing rollback scripts completed
  their respective dry runs. The installer proposes to create a
  new 0.5.12-hw rollback snapshot; the only currently validated
  retained snapshot predates this and is from 0.5.11-hw.
- The candidate has **not** been installed, released or approved.
  Human-visible Telegram delivery, a verified current-version
  rollback, owner approval, and Pro 3EM hardware testing are
  separate outstanding acceptance gates.

## 2026-11-06 — 0.5.13-hw backup freshness QA

- PR #111 merged `--max-age-hours` as an explicit opt-in
  read-only archive freshness check. Default archive verification,
  JSONL payload and exit codes remain unchanged.
- PASS: 408/408 Python tests in isolated offline Docker, Ruff
  and project maintainability gates, plus GitHub CI for Python
  3.11, 3.12 and 3.13.
- PASS: actual latest backup archive on LXC 110 passed the
  36-hour age check with SQLite integrity and retained samples;
  an intentional 1-hour limit failed as expected with code 2.
- PASS: checksum parity with exact merged `main` files.
  Results saved locally in a private, one-record JSONL report.
- Production remains at `0.5.12-hw`; real Shelly outage #11
  has not closed. `/ready=503` is expected. Read-only doctor
  reports Telegram API OK and SQLite OK; no message was sent.
- Physical 0.5.13-hw acceptance and Telegram recipient-visible
  message confirmation remain pending before a controlled
  update with rollback. No automatic promotion was performed.

## 2026-11-06 — isolated 0.5.13-hw synthetic hardware smoke

- Source commit: `fcf33e8570ede3905e3f8839d39a7861e3198eae`
  (`main` at time of test); image built only in isolated QA LXC 111.
- PASS: synthetic Pro EM-50 230 V / 50 Hz, a three-failure
  connectivity outage and automatic recovery. Repeated loss/recovery
  gave two fully closed outages and zero fabricated 0 V samples.
- PASS: five synthetic 196 V / 49.9 Hz samples and return to 230 V.
- PASS: offline Prometheus gauges changed to `NaN`; online gauges
  resumed. Final database: 232 measurements, SQLite quick_check OK.
- Telegram was disabled and no physical device or power source was
  manipulated. This is **not** evidence of real Telegram message
  receipt, pin/edit behavior or natural voltage-threshold acceptance.
- Isolated candidate containers/network/image were cleaned up,
  original QA monitor remained healthy (0 restarts); no deployment
  to production `0.5.12-hw`.
- Real Shelly outage #11 is still awaiting natural recovery and
  administrator-confirmed Telegram acceptance before a controlled
  `0.5.13-hw` promotion can be considered.

## 2026-11-06 — 0.5.13-hw candidate source verification

- `0.5.13-hw` is a **source-only candidate**, not a public release
  or installed service. Production remains on `0.5.12-hw`.
- PR #107 (merged) hardens archived backup checksum sidecars against
  wrong filenames, symlinks and oversized metadata.
- PR #108 (merged) adds opt-in, path-free `--jsonl` reporting while
  retaining normal CLI output and PASS/FAIL exit codes.
- Acceptance in an isolated offline Docker test environment:
  **404/404 unit tests PASS**, Ruff and maintainability checks PASS;
  both PRs passed GitHub CI for Python 3.11, 3.12 and 3.13.
- Three real archived SQLite backups passed read-only verification;
  their restored measurement counts were 20,589 / 17,414 / 14,501.
- Latest Gateway production inspection: Docker healthy with zero
  restarts, Shelly offline, `/ready=503`, outage #11 open,
  SQLite integrity OK and zero synthetic 0 V samples.
- No production restart, service configuration change, backup
  schedule change, Telegram message or Shelly write RPC was made.
- **Blocked until natural hardware recovery:** validate outage #11,
  fresh samples, 15-minute stable-status behavior, Telegram admin
  visibility and rollback safety before a separately approved
  `0.5.13-hw` promotion. Physical Pro 3EM testing is still pending.

## 2026-11-04 natural restoration evidence (partial hardware acceptance)

- PASS: a naturally occurring Pro EM-50 availability outage was recorded
  as SQLite outage id `6`, L1, from 18:49:40 to 19:46:30 Europe/Kyiv
  (56 minutes 50 seconds). The operator reported that power was off.
  These are *monitor-connectivity* boundaries, not independently verified
  electrical mains-on/off timestamps.
- PASS: at approximately 20:07 local time the device's local EM1 RPC
  returned HTTP 200 (236.8 V, 50 Hz), the Docker monitor was healthy,
  `/ready` reported fresh online measurements, SQLite `quick_check`
  was `ok`, zero-voltage measurements remained at 0, and no outage
  remained open. The database contained 16,689 measurements.
  The Telegram watchdog failure counter was 0 at inspection time.
- OBSERVED: the Telegram status-message fingerprint differed from the
  earlier 18:44 stable baseline and the persisted recovery context
  had been cleared. This is evidence of a changed status message, not
  proof of uninterrupted 15-minute stable recovery.
- PENDING: a separate open-outage baseline was **not** captured for
  event id 6 because Gateway's Tailscale regional executor was unavailable.
  The earlier stable baseline belongs to a prior voltage episode and must
  not be reused to promote the physical acceptance checkpoint.
- DONE: PR #44 added a read-only baseline validation guard that rejects
  a stable-verification result if the recorded outage changed since its
  baseline. Python 3.11, 3.12 and 3.13 CI passed. This tool update is
  in GitHub `main`, not deployed as a new physical release.
- NEXT: capture a fresh event-specific baseline during a future natural
  event and verify closure, status rotation, and continuous stability
  without issuing any control RPCs or artificially disconnecting mains.

## 0.5.12-hw — public release published (2026-11-04)

- Published release: https://github.com/iSentinel/shelly-voltage-telegram/releases/tag/v0.5.12-hw
- Exact release tag commit: `d5819ed9b110f887fc032c62dfadad70cff20b0f`.
- Source Release and multi-architecture Container Image workflows: PASS.
- Published archive SHA256: `b716d59efff5858a2b7dfef1b8ee9bf47dfd1b744920fd77b560a037f2feef44`.
- GitHub PR #39 contributed a read-only Shelly troubleshooting guide with
  PASS on Python 3.11, 3.12, and 3.13 CI.
- Local Pro EM-50 RPC was verified after DHCP reservation. A new Docker
  `/ready` result remains necessary to assert current monitor readiness.
- Independent image verification PASS: PR #41 merged reusable read-only
  verifier after Cosign identity check and direct OCI manifest/attestation
  inspection for both linux/amd64 and linux/arm64. Immutable image digest:
  `sha256:e725d628db6869c39225c5ff5a123bbd94399414fd8be15e13df8cfd9ab2fa46`.
  Each image config has release commit `d5819ed9b110f887fc032c62dfadad70cff20b0f`;
  each linked in-toto statement includes SPDX 2.3 or SLSA provenance and
  references its platform image digest. Workflow `41860089060` PASS;
  Python 3.11/3.12/3.13 CI `41860089059` PASS.
- Full natural voltage-transition hardware checkpoint remains `0.5.6-hw`.
- No production runtime, SQLite, or Telegram configuration was changed by
  these documentation updates.

## 0.5.12-hw - live validated / publication history

- Private `/start` menu, combined Status/settings and model-specific voltage
  topology are deployed. Pro EM-50 shows its single voltage phase as L.
- Live runtime source matches GitHub main `63c4e56a1eabd1374e38dbe9982008a2b4df04cf`.
  Main CI and Container Image completed successfully on that source.
- Local quality gate and 200/200 unit tests PASS; the device is reachable,
  live voltage/frequency readings are fresh, readiness and Docker health PASS.
- During an electrical-service interruption the device acquired a different
  DHCP address. Updating only SHELLY_HOST and restarting Compose restored
  connectivity. A permission-restricted copy of the previous .env was saved.
- SQLite quick_check PASS, no fake 0 V measurements, Telegram failures 0;
  measurement history and the existing service configuration are preserved.
- DONE: published `v0.5.12-hw` from the exact release-metadata commit; see current checkpoint above.
- Full electrical-transition hardware checkpoint remains 0.5.6-hw.
- Mini App preparation remains unpublished, as requested.

## 0.5.11-hw — public release independently verified

- Release commit: `391be951b85a98b32a82de423d277f6cff595a1d`.
- Source Release #9, tag CI #314 and Container Image #80: PASS.
- Source archive SHA256: `6a1c321682cdf8bf990e2d48fb0c5517076fb9a7d233d3af30aec28c66ca8abe`; published checksum and independent reproducible rebuild match byte-for-byte.
- GHCR digest: `sha256:df00b90a300ed27e0758554c5c1e2d99b1bc97b5b98f2505057f1a5d05f5a736`; linux/amd64 and linux/arm64 are present.
- Cosign v3.0.6 signature verification: PASS.
- SPDX 2.3 SBOM and SLSA provenance: PASS for both architectures; each SBOM contains 122 packages and provenance references the exact release revision.
- Live source is synchronized with the exact public tag; service remains healthy/ready with preserved SQLite and Telegram settings.
- Full electrical-transition hardware checkpoint remains `0.5.6-hw` until a natural threshold/recovery event is observed.

## 0.5.11-hw — live verified / release target

- Software candidate and production runtime: `0.5.11-hw`; public release remains `0.5.10-hw` until the exact release tag completes.
- `/settings` is reduced to five top-level sections with focused subpages; navigation edits the same message.
- Reset requires explicit confirmation; legacy settings callbacks remain compatible.
- Local gate: QUALITY PASS, 187/187 tests, clean Python 3.11/3.12/3.13 matrix, installer/rollback/support/release/Docker gates PASS.
- PR #34 CI and post-merge main CI/container workflows passed on exact source `0f0b5d691f6e8e608d33442e3fe5f42321c62f7b`.
- Live promotion created a verified rollback snapshot; source parity, health/ready, SQLite continuity and restart continuity passed.
- Live compact settings render confirms exactly five main buttons and persisted UA language; Telegram status ID, alert cooldown timestamp, edit window and cooldown settings remained unchanged.
- Optional Telegram Mini App support is deferred until a public HTTPS endpoint and server-side `initData` validation are available; existing health endpoints remain private.
- Full electrical-transition hardware checkpoint remains `0.5.6-hw` until a natural threshold/recovery event is observed.

# Release status

## 0.5.10-hw — live verified / release target

- Current software candidate and production runtime: `0.5.10-hw`; release metadata is promoted for public `v0.5.10-hw` publication.
- Adds read-only field acceptance for the natural 15-minute post-recovery status rotation introduced in `0.5.9-hw`.
- `stable-baseline` captures only safe recovery evidence, including a SHA-256 fingerprint of the Telegram status message instead of its raw ID.
- `stable-verify` checks message rotation, timer/event/episode cleanup, measurement growth, SQLite integrity, online/ready state and an unchanged audible cooldown timestamp.
- No Telegram API call, Shelly write/control RPC or live SQLite write is performed by the acceptance flow.
- Runtime startup restores a missing stability timer only for a persisted legacy `recovered` event with an existing status message; this closes the upgrade edge case found on the live `0.5.9-hw` database.
- Live promotion from `0.5.9-hw` restored the legacy recovery timer, silently rotated the stale recovery status, changed the status message while preserving the audible cooldown timestamp, cleared recovery/event/episode/edit-window context, kept SQLite `quick_check=ok`, zero open outages and zero synthetic `0 V` rows.
- Restart continuity PASS: the replacement status remained stable, recovery context did not reappear, and the pre-upgrade source/SQLite snapshot passed SHA256 and SQLite integrity checks.
- Physical acceptance remains pending until a natural voltage threshold/recovery event occurs; no electrical transition will be induced for this validation.

## 0.5.9-hw — software released / runtime verified

- Published software release and current production runtime: `0.5.9-hw`; full electrical-transition hardware checkpoint remains `0.5.6-hw`.
- After a confirmed voltage recovery, persist a 15-minute stability timer in SQLite.
- If no voltage-state transition occurs for the full period and all configured phases are online and normal, publish a fresh current-status post with `notify=False`, then unpin and delete the recovery post.
- Any new abnormal/offline transition cancels the timer; restart resumes it from persisted metadata.
- Telegram publication failure is fail-safe: the old recovery post and timer are retained.
- The silent replacement does not modify the audible voltage-notification cooldown timestamp.
- Live promotion from `0.5.8-hw` passed through the standard installer with verified rollback snapshot, exact merged-source parity, healthy/ready service, SQLite continuity, unchanged Telegram status identity and unchanged audible cooldown state.
- Post-promotion restart continuity passed with Shelly online, zero Telegram failures, no open outage and no synthetic `0 V` rows. Natural physical recovery observation remains pending; no electrical transition was induced for this software deployment.
- Tag `v0.5.9-hw` points to exact release commit `c92d86006f17441e42a0a33d0b4b7e56f213aa82`.
- Source Release run `41544388759`, Container Image run `41544388518` and tag CI run `41544388631` all passed.
- Published source archive SHA256 is `b73e4e958de960031cd4cac2be1cef842cf5c56c4ddd492e88e053b952ed31dc`; an independent rebuild from the exact tag matched byte-for-byte and the public archive privacy scan passed.
- Signed multi-architecture GHCR image digest is `sha256:eb563f8b8243b3edfefb4c977f2629adca41c35f4a40bd48bca45c231ce3f8bb`.
- Independent checksum-verified Cosign v3.0.6 verification passed for the exact tag workflow identity and GitHub OIDC issuer.
- GHCR contains `linux/amd64` and `linux/arm64`; both architectures expose SPDX document and SLSA provenance v1 attestation layers.
- Exact public release installation through `scripts/install-release.sh` passed with checksum verification, exact-tag source parity, a verified rollback snapshot and restart continuity.
- After exact release installation/restart, the service is healthy/ready, Shelly is online, SQLite `quick_check` is OK, no outage is open, no synthetic `0 V` rows exist, Telegram status identity/cooldown are preserved and no recovery timer exists without a real recovery event.

## 0.5.8-hw — software released / runtime verified

- Published software release and current production runtime: `0.5.8-hw`; the full electrical-transition hardware checkpoint remains `0.5.6-hw`.
- Tag `v0.5.8-hw` points to release commit `14f85134167b09c697a41c47f17077d9ef0a77b9`.
- Source Release run `41538097793`, Container Image run `41538097941` and tag CI run `41538097905` all passed.
- Published source archive SHA256: `f61d6221191983c84442decd595a08dbd10379ee1807f593bb96131eb7339494`; an independent rebuild from the exact tag matched byte-for-byte.
- Signed multi-architecture GHCR image digest: `sha256:36d061f76becac915f235f9af350bfe2f9ab9c9fee496f92db7f7c3fde231d27`.
- Independent Cosign verification passed for the exact tag workflow identity and GitHub OIDC issuer using checksum-verified Cosign v3.0.6.
- GHCR contains `linux/amd64` and `linux/arm64` images. Both architectures have SPDX 2.3 SBOMs and SLSA provenance v1 tied to release commit `14f85134167b09c697a41c47f17077d9ef0a77b9`.
- Public release installation through `scripts/install-release.sh` passed on the physical Pro EM-50 deployment; release checksum verification, restart continuity and source parity all passed.
- After the release install, the service is healthy/ready, Shelly is online, SQLite `quick_check` is OK, no outage is open, no synthetic `0 V` rows exist, and Telegram status message continuity is preserved.
- Adds event summaries, RU/UA/EN Telegram UI, notification diagnostics and restart-persistent unstable-grid indication.
- Software validation remains 168/168 tests PASS. The electrical-transition hardware checkpoint remains `0.5.6-hw` until a natural threshold/recovery event is observed.

## 0.5.7-hw — software released / runtime verified

- Published software release: `0.5.7-hw`; the latest full hardware checkpoint remains `0.5.6-hw`.
- Tag `v0.5.7-hw` points to approved source `874fc45d81bed3c41ac0005fd801d4e9db291611`.
- Source Release run `41526489280`, Container Image run `41526489577` and CI run `41526489500` all passed.
- Published source SHA256: `852ecc6f96a8334b0da26d81244bda6d354595d8833eafa01ac962edd70fdb89`; all 57 internal manifest entries verified.
- Signed amd64/arm64 image digest: `sha256:a0810c569a911c4e4a34f016cd4bf9495a7bd73e59764be3b0042baf5d55827b`.
- Independent Cosign verification passed for the exact tag workflow identity and GitHub OIDC issuer; SPDX SBOM and SLSA provenance were confirmed for both architectures.
- PR #22 merged with explicit user approval; source commit and deployed runtime match.
- Physical Pro EM-50, SQLite and Telegram doctor checks PASS; verified online backup saved.
- Recent normal-operation observation has no open outage or diagnostic polling gaps.
- Configurable 3/5/10 minute edit window defaults to 10 minutes.
- Recovery and repeated transitions preserve the first post's deadline.
- Runtime settings, current message ID and window start persist in SQLite metadata.
- Legacy databases retain history and can resume a recent stored voltage event.
- Sound cooldown and quiet hours remain independent of message lifetime.
- Software validation: quality gate PASS and 158/158 unit tests PASS.
- Runtime verification PASS: healthy container, ready endpoint, online readings, zero Telegram failures and unchanged status message after deployment restart.
- SQLite quick_check PASS; measurement history retained and collection resumed.
- Simulated voltage transitions do not constitute physical voltage acceptance.

## 0.5.6-hw — hardware accepted / release candidate

- Current software candidate: `0.5.6-hw`.
- Current public release: `0.5.6-hw`.
- Current production live checkpoint: hardware-accepted `0.5.6-hw`.
- Candidate/source, public release and live checkpoint versions are separated by
  `VERSION`, `RELEASE_VERSION` and `LIVE_VERSION`.
- Final source publication fails closed until `RELEASE_VERSION` is explicitly
  promoted to match `VERSION`.
- `0.5.6-hw` is promoted on the current Pro EM-50 after recovery, promotion, Telegram and read-only hardware checks passed.
- Read-only Pro 3EM / 3CT63 physical-acceptance CLI is implemented and software accepted; switching production to the `pro3em` backend remains pending physical device installation.
- Local CI-equivalent acceptance: Ruff + quality PASS, 138/138 tests PASS,
  using the same pinned `scripts/quality.sh` gate as GitHub CI;
  ResourceWarning gate PASS, installer/rollback/support-bundle/release-installer
  fixtures PASS, reproducible release PASS, Compose config PASS and candidate
  container build PASS.
- Candidate image passed a read-only physical Pro EM-50 probe and the Pro 3EM
  acceptance CLI passed three end-to-end HTTP cycles against a simulated
  `EM.GetStatus?id=0` triphase endpoint.
- Read-only blackout/recovery acceptance tooling is implemented; a real baseline for the current outage was captured successfully and remains local under `validation/`.
- Real blackout-soak acceptance PASS on the active outage: the outage ID and stable status message remained unchanged, measurement count stayed fixed, SQLite stayed healthy and no synthetic `0 V` rows appeared.
- Read-only `promotion-check.py` combines recovery verification, live version validation and the `pro_em50` backend guard into one fail-closed pre-promotion result.
- Recovery/promotion reports redact deployment-specific connection error text and raw Telegram message IDs, and recovery must close the exact captured outage identity (ID, phase and start time).
- New recovery baselines store a one-way status-message fingerprint rather than a raw Telegram message ID, while legacy baselines remain readable for existing outage checkpoints.
- Prometheus current voltage/frequency metrics are stale-safe: offline/starting phases export `NaN` rather than replaying the last good value.
- Local dashboard phase cards also hide stale voltage while offline/starting and resume display only after a new recovering/online sample exists.
- Telegram stable status hides last-good V/Hz while offline and shows electrical values again only after a new recovering/online sample is received.
- Rollback snapshot `20261102-212042-0.5.4-hw` source and SQLite checksums PASS.
- Post-promotion restart continuity PASS; shared Telegram status ID stayed stable, SQLite quick_check is ok and synthetic 0 V remains zero.
- Voltage warning/critical transitions now replace the shared status post; first abnormal post may notify, later posts use the configurable Off/3/5/10 minute sound cooldown.

## 0.5.5-hw

- Current public release: `0.5.5-hw`.
- Current production live checkpoint remains hardware-accepted `0.5.4-hw`.
- Runtime monitoring behavior is unchanged from the accepted 0.5.4 deployment.
- GitHub Source Release run `41373981210` published `v0.5.5-hw` from exact
  source SHA `f669dbb628854ea6bdbedd61d3b505fe613c93bf`.
- Source assets include `shelly-voltage-telegram-0.5.5-hw.tar.gz` and
  `SHA256SUMS`.
- GHCR run `41373982691` published the amd64/arm64 image with SBOM,
  provenance and keyless Sigstore Cosign signing.
- Final image digest:
  `sha256:c2115def9975dcfbb4a722d08713450403f53beaa01e6c54fec75962d82ce47a`.
- Independent Gateway `cosign verify` passed with the expected GitHub Actions
  workflow certificate identity and OIDC issuer.
- Production does not need an application upgrade for this supply-chain-only
  release.

Physical three-phase acceptance remains pending until a Pro 3EM / Pro 3EM-3CT63
is installed. Physical Digest-auth validation remains operator-controlled; the
monitor does not change Shelly authentication settings.
