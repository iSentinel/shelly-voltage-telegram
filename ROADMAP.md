# Roadmap

Status date: 2026-11-02
Runtime candidate: `0.5.7-hw` (software and operational checks passed)
Latest full hardware checkpoint: `0.5.6-hw`
Public release: `0.5.7-hw` (source and signed multi-architecture image verified)


## Current next steps

- DONE: merge the persistent edit-window implementation (PR #22) after explicit user approval.
- DONE: confirm physical Pro EM-50 readings, Telegram diagnostics and SQLite integrity.
- DONE: confirm no open outages or synthetic zero-voltage rows; save a verified online backup.
- DONE: software validation with 158 unit tests and Python 3.11/3.12/3.13 CI.
- DONE: publish v0.5.7-hw from approved source 874fc45d81bed3c41ac0005fd801d4e9db291611.
- DONE: verify all 57 source files, archive checksums, amd64/arm64 images, SBOM, provenance and Cosign signature.
- CONTINUE: observe normal operation without inducing power cuts or voltage changes.
- WAITING FOR HARDWARE: physical Pro 3EM / 3CT63 L1/L2/L3 acceptance.
- Full physical voltage-transition acceptance for the new edit-window behavior remains outstanding.

These current steps take precedence over older historical version notes below.
`RELEASE_VERSION=0.5.7-hw` matches the published software release.
`LIVE_VERSION=0.5.6-hw` remains the latest full physical acceptance checkpoint.

## Completed

### 1. Shelly hardware/API discovery — DONE

- Physical Shelly Pro EM-50 identified and queried locally.
- `EM1.GetStatus` validated on both channels.
- Voltage/frequency fields confirmed on hardware.
- Stable firmware 2.1.1 retained; beta firmware intentionally skipped.
- Device timezone corrected to Europe/Kyiv.

### 2. Core monitoring service — DONE

- Polling every 10 s by default.
- SQLite WAL history.
- Daily min/max/avg.
- Retention pruning.
- No fake `0 V` samples when Shelly is unavailable.
- Last valid data preserved.
- Digest authentication support.
- Monitor-only safety is regression-tested: production code permits the measurement
  `GET /rpc/EM1.GetStatus` path and contains no Shelly control/configuration RPCs.

### 3. Telegram implementation — DONE / LIVE ACCEPTED

Implemented:

- optional Telegram integration;
- one stable status message;
- `message_id` persisted in SQLite;
- `editMessageText` updates;
- automatic recreation after deleted/uneditable status message;
- `/voltage`, `/status`, `/today`, `/minmax`, `/history`, `/health`;
- optional threshold alerts, disabled by default;
- secret-file token/chat configuration.

Candidate `0.4.2-hw` additionally validates `/start`, `/help`, `/outages`,
persisted `getUpdates` offset, optional user-ID ACL, edit throttling, bounded 429
retry, and `message is not modified` handling.

Live Telegram acceptance is complete; current bot and status-channel destinations are validated by `doctor` and the live command/status flow.

### 4. Container/runtime hardening — DONE

- Debian 13 LXC 110 deployment.
- Docker/Compose runtime validated.
- non-root container.
- read-only root filesystem.
- capabilities dropped.
- no-new-privileges.
- PID and memory limits.
- Docker log rotation.
- persistent bind-mounted SQLite data.

### 5. Recovery/autostart — DONE

- LXC autostart.
- Docker autostart.
- Compose systemd unit.
- container restart persistence test.
- full LXC reboot recovery test.

### 6. Health/observability — DONE

- `/health`
- `/ready`
- `/status.json`
- `/metrics`
- runtime RPC latency
- SQLite integrity check
- `doctor`
- soak report CLI

### 7. Backup/supportability — DONE

- online SQLite backup;
- backup SHA256;
- daily persistent systemd timer;
- 14-copy default retention;
- redacted support bundle;
- bundle SHA256;
- secret redaction validation.

### 8. Installation UX — DONE / PUBLIC BOOTSTRAP ACCEPTED

- `install.sh --dry-run`;
- idempotent local install/update;
- secure interactive configuration;
- hidden Telegram token input;
- non-interactive mode;
- public `curl | bash` bootstrap, including clean Debian 13 dry-run validation;
- public `.env.example` separated from deployment `.env`.

## `0.4.2-hw` hardening and live acceptance

### `0.4.2-hw` — DEPLOYED / HARDWARE ACCEPTED

- Docker healthcheck now uses `/health`; `/ready` remains Shelly-data readiness.
- 3 consecutive poll failures -> `offline`; 2 successes -> recovery.
- last-good voltage/frequency restored from SQLite after process restart.
- no synthetic `0 V` samples during outage.
- repetitive outage warnings are rate-limited.
- persistent SQLite outage journal, schema version 2.
- schema v1 -> v2 migration preserves existing measurements.
- open outage survives service restart without creating a duplicate event.
- recovery closes the same event and records duration.
- optional one-shot Telegram outage/recovery alerts.
- outage count/duration metrics and `/outages` command.
- 45 unit tests pass; compile/style/Compose/ResourceWarning and supply-chain gates pass.
- isolated runtime simulation passed: offline -> restart while offline ->
  recovering -> online using a fake Shelly at 230.5 V / 50.0 Hz.
- installer acceptance uses `/health`, so a valid update does not fail merely because
  Shelly is offline; `/ready` remains a diagnostic data-readiness endpoint.
- pre-upgrade snapshots preserve source + online SQLite backup with SHA256.
- `scripts/rollback.sh` restores source/database, preserves `.env`/secrets, and
  creates a rescue backup of the state being rolled back.
- rollback fixture is part of CI and passed end-to-end.
- online backup against the live 2382-sample database passed SHA256 + quick_check.
- CI-equivalent release-candidate gate passed end-to-end on 2026-10-31.
- reproducible source-release fixture produces byte-identical tarballs for a fixed epoch,
  verifies internal/external SHA256 manifests, and excludes `.env`, secrets, SQLite,
  logs and pycache.
- release-hygiene regression now also excludes operational `validation/`, `dist-publish/`
  and `ROADMAP.md.pre-*` files; the final source-archive checksum is kept in the
  external `SHA256SUMS` release asset and validation evidence, avoiding a self-referential hash.
- schema-aware soak report remains compatible with schema v1 and separately reports
  diagnostic sample gaps, last-sample age and confirmed schema-v2 outage events.
- support bundle schema-v2 fixture validates outage diagnostics and secret redaction.
- `doctor` remains usable while Shelly is offline, so hardware unavailability does not
  suppress service/database/Telegram diagnostics.
- Telegram transport failures are sanitized so exception text cannot expose the bot
  token embedded in the Bot API URL.

Live acceptance on 2026-10-31 is complete for the monitor-only path: isolated real-Shelly smoke PASS; a live schema-v1 database copy migrated to schema v2 with all 3848 original measurement rows byte-stable by row hash; the production switch preserved all 3962 pre-switch rows; SQLite `quick_check=ok`, zero-voltage rows remain 0, and container restart persistence PASS. Telegram remained disabled because its secret files are empty.

## Active validation

### 9. Long-running hardware soak — DONE

24 h soak acceptance is complete. The database spans 26.947 h of real samples
(28.678 h wall time at acceptance), with 4538 measurements. While Shelly was
reachable, median cadence was 10.040 s and p95 cadence was 10.069 s. SQLite
`quick_check=ok`; zero-voltage samples remain 0. The report separately records
three >30 s diagnostic gaps and does not reinterpret them as outages.

A real mains blackout beginning at 2026-10-31 23:55:03+02:00 was observed during
acceptance. `0.4.2-hw` kept `/health` live, moved `/ready` to 503, retained the
last-good 227.7 V / 50.0 Hz sample, and opened exactly one schema-v2 outage event.
An online SQLite backup taken during the outage passed SHA256 and `quick_check`
with all 4538 measurement rows. Restarting the production container during the
same open outage preserved that single outage event (no duplicate), restored the
last-good sample, kept zero synthetic 0 V rows, and returned to `offline` state.

Soak statistics at acceptance:

- sample span: 26.947 h;
- wall elapsed: 28.678 h;
- samples: 4538;
- cadence median / p95 while sampling: 10.040 / 10.069 s;
- voltage min/max over retained history: 106.5 / 234.7 V;
- frequency min/max: 48.5 / 50.0 Hz;
- diagnostic gaps >30 s: 3; maximum 26610.197 s;
- confirmed outage journal: 1 open real blackout event;
- SQLite `quick_check`: `ok`;
- synthetic 0 V rows: 0.

The monitor remains intentionally online as a service while the physical Shelly is
off; data collection will resume automatically after power returns and two
successful polls confirm recovery.

## Offline-safe supply-chain audit — DONE

- GitHub workflows use explicit least-privilege permissions.
- CI uses `pull_request`, not privileged `pull_request_target`.
- release checkout is pinned to the exact release tag and verifies `VERSION`.
- source assets are checksum-verified and private/generated files are rejected.
- GHCR build emits SBOM and max provenance for amd64/arm64.
- runtime Python dependencies have upper bounds.
- bootstrap refuses the placeholder repository; pinned release installer verifies SHA256.
- workflow security regression tests are included in the normal unit-test suite.
- publication metadata/version consistency and operational-file exclusion are
  regression-tested; the frozen `0.4.2-hw` publication gate passed 45 tests plus
  installer, rollback, release reproducibility and support-bundle fixtures.
- final publication gate rebuilds the pinned container image to the exact
  hardware-accepted image ID and verifies source/image `app.py` identity; the only
  GitHub-publication input still missing is the destination `owner/repo`.
- All external GitHub Actions are pinned to full immutable commit SHAs; this is
  protected by the normal regression suite.

## `0.4.3-hw` hardening and live acceptance

- `version` CLI command no longer requires Shelly/runtime configuration.
- `db-check` no longer requires Shelly config and uses SQLite read-only mode;
  a missing database fails with exit 2 instead of being silently created/migrated.
- candidate work was isolated from the then-live `0.4.2-hw` until hardware promotion.
- full candidate software suite passes 49/49 tests plus compile/shell, Compose,
  reproducible release, installer, rollback and support-bundle fixtures.
- `0.4.3-hw-rc2` passes isolated blackout validation with no measurements or
  synthetic 0 V rows, one outage event and SQLite `quick_check=ok`.
- cold-start blackout readiness now reports the confirmed `shelly offline`
  state even before the first successful measurement exists.
- operational `db-check` against the live WAL database leaves the main SQLite
  file SHA256 and mtime unchanged; a read-only bind mount is not a supported
  WAL diagnostic mode because SQLite may require shared-memory access.
- after mains recovery, real-device smoke passed on the physical Shelly;
  `0.4.3-hw` was promoted to live CT110 with the existing `.env` preserved.
- pre-promotion snapshot `20261101-035941-0.4.2-hw` passed source/database
  SHA256 verification and rollback dry-run validation.
- post-promotion SQLite continuity passed: no synthetic 0 V rows, no duplicate
  outage, `quick_check=ok`, and sampling resumed normally after container restart.

## `0.4.4-hw` hardening and live acceptance

- Outage duration semantics are now explicit: confirmed connectivity interval
  after configured offline/recovery hysteresis, not exact mains-loss boundaries.
- Telegram `/outages`, Prometheus HELP and soak-report JSON use the same semantics.
- Software candidate version is separated from the hardware-accepted live version.
- Recovery regression asserts two valid recovery samples are persisted and the
  outage closes only on the second successful poll.
- Runtime version now has one source of truth (`VERSION`); the Docker image and
  `app.py version` can no longer silently retain a previous release string.
- Isolated real-Shelly smoke PASS before promotion.
- Safe promotion PASS with preserved `.env`, SQLite continuity, verified snapshot,
  rollback dry-run and post-promotion restart continuity.
- Live `0.4.4-hw` is now the hardware-accepted checkpoint.

## `0.4.5-hw` hardening and live acceptance

- Real restart evidence showed `last_outage_duration` became null although SQLite
  retained the latest closed outage.
- Candidate restores that historical duration from SQLite during startup without
  changing outage detection, hysteresis or schema.
- Regression coverage includes a newest closed outage followed by an open outage,
  preserving both the historical duration and current offline state.
- Isolated real-production-DB restart validation: PASS.
- Safe promotion, snapshot verification, rollback dry-run and production restart: PASS.
- Live `0.4.5-hw` is now the hardware-accepted checkpoint.

## `0.4.6-hw` hardening and live acceptance

- Clean Debian 13 release gate exposed a first-install dry-run failure caused by
  bare `return` inheriting status 1 from a failed no-op condition under `set -e`.
- Candidate makes expected snapshot/rollback no-op paths return 0 explicitly.
- CI now covers both a fresh target and in-place installer dry-run.
- Clean Debian 13 first-install acceptance: PASS.
- Isolated real-Shelly smoke: PASS.
- Safe promotion, snapshot verification, rollback dry-run and production restart: PASS.
- Live `0.4.6-hw` is now the hardware-accepted checkpoint.

## `0.4.7-hw` supportability hardening and live acceptance

- `soak-report.py` now opens the requested SQLite database read-only.
- A missing or mistyped `--db` path fails closed with exit 2 and a concise diagnostic instead of creating an empty SQLite file or emitting a traceback.
- 52/52 Python regression tests pass; release, installer, rollback and support-bundle fixtures pass.
- Live SQLite read-only validation preserves SHA256 and mtime; missing DB paths fail closed without creating files.
- Isolated real-Shelly probe PASS before promotion.
- Safe promotion from `0.4.6-hw` created and checksum-verified the rollback snapshot.
- Post-promotion continuity and container restart PASS: no synthetic 0 V rows, two outage rows preserved, `quick_check=ok`.
- Live `0.4.7-hw` is now the hardware-accepted checkpoint.

## `0.4.8-hw` Telegram hardening and live acceptance

- Preserve the live long-poll fix: `getUpdates` uses a 20 s Bot API long poll with 25 s HTTP timeout headroom.
- Add `telegram-discover`, which validates the configured bot token and lists pending chats without printing secret material.
- Discovery works before a chat ID is configured and reports `waiting_for_update` cleanly when no pending update exists.
- Software gate passes 55/55 tests plus syntax, Compose, installer, reproducible release, rollback and support-bundle fixtures.
- Isolated real-Shelly probe PASS before promotion.
- Safe promotion from `0.4.7-hw` created a rollback snapshot and preserved SQLite continuity.
- Telegram `doctor` validates both bot identity and destination chat.
- Stable status message edit-in-place PASS with the persisted message ID unchanged.
- Deleted status message recreation PASS: one replacement message was created and its new ID persisted.
- Live `/voltage` command handler response PASS in the configured chat; all expected bot commands are registered.
- Sustained live command-loop check reports zero `ReadTimeout`, transport or polling failures.
- Post-promotion service remains healthy and ready; zero synthetic 0 V rows, two outage rows preserved, `quick_check=ok`.
- Live `0.4.8-hw` is now the hardware-accepted checkpoint.

## `0.4.9-hw` Telegram notification-policy acceptance

Requested notification behavior:

- ordinary Telegram messages: silent;
- automatic low-voltage alert below 180 V: normal notification outside quiet hours;
- 23:00-07:00 Europe/Kyiv: all Telegram messages forced silent;
- low-voltage recovery threshold: 185 V, recovery message silent;
- one alert per low-voltage episode, not every poll.

Implementation is configurable through `.env`; boundary/regression tests pass and the candidate was promoted to live CT110 with Telegram/Shelly doctor checks passing.

Acceptance evidence:

- 60/60 Python tests PASS;
- installer dry-run, reproducible release, rollback and support-bundle redaction PASS;
- live `.env` explicitly sets silent-by-default, 23:00-07:00 quiet hours, 180 V alert and 185 V recovery;
- Telegram and Shelly doctor checks PASS after promotion;
- persisted Telegram status `message_id` and update offset retained;
- no Telegram warnings after restart;
- pre-upgrade snapshot: `20261101-182218-0.4.8-hw`.

## `0.4.10-hw` admin Telegram settings acceptance

- `/settings` displays the live Telegram notification policy.
- Inline edit controls are shown only to IDs in `TELEGRAM_ADMIN_USER_IDS`.
- Callback writes are rejected for non-admin users.
- Settings are persisted in SQLite `meta` and loaded immediately, so no container restart is required.
- Controls cover silent default, quiet-hours enable/start/end, low-voltage alert enable/threshold/recovery and reset to `.env`.
- Promoted to live CT110 after all software gates passed. Admin configuration, health, Shelly/Telegram doctor, command registration source and existing Telegram state were verified. Interactive callback acceptance is available through `/settings`.

## `0.4.11-hw` separate Telegram status-channel acceptance

- Keep private control chat for commands and admin `/settings`.
- Publish/edit the stable voltage status in a dedicated Telegram channel.
- Persist channel status message independently as `telegram_status_message_id`.
- Doctor validates both destinations.
- Live target channel verified by Bot API before candidate promotion.
- Promoted to live CT110. Bot API validated the channel destination and the persisted channel status remained stable across subsequent update cycles, confirming in-place editing. Private control chat and admin `/settings` remain separate.

## `0.5.0-hw` three-phase + full feature candidate — SOFTWARE ACCEPTED / HARDWARE WAIT

Implementation completed in isolated candidate while production remains `0.4.11-hw` during the real mains outage.

Implemented:

- schema v3 with lossless v1/v2 → L1 migration;
- L1/L2/L3 runtime model; L2/L3 optional until hardware arrives;
- independent phase history/outage/polling/alerts;
- three-phase status channel, dashboard, health JSON and Prometheus metrics;
- warning/critical and <180 V alarm dwell/recovery hysteresis;
- recovery/outage notifications;
- daily summary and voltage-quality analytics;
- PNG `/chart`, CSV `/export`, outage history;
- admin maintenance mode and audit journal;
- backup freshness, watchdog counters and SQLite maintenance/rollups;
- read-only device firmware/uptime/time-skew health;
- optional channel status pinning.

Migration proof on a copy of the live database: 7543 measurements + 3 outages before and after migration, all assigned to L1, `PRAGMA quick_check=ok`.

Software/package gates are complete. Do not promote during the blackout.

Software acceptance checkpoint (final):

- 85/85 Python tests PASS;
- every function in `app.py` and `advanced_features.py` is <=40 lines;
- fresh/in-place installer dry-run PASS;
- reproducible release PASS; rollback PASS; support-bundle redaction PASS; Compose config PASS;
- final candidate image runs as UID 10001 with read-only root;
- isolated HTTP smoke PASS for `/health`, `/metrics`, `/dashboard`;
- schema-v3 migration on production DB copy PASS, row counts preserved, zero synthetic 0V rows, `quick_check=ok`;
- Telegram doctor PASS for private control chat + status channel; bot is channel administrator with post/edit rights;
- production `0.4.11-hw` remains unchanged and healthy during the real mains outage.

Promotion gate: wait for L1 mains to return, run real Shelly hardware acceptance, then promote `0.5.0-hw`. L2/L3 software slots are ready; their hardware acceptance naturally waits for the two future sensors.

## `0.5.1-hw` Pro 3EM preparation — SOFTWARE CANDIDATE

- Native Pro 3EM-3CT63 triphase polling through `EM.GetStatus?id=0`.
- A/B/C mapped to L1/L2/L3 with one polling timestamp.
- Missing/null phase values never become fake 0 V samples.
- Legacy Pro EM-50 and optional separate L2/L3 sensor mode retained.
- Confirmed power recovery rotates the stable channel status: delete old outage-era message → create/persist fresh online message.
- Production recovery behavior is already backported to live `0.4.12-hw` so the next mains restoration is covered before Pro 3EM hardware arrives.

Hardware validation for Pro 3EM naturally waits for the device purchase/installation.

## `0.5.3-hw` Telegram module selector — SOFTWARE CANDIDATE

- Admin `/settings` shows the active Shelly module.
- Admin may select `Pro EM-50` or `Pro 3EM / Pro 3EM-3CT63`.
- Selection is persisted in SQLite and applied immediately by rebuilding read-only clients/state.
- IP addresses and credentials remain in protected configuration, never editable through Telegram.
- Persisted runtime settings are now loaded automatically after every service restart.


## Next external-input stages

### 10. Telegram live acceptance — DONE

Live acceptance completed on CT110 with the configured private destination:

1. `doctor` validates `getMe` and `getChat` — PASS.
2. Stable status message exists and its `message_id` is persisted — PASS.
3. Subsequent status update edits the same message in place — PASS.
4. Telegram update offset persistence remains present — PASS.
5. Live command response and registered command set — PASS.
6. Deleting the status message causes exactly one replacement status message to be created and remembered — PASS.
7. Long-poll command loop runs without `ReadTimeout` or transport warnings after the timeout-headroom fix — PASS.

No bot token or private chat identifier is stored in public release artifacts or documentation.

### 11. GitHub publication — DONE

- Public repository: `iSentinel/shelly-voltage-telegram`.
- Multilingual README pages and the live dashboard image are published.
- Release `v0.5.4-hw` is published with source archive and `SHA256SUMS`.
- CI, container image and source-release workflows are active.
- Public release excludes `.env`, secrets, SQLite and local validation data.
- The one-line installer points to the real public repository.
- Public bootstrap dry-run was validated on a clean Debian 13 container.
## Optional hardening after acceptance

- Shelly Digest-auth software path is regression-tested. Live auth validation is
  deferred until the operator independently enables authentication on the physical
  Shelly; this monitor-only project does not change device authentication settings.
- Add a second Shelly device only if required later.
- Add Grafana/Prometheus integration only if useful; current metrics endpoint is already compatible with scraping.
- Signed container releases are implemented and independently verified in `0.5.5-hw` using keyless Sigstore Cosign signatures over immutable GHCR digests.


## `0.5.4-hw` maintainability audit — RELEASED / HARDWARE ACCEPTED

- Production is running hardware-accepted `0.5.4-hw` after the audited
  release was merged and promoted through the normal installer path.
- PEP 8/Ruff checks pass with no source lines over 79 characters.
- All Python functions, including tests, are limited to 40 lines.
- Production modules/classes/functions have clear docstrings; application logging
  remains on `loguru`; no standard-library `logging` imports remain.
- Exact duplicate function bodies are rejected by the audit gate.
- Legacy `SHELLY_METER_TYPE` migration is covered for missing and blank values.
- Multilingual GitHub documentation and a real dashboard image are maintained as
  part of the public release tree.
- Isolated candidate runtime against a physical Pro EM-50 passed with schema v3,
  valid live samples and no synthetic `0 V` measurements.


### Next stage after `0.5.4-hw`

- Keep the current Pro EM-50 deployment under normal observation.
- Perform physical three-phase acceptance when the planned Pro 3EM / Pro 3EM-3CT63 is installed.
- Validate L1/L2/L3 mapping, outage behavior, Telegram presentation, charts,
  exports and metrics with real three-phase data before enabling that backend in
  production.


## `0.5.5-hw` signed container release — RELEASED / SUPPLY-CHAIN ACCEPTED

- Keep production on hardware-accepted `0.5.4-hw`; `0.5.5-hw` changes the public release supply chain, not runtime monitoring behavior.
- Versioned GHCR images install Cosign through an official action pinned to a full commit SHA.
- Release images are signed keylessly through GitHub OIDC; no long-lived signing key is stored in repository secrets.
- The signature targets the immutable image digest returned by the multi-architecture build.
- Ordinary `main` images remain unsigned; only versioned release images receive the release signature.
- Regression tests enforce OIDC permission, pinned action SHA, digest-based signing and release-only conditions.
- Acceptance completed: GitHub Actions run `41373145505` built and keyless-signed RC2 digest `sha256:df534c7b9b30e2a8f3dfc617b07a0ff6837ecc15b285331dc13e211130dcca4d`; independent Gateway `cosign verify` passed with the expected GitHub Actions certificate identity and OIDC issuer.
- Final `v0.5.5-hw` publication completed: Source Release run `41373981210` and signed Container Image run `41373982691` succeeded from exact tag source `f669dbb628854ea6bdbedd61d3b505fe613c93bf`; final image digest is `sha256:c2115def9975dcfbb4a722d08713450403f53beaa01e6c54fec75962d82ce47a`, independently verified through Gateway.


## `0.5.6-hw` Pro EM-50 live acceptance + Pro 3EM tooling — HARDWARE ACCEPTED

Current hardware gates:

- **Current Pro EM-50 deployment:** recovery acceptance, read-only hardware probe, Telegram continuity and post-promotion soak all PASS; `0.5.6-hw` is live and hardware accepted.
- **Future Pro 3EM / 3CT63 backend:** keep disabled in production until the physical three-phase meter is installed and the dedicated acceptance CLI plus L1/L2/L3 UI/data checks pass.

- Separate candidate/source, public release and live hardware checkpoint with `VERSION=0.5.6-hw`, `RELEASE_VERSION=0.5.5-hw` and `LIVE_VERSION=0.5.6-hw`.
- Final release publication is fail-closed until `RELEASE_VERSION` is explicitly promoted to match `VERSION`.
- Add standalone read-only `scripts/pro3em-acceptance.py` for physical Pro 3EM / Pro 3EM-3CT63 acceptance.
- Reuse the existing Digest-auth `Pro3EMClient`; the acceptance path issues only `EM.GetStatus?id=0` measurement RPCs.
- Require complete L1/L2/L3 data from one atomic RPC cycle and reject missing phases, zero/implausible values and RPC failures.
- Provide repeated sampling, configurable bounds, text/JSON output and stable PASS/FAIL exit codes.
- Route low-voltage/recovery transitions for L1/L2/L3 into the stable Telegram status header, including phase (for L2/L3), event time, measured voltage and frequency; do not send duplicate private messages.
- Add read-only `scripts/recovery-acceptance.py` with `baseline` and `verify` modes for real blackout/recovery acceptance. It checks the same outage closes, the stable Telegram status rotates, `/ready` becomes healthy, valid samples increase and synthetic `0 V` remains absent.
- Recovery verification matches the exact captured outage by ID, phase and start time, and verification/promotion JSON redacts raw connection error text and Telegram status message IDs.
- New recovery baselines persist only a status-message fingerprint instead of the raw Telegram `message_id`; legacy baseline files remain compatible.
- Add read-only `scripts/promotion-check.py` as the single pre-promotion gate. It reuses recovery verification, requires the live service version to match `LIVE_VERSION`, and fails closed unless the active backend remains `pro_em50`.
- Physical three-phase acceptance remains pending until the Pro 3EM / 3CT63 is installed; this blocks only switching production to the `pro3em` backend, not promotion of the 0.5.6 software on the current Pro EM-50.


Software acceptance checkpoint:

- Ruff 0.14.2 formatting/lint and project quality rules PASS.
- Local and GitHub quality checks now use the same pinned `scripts/quality.sh` gate, preventing format/lint drift.
- 138/138 Python tests PASS with the ResourceWarning gate enabled.
- `py_compile`, shell syntax, installer dry-run, rollback, support-bundle
  redaction, pinned-release installer and Compose validation PASS.
- Reproducible source release PASS and excludes Python tool caches.
- Candidate container build/version smoke PASS.
- Prometheus current voltage/frequency fail stale-safe: `starting`/`offline` export `NaN`; `recovering`/`online` export only the newest valid sample.
- Local dashboard is stale-safe too: `starting`/`offline` phase cards hide voltage, while `recovering`/`online` show only the newest valid sample.
- Telegram stable status is stale-safe: offline phases expose no V/Hz values; `recovering` shows the newly received sample with an explicit recovery state.
- Low-voltage/recovery status-header persistence and destination tests PASS; no duplicate private message is sent for the primary phase.
- Read-only physical Pro EM-50 probe PASS on the current installation.
- Real blackout-soak acceptance PASS: the active outage remains a single record, no measurements are inserted while offline, stable Telegram status is not rotated and synthetic `0 V` remains absent.
- Pro 3EM acceptance CLI end-to-end HTTP smoke PASS for three atomic
  L1/L2/L3 cycles.
- Promotion on the current Pro EM-50 PASS: the captured outage closed, recovery baseline verified, read-only hardware probe passed, and Telegram/status continuity passed before and after promotion.
- Switching production to the `pro3em` backend remains blocked until the real Pro 3EM / 3CT63 is installed and L1/L2/L3 mapping, outage behavior, Telegram presentation, charts, exports and metrics pass on physical three-phase data.

### 2026-11-02 blackout operational checkpoint (historical pre-recovery)

- Shared quality gate PASS and 138/138 Python tests PASS.
- Installer, rollback, support-bundle and reproducible-release fixtures PASS.
- Live promotion gate fails closed while outage 4 remains open.
- Active recovery baseline now stores only a Telegram status-message fingerprint;
  no raw Telegram message ID is stored in the refreshed baseline.
- Compose service and backup timer are enabled and active; runtime container is
  healthy with zero restarts.
- Latest backup checksum and SQLite quick_check PASS; live SQLite quick_check PASS.
- Backup and live history contain zero synthetic 0 V measurements.
- Promotion remains blocked only by real Shelly recovery; the Pro 3EM backend
  remains separately blocked until physical three-phase hardware is installed.

## `0.5.6-hw` shared voltage-status notification cooldown

- Low/high warning and critical transitions no longer create separate Telegram
  alert messages; they replace the shared status message.
- Replacement is publish-first/delete-old so a publish failure keeps the
  previous status visible.
- First abnormal status may notify normally; subsequent abnormal replacements
  are silent for the configured cooldown. Recovery is always silent.
- Cooldown is measured from the last loud voltage status and silent updates do
  not extend it.
- Runtime choices: Off / 3 / 5 / 10 minutes; default 5 minutes.
- `/settings` persists the selection in SQLite and applies it immediately.
- Quiet hours remain authoritative and force silence.
- Software validation: quality gate PASS, 138/138 Python tests PASS.
- Hardware promotion PASS after outage #4 closed and recovery verification passed.

### 2026-11-02 `0.5.6-hw` live hardware acceptance

- Outage #4 closed on the physical Pro EM-50 and the captured recovery baseline
  verified PASS.
- `promotion-check.py --json` PASS with `pro_em50` and read-only guard active.
- Production promoted from `0.5.4-hw` to `0.5.6-hw`; rollback snapshot
  `20261102-212042-0.5.4-hw` source and SQLite checksums PASS.
- Post-promotion `doctor` PASS for Shelly, SQLite and Telegram.
- Container healthy/ready with physical voltage sampling resumed.
- SQLite continuity PASS: four closed outages, zero open outages, quick_check=ok,
  zero synthetic 0 V rows, measurement history increasing.
- Telegram shared status continuity PASS across promotion and restart; the persisted identifier remained stable without being exposed in release documentation.
- Runtime cooldown default is 5 minutes; `/settings` exposes Off/3/5/10.
- Restart continuity PASS with the same status ID and no duplicate outage.
- `LIVE_VERSION` promoted to `0.5.6-hw`; `RELEASE_VERSION` remains `0.5.5-hw`
  until public release publication completes.
- Pro 3EM production backend remains blocked until physical three-phase hardware
  acceptance is performed.


### 2026-11-02 post-promotion live checkpoint

- Outage 4 recovery verification PASS with the same captured outage identity.
- Read-only physical Pro EM-50 probe PASS before promotion.
- Safe installer promotion from `0.5.4-hw` to `0.5.6-hw` PASS with a verified
  pre-upgrade source + SQLite rollback snapshot.
- `VERSION=0.5.6-hw` and `LIVE_VERSION=0.5.6-hw`; public
  `RELEASE_VERSION=0.5.5-hw` remains intentionally separate.
- Level alerts are enabled on the live deployment and voltage notification
  cooldown defaults to 5 minutes.
- Post-promotion container health PASS with zero restarts, `/ready` PASS,
  Telegram failures zero and no warning/error logs in the acceptance window.
- SQLite `quick_check` PASS; outage count remains four with no open duplicate;
  synthetic `0 V` rows remain zero.
- Public `0.5.6-hw` release is the next release stage. Physical Pro 3EM /
  3CT63 acceptance remains blocked until that hardware is installed.

### 2026-11-02 `0.5.7-hw` Telegram status lifecycle

- PASS: old shared status is unpinned before deletion.
- PASS: status pinning defaults to OFF and remains configurable in `/settings`.
- PASS: public status inline buttons are removed, including startup cleanup for the current status.
- PASS: voltage alert/recovery transitions use one editable message for 3/5/10 minutes; default is 10 minutes.
- PASS: recovery keeps the original deadline; repeated transitions reuse the post.
- PASS: quality gate and 158/158 tests passed before deployment.
- PASS: corrected source deployed; container healthy, readiness true, status ID preserved, SQLite healthy and new measurements collected.

### 2026-11-02 operational continuation after PR #22

- Physical Pro EM-50 diagnostics, SQLite diagnostics and Telegram diagnostics PASS.
- Runtime container healthy and ready; monitor-only measurement polling continues.
- At the recorded checkpoint: 11,103 measurements, four closed outages,
  no open outages and no synthetic zero-voltage samples.
- Recent nine-minute observation: 53 samples, no diagnostic gaps above 30 seconds,
  maximum sample interval 10.259 seconds, voltage 212.6–219.1 V.
- Fresh online backup SHA256 and SQLite quick_check PASS.
- Runtime source matches the implementation merged in PR #22.
- Full hardware checkpoint remains 0.5.6-hw; no electrical transition was induced.
