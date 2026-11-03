## 0.5.10-hw - stable recovery field acceptance

- Extend `recovery-acceptance.py` with read-only `stable-baseline` and `stable-verify` modes.
- Capture the active 15-minute recovery timer, status-message fingerprint and audible cooldown timestamp without exposing the raw Telegram message ID.
- Verify after a natural recovery that the status message rotated, the recovery timer/event/episode context was cleared, valid measurements continued, and the audible cooldown timestamp did not change.
- Keep all field verification read-only: SQLite is opened with `mode=ro`, Telegram is not called, and no Shelly control RPC is used.
- Return stable PASS/FAIL exit codes suitable for CI and field evidence collection.
- Restore the 15-minute stability timer on upgrade when an older persisted `recovered` event and status message exist but the timer metadata predates `0.5.9-hw`; abnormal legacy events are not migrated.

## 0.5.9-hw - stable recovery status rotation

- Start a restart-persistent 15-minute stability timer on a confirmed voltage recovery.
- If no voltage status transition occurs during that period, publish a fresh current-status message silently.
- Publish the new status before unpinning and deleting the old recovery message, preserving the old post if publication fails.
- Clear the transient recovery banner and episode context only after the replacement succeeds.
- Cancel the stability timer on any new abnormal transition or confirmed offline state.
- Require every configured phase to remain online and inside the normal voltage range before rotation.
- Keep audible notification cooldown state unchanged by the silent replacement.

## 0.5.8-hw - event summaries, localization and notification diagnostics

- Add finalized voltage-event summaries using real SQLite samples for min/max/average.
- Track normalized low/high/normal transitions and time outside normal during the fixed edit window.
- Add persistent RU/UA/EN Telegram UI selection with immediate command/status refresh.
- Add administrator loud, silent and edit notification tests without changing real alert cooldown state.
- Add a persistent unstable-grid indicator after four confirmed transitions within 15 minutes, with an ON/OFF runtime setting.
- Include the new production modules in Docker, release archives, CI compile checks and the maintainability gate.

## 0.5.7-hw - Telegram status lifecycle and edit window

- Unpin the previous Telegram status before deleting it.
- Disable status pinning by default while keeping `Pin status` configurable in `/settings`.
- Remove inline buttons from the public voltage status and clean up legacy buttons at startup.
- Add a configurable 3/5/10 minute voltage status edit window; default is 10 minutes.
- Edit the active alert message in place through recovery while the edit window is active.
- Keep the original edit deadline through recovery and repeated transitions.
- Persist the edit window across restarts and reuse recent legacy events.
- Keep temporary edit failures from creating duplicate posts.
- Keep edit lifetime and audible notification cooldown independent.

## 0.5.6-hw - Pro EM-50 live acceptance and shared status alerts

- Add a standalone read-only Pro 3EM / 3CT63 acceptance CLI.
- Reuse the existing Digest-auth `Pro3EMClient`; do not initialize Telegram or SQLite.
- Validate complete L1/L2/L3 data from one atomic `EM.GetStatus?id=0` cycle.
- Reject missing phases, synthetic/invalid zero voltage, implausible voltage/frequency and RPC failures with stable PASS/FAIL exit codes.
- Support multiple samples, configurable bounds, text/JSON output and password-file authentication.
- Physical three-phase acceptance remains pending until the meter is installed.
- Add independent `RELEASE_VERSION` metadata and fail-closed final publication until it matches `VERSION`.
- Exclude `.ruff_cache`, `.pytest_cache` and `.mypy_cache` from public source archives.
- Software and live acceptance completed with 138/138 tests, CI-equivalent quality/ResourceWarning gates, release/install fixtures, Compose validation and candidate image build.
- End-to-end Pro 3EM HTTP acceptance smoke passed for three atomic L1/L2/L3 cycles; real three-phase hardware acceptance remains pending.
- Low-voltage and recovery transitions now appear at the top of the stable status message with event time, voltage and frequency instead of being sent as separate private messages.
- Apply the same stable-header low/recovery policy to L2/L3, persist the phase with backward-compatible L1 restore, and clear stale phase events when that phase goes offline.
- Add read-only recovery acceptance tooling that captures an outage baseline and verifies outage closure, stable-message rotation, readiness, new valid samples and absence of synthetic `0 V` after power returns.
- Add a read-only promotion checker that reuses the recovery result and also verifies the live version marker and `pro_em50` backend before an upgrade is allowed.
- Export current Prometheus voltage/frequency as `NaN` while a phase is `starting` or `offline`, preventing stale last-good samples from looking live during a blackout.
- Hide stale last-good voltage from the local dashboard while a phase is `starting` or `offline`; show the newest valid value only in `recovering`/`online`.
- Hide stale voltage/frequency from Telegram stable status while a phase is offline; recovering state shows only the newly received valid sample.
- Add one shared `scripts/quality.sh` gate for local development and CI, pinning Ruff 0.14.2 so formatting/lint rules cannot drift between environments.
- Redact deployment-specific connection error text and raw Telegram status message IDs from recovery/promotion verification output while preserving internal comparisons.
- Require recovery verification to match the exact captured outage by ID, phase and start timestamp before accepting closure.
- New recovery baselines store only a SHA-256 fingerprint of the Telegram status message ID; legacy baselines with the raw ID remain supported for in-progress outages.
- Warning/critical low and high voltage transitions now replace the shared
  Telegram status instead of creating separate alert messages.
- Publish the new voltage status before deleting the previous one, preserving
  the previous status if Telegram publication fails.
- Add a configurable sound cooldown with Off / 3 / 5 / 10 minute runtime
  choices; default 5 minutes, recovery stays silent and quiet hours override.
- Promote `0.5.6-hw` to the physical Pro EM-50 deployment after blackout
  recovery, read-only probe, Telegram continuity, SQLite and restart checks
  passed. Public `0.5.6-hw` publication remains a separate release step.

## 0.5.5-hw - supply-chain signing candidate

- Add keyless Sigstore Cosign signing for versioned GHCR images.
- Sign the immutable multi-architecture image digest after a successful push.
- Keep ordinary `main` images unsigned so release signatures remain easy to interpret.
- Pin the official `sigstore/cosign-installer` action to a full commit SHA.
- Add regression coverage for signing permissions, digest use and release-only conditions.
- Keyless signing is accepted with GitHub Actions RC2 run `41373145505` plus independent Gateway `cosign verify`; production remains on hardware-accepted `0.5.4-hw` because `0.5.5-hw` changes only the release supply chain.
- Final `v0.5.5-hw` release published from exact source SHA `f669dbb628854ea6bdbedd61d3b505fe613c93bf`; source assets and the signed amd64/arm64 GHCR image passed final release verification.

## 0.5.4-hw - 2026-11-02 — maintainability and upgrade audit

- Refactored large configuration, Telegram routing, settings keyboard and HTTP
  handlers into small, documented helpers without changing monitor behavior.
- Enforced PEP 8 formatting, a 79-character source-line limit and a maximum of
  40 lines per function across production code and tests.
- Added missing production docstrings and kept application logging on `loguru`.
- Removed repeated settings/response construction and duplicate dashboard assets.
- Fixed legacy upgrades where a missing or empty `SHELLY_METER_TYPE` could be
  written back as an empty value; it now safely defaults to `pro_em50`.
- Added regression coverage for both missing and empty legacy meter settings.
- Hardened metadata scanning so local `.ruff_cache` data is ignored.
- Added English, Russian and Ukrainian GitHub documentation plus a real dashboard
  screenshot captured from the live monitor.
- Revalidated release reproducibility, installer/rollback/support-bundle gates,
  SQLite schema v3 and an isolated real-hardware Pro EM-50 smoke test.
- Promoted `0.5.4-hw` to production after live Pro EM-50 acceptance;
  sampling, Telegram continuity, database integrity and container hardening passed.

## 0.5.3-hw - 2026-11-01 — generic multi-model Shelly support

- Added inexpensive single-phase Shelly models to the admin selector.
- Added read-only EM1, PM1 and Switch status backends.
- Added generic deployment configuration for meter type, control chat, status destination and admin IDs.
- Added publish guards that reject deployment-specific IDs, private IPv4 addresses and Telegram token-like strings from public release trees.
- Public release remains independent of any particular Telegram bot or Shelly installation.

## 0.5.1-hw - 2026-11-01 — Pro 3EM backend + recovery rotation

- Added native Shelly Pro 3EM/Pro 3EM-3CT63 triphase backend using one read-only `EM.GetStatus?id=0` request.
- Maps A/B/C atomically to L1/L2/L3 and skips missing/null phases instead of writing synthetic zeros.
- Added `SHELLY_METER_TYPE=pro_em50|pro3em`; legacy separate-sensor mode remains supported.
- Probe and doctor now understand both meter types.
- On confirmed primary recovery, delete the outage-era stable channel status and create/persist a fresh status post.
- The fresh recovery status includes the duration of the just-closed outage.

## 0.5.0-hw - 2026-11-01 — three-phase and advanced operations candidate

- Added SQLite schema v3 and phase-aware measurements/outages with lossless L1 migration.
- Added optional L2/L3 sensor configuration, independent states, polling, outages and alerts.
- Added three-phase Telegram status, HTTP health/dashboard and Prometheus labels.
- Added alert dwell/recovery hysteresis and configurable warning/critical levels.
- Added daily summary, voltage-quality statistics and outage history.
- Added PNG chart and CSV export commands without heavy runtime dependencies.
- Added maintenance mode, settings audit log, backup health and watchdog metrics.
- Added read-only Shelly firmware/uptime/time-skew diagnostics.
- Added hourly rollups, WAL checkpoint/optimize and conditional database VACUUM.
- Added optional automatic pinning and read-only buttons on the stable channel status message.
- Added independent credential secret files for future L2/L3 sensors.
- Hourly aggregates are guaranteed before raw-retention pruning.
- L2/L3 remain unconfigured until the two additional physical sensors are installed.
- Live promotion is blocked until L1 mains power returns for hardware acceptance.

## 0.4.11-hw - 2026-11-01 — separate Telegram status channel

- Added `TELEGRAM_STATUS_CHAT_ID` with fallback to the existing control chat.
- Commands and admin `/settings` stay in the private control chat.
- Stable voltage status is posted and edited in the dedicated status channel.
- Added independent `telegram_status_message_id` persistence for the channel.
- Telegram doctor now validates both control and status destinations.
- Added regression coverage for destination fallback, channel send target and separate status persistence.

## 0.4.10-hw - 2026-11-01 — admin Telegram settings

- Added `/settings` with inline controls for notification policy.
- Added `TELEGRAM_ADMIN_USER_IDS`; only configured admins may change settings.
- Non-admin users can view the current settings but receive no edit keyboard.
- Runtime changes are persisted in SQLite and apply immediately without restart.
- Added reset-to-environment-defaults control.
- Added callback-query polling and callback acknowledgements.
- Added regression tests for admin/non-admin authorization and persistence.

## 0.4.9-hw - 2026-11-01 — Telegram notification policy

- Ordinary Telegram `sendMessage` traffic is silent by default.
- Added configurable quiet hours; default 23:00-07:00 Europe/Kyiv forces all sends silent.
- Added dedicated low-voltage alert below 180 V; it may notify normally only outside quiet hours.
- Added 185 V recovery hysteresis and a silent recovery message to prevent alert flapping.
- Added regression coverage for quiet-hour boundaries, silent defaults and low-voltage one-shot behavior.

# Changelog

## 0.4.8-hw - 2026-11-01 — deployed / hardware accepted

- Add `telegram-discover` to validate the configured bot token and list pending Telegram chats without requiring a preconfigured chat ID.
- Keep token material out of discovery output and return a structured `waiting_for_update` result when no `/start` update is pending.
- Add regression coverage for successful discovery and the empty-update path.
- Preserve the prior live long-poll hardening: Bot API `getUpdates` uses 20 s long polling with 25 s HTTP timeout headroom.
- Live Telegram acceptance passed: doctor, stable-message edit, delete/recreate, command response, registered commands, and sustained command-loop polling.

## 0.4.7-hw - 2026-11-01 — deployed / hardware accepted

- Make `soak-report.py` open SQLite in read-only mode so a mistyped database path cannot create an empty database.
- Return a concise exit-2 diagnostic instead of a Python traceback when the report database cannot be opened or read.
- Add regression coverage proving a missing database is not created.

## 0.4.6-hw - 2026-11-01 — deployed / hardware accepted

- Fix first-install `--dry-run` on a clean Debian host: expected no-op branches now
  return success instead of inheriting a failed shell test under `set -e`.
- Fix the equivalent rollback no-current-DB no-op path.
- Add fresh-target and in-place installer dry-run fixtures and run them in CI.
- Add clean Debian 13 release acceptance before publication.
- Validate the fixed release on clean `debian:13-slim`: archive/manifests/privacy/
  shell syntax PASS; first-install dry-run reaches `DRY-RUN PASS`; bootstrap
  placeholder remains fail-closed.
- Pass isolated real-Shelly smoke and safe `0.4.5-hw -> 0.4.6-hw` promotion.
- Verify snapshot/rollback and post-promotion restart continuity; deployed installer
  fresh/in-place dry-run fixtures remain PASS.

## 0.4.5-hw - 2026-11-01 — deployed / hardware accepted

- Restore `last_outage_duration` from the latest closed SQLite outage after a
  process/container restart.
- Keep outage hysteresis and schema unchanged; this is observability persistence
  only.
- Add storage/startup regressions covering the newest closed outage when a newer
  outage is still open.
- Validate against an online copy of the real two-outage production database,
  including an isolated candidate restart.
- Promote safely from `0.4.4-hw`; verify snapshot/rollback, DB continuity and a
  production restart with `last_outage_duration` preserved exactly.

## 0.4.4-hw - 2026-11-01 — deployed / hardware accepted

- Clarify that outage journal durations are hysteresis-confirmed connectivity
  intervals, not exact mains-loss boundaries.
- Make `/outages` show confirmed-unavailability semantics and configured
  offline/recovery confirmation thresholds.
- Add Prometheus HELP text for outage counters/duration without renaming existing
  metrics.
- Add machine-readable `confirmed_outage_semantics` to the soak report.
- Separate software `VERSION` from the hardware-accepted `LIVE_VERSION` so a
  candidate cannot be mislabeled as already deployed.
- Strengthen recovery regression coverage: valid samples are persisted during
  recovery, while the outage closes only after the second successful poll.
- Remove the duplicated hard-coded runtime version: `app.py` now reads packaged
  `VERSION`, Docker copies it explicitly, and CI verifies CLI/source identity.
- Pass isolated real-Shelly smoke, safe `0.4.3-hw -> 0.4.4-hw` promotion,
  snapshot/rollback validation and post-promotion restart continuity.

## 0.4.3-hw - 2026-11-01 — deployed / hardware accepted

- Make `app.py version` independent of Shelly/runtime configuration so release and
  diagnostics tooling can identify an image without supplying `SHELLY_HOST`.
- Make `db-check` config-independent and strictly read-only: it no longer creates
  or migrates SQLite and returns exit 2 when the database cannot be checked.
- Keep `0.4.2-hw` unchanged while candidate hardening is validated separately,
  then promote only after a real-device hardware gate passes.
- Validate the candidate with 49/49 tests, release/install/rollback/support
  fixtures, isolated blackout smoke and real-device smoke before promotion.
- Make `/ready` report `shelly offline` on a cold-start blackout instead of the
  less-specific `no successful sample yet` once the offline state is confirmed.
- Promote `0.4.3-hw` to CT110 after mains recovery; preserve existing `.env` and
  SQLite history, verify the pre-upgrade snapshot, rollback dry-run and restart
  continuity, with zero synthetic `0 V` rows and no duplicate outage event.

## 0.4.2-hw - 2026-10-31 — deployed / hardware accepted

- Split Docker liveness (`/health`) from Shelly data readiness (`/ready`).
- Added `starting` / `online` / `recovering` / `offline` hysteresis.
- Restore the last valid measurement from SQLite after process restart.
- Suppress repetitive outage warnings while preserving transition warnings.
- Added Telegram edit throttling and `message is not modified` handling.
- Added bounded Telegram 429 retry and isolated long-poll HTTP session.
- Persist Telegram `getUpdates` offset and support optional user-ID ACL.
- Added `/start`, `/help` and `/outages` commands.
- Added persistent outage journal and safe SQLite schema v1 -> v2 migration.
- Added optional one-shot outage/recovery Telegram alerts with duration.
- Added outage counters/duration metrics.
- Validated offline -> restart -> recovery with an isolated fake Shelly.
- Fixed SQLite connection lifetime while preserving transaction commit/rollback;
  CI now fails on any `ResourceWarning`.
- Changed installer post-update gate from `/ready` to `/health` so device outages
  do not make healthy deployments fail installation.
- Added versioned pre-upgrade source + SQLite snapshots with SHA256.
- Added `scripts/rollback.sh` with rescue backup and a permanent rollback fixture.
- Validated online backup/restore against the live 2382-sample SQLite database.
- Passed the full CI-equivalent gate; the final publication-hardening suite has
  45 tests plus shell, installer, rollback, release, Compose and Docker checks.
- Added reproducible source release creation with fixed timestamps, public-file
  allowlisting-by-exclusion, internal file manifest and external SHA256 checksum.
- Added schema-aware soak diagnostics that keep sample gaps separate from confirmed
  outage events and expose sample span / latest-sample age.
- Extended support bundles with 24-hour soak/outage diagnostics and schema-v2
  redaction fixture coverage.
- Kept `doctor` functional when Shelly is unreachable, separating diagnostics from
  hardware availability.
- Sanitized Telegram transport exceptions so bot tokens cannot leak through request
  URLs in error messages or logs.
- Completed the 24-hour hardware soak with 26.947 h of real sample span,
  4538 valid measurements, SQLite `quick_check=ok`, and zero synthetic 0 V rows.
- Validated a real mains blackout: one persistent outage event, last-good sample
  retained, `/health` live, `/ready` 503, and no duplicate outage after restart.
- Validated online SQLite backup during the real outage and restart persistence.
- Hardened publication hygiene so operational validation/dist/roadmap-backup files
  cannot enter release archives or be accidentally committed.
- Added Shelly Digest-auth regression coverage and verified credentials are passed
  through the HTTP auth object rather than embedded in the RPC URL.
- Added a monitor-only safety regression that locks the production Shelly path to
  `GET /rpc/EM1.GetStatus` and rejects control/configuration RPC names.
- Removed deployment-specific LAN identifiers from public documentation and added
  a regression preventing private device IP/MAC leakage into public docs.
- Refreshed the pinned `python:3.13-slim` manifest digest after the previous
  registry digest became unavailable; publication builds now resolve again.

## 0.4.1-hw - 2026-10-30

- Wired `SHELLY_TIMEOUT_SEC` into the live RPC client and validation.
- Added portable unit tests for config, secret-file fallback, RPC timeout,
  SQLite aggregation/integrity, voltage levels and Telegram message recovery.
- Added GitHub Actions CI template for Python 3.11/3.12/3.13, shell syntax,
  Compose validation and image build.
- Added GHCR multi-architecture image workflow for amd64/arm64 with SBOM and
  provenance.
- Preserved the live hardware deployment and 24-hour soak collection.

## 0.4.0-hw - 2026-10-30

- First hardware-validated Shelly Pro EM-50 checkpoint.
- Validated local `EM1.GetStatus` on a physical `SPEM-002CEBEU50`.
- Added persistent SQLite history, health/readiness/metrics and Telegram logic.
- Deployed hardened Docker container in dedicated Debian 13 LXC 110.
- Validated container restart and full LXC reboot recovery.
- Added secret-file support, daily online backup, support bundle and soak report.
- Added idempotent local installer and future GitHub bootstrap script.

## 0.3.0 - 2026-10-30

- Software-only production-hardening checkpoint before real hardware access.
