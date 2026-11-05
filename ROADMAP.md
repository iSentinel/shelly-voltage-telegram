## 2026-11-05 defensive GitHub update response validation

- DONE: PR #71 merged localized, administrator-only GitHub update
  information for RU/UK/EN and rejected stable releases missing
  the matching source archive or SHA256SUMS. Automatic installation
  remains disabled.
- IMPLEMENTED IN SOURCE (not deployed): constrain public GitHub API
  response size to 128 KiB; reject malformed JSON, unexpected response
  shapes and noncanonical release tags before showing an update notice.
- TESTING: regression tests cover malformed metadata, missing assets,
  invalid JSON and oversized responses. GitHub CI must pass before merge.
- PRODUCTION: physical Shelly remains offline in natural outage #10;
  LXC 110 stays on 0.5.12-hw without any updater service/timer deployed.
- NEXT: after natural recovery, verify real Telegram delivery and exact
  outage #10 closure using the private event baseline. Do not force
  a hardware outage or an in-place update during the current event.

## 2026-11-05 private and localized update-check follow-up

- IMPLEMENTED IN SOURCE (not deployed): administrator-only `/updates`
  and the Telegram Updates menu now use the configured Russian, Ukrainian
  or English language instead of English-only messages.
- IMPLEMENTED IN SOURCE (not deployed): only published stable GitHub
  Releases containing both the matching source archive and SHA256SUMS are
  eligible for an availability notice; incomplete release assets are
  rejected. The displayed URL uses the validated tag on the canonical
  project GitHub Releases page.
- IMPLEMENTED IN SOURCE (not deployed): network or metadata errors produce
  a short private localized message, without exposing internal details
  into the shared Telegram control chat.
- TESTING: full CI and isolated quality acceptance are required before
  merge. No production Docker update, timer installation or Telegram
  delivery is authorized during natural outage #10.
- NEXT HARDWARE STEP: after natural recovery, verify actual private
  Telegram delivery and close the outage #10 hardware acceptance baseline.

## 2026-11-05 final read-only updater and Git tag acceptance

- PASS: a real Github Releases API request from the isolated LXC 111
  returned latest public `0.5.12-hw` relative to installed QA
  `0.5.11-hw`, with `Update available: yes` and automatic installation
  disabled. No Telegram message, release publication or Docker action
  was performed.
- PASS: the QA-only `shelly-voltage-update-check.service` executed under
  systemd with DynamicUser, stored its JSON status, and exited normally.
  Its timer was temporarily enabled and its 24-hour cadence parameters
  inspected, then disabled again. The test container's ID, start time and
  restart counter remained identical.
- PASS: the new release policy CLI was exercised against a temporary,
  real Git repository and real `v0.5.13-hw` tag: matching
  `VERSION=RELEASE_VERSION=0.5.13-hw` and matching commit were accepted.
  Changing `RELEASE_VERSION` to `0.5.12-hw` was denied. No tag was
  created in GitHub and no image was published.
- PASS: PR #69 implemented signed release promotion controls, 274/274
  unit tests, pinned Ruff/maintainability checks, and GitHub CI on
  Python 3.11, 3.12 and 3.13.
- LIMITATION: actual Telegram delivery of an update notice to a
  privileged administrator still requires separate authorized acceptance;
  checking GitHub and showing text in a synthetic fixture do not prove
  Telegram delivery.
- PRODUCTION: LXC 110 remains at `0.5.12-hw`, Shelly offline with
  natural outage #10 open, SQLite intact and no updater timer installed.
  Do not upgrade, restart, publish a new release or modify real meter data
  until stable power recovery and acceptance.

## 2026-11-05 daily release check and secure image promotion QA

- PASS: In isolated LXC 111, `shelly-voltage-update-check.service`
  completed as an unprivileged DynamicUser oneshot. Its status JSON reported
  installed QA `0.5.11-hw`, latest public `0.5.12-hw`, available=true and
  auto_install=false. The QA Docker container ID, start time and restart
  count were unchanged. The configured 24-hour timer was started and
  inspected, then disabled again in the QA LXC.
- NOT YET OBSERVED: an entire 24-hour real-time timer interval. The
  `OnUnitActiveSec=24h` configuration and an immediate systemd service
  execution were verified, not a complete calendar-day soak.
- IMPLEMENTED FOR CI: `image.yml` now invokes
  `scripts/image_release_policy.py` before publishing an image. Stable
  signed tags `vX.Y.Z-hw` require a matching tag push, matching
  `VERSION` and `RELEASE_VERSION`, and an exact checkout of that tag's
  commit. Manual builds use `manual-*` tags only and cannot promote an
  arbitrary commit as a signed stable release.
- PASS: release-promotion policy regression tests, complete 274/274
  Python tests, pinned Ruff and maintainability quality gate.
- PRODUCTION: no GitHub update check timer or updater has been installed
  in LXC 110. The physical Shelly remains offline with natural outage #10
  open. No release was tagged, published or installed.

## 2026-11-05 positive signed-update acceptance in isolated LXC 111

- PASS: The full updater installed published, Cosign-verified
  `v0.5.12-hw` over a disposable `0.5.11-hw` QA fixture using the real
  GitHub Release, immutable GHCR digest and source archive SHA256.
  Preflight, storage checks, restricted backup and post-update checks passed.
  `apply-update.sh` returned `UPDATE PASS: 0.5.12-hw`.
- PASS: The upgraded application (not a stub) polled an isolated synthetic
  Shelly `EM1.GetStatus` endpoint at 230.0 V and 50.0 Hz. Telegram was
  explicitly disabled. `/health` returned version `0.5.12-hw` and
  `/ready` returned HTTP 200; new rows were written to test SQLite.
  No production network, meter or Telegram credentials were used.
- QA FIXTURE: initial attempts failed because a local test-only Compose
  file omitted `env_file` and the synthetic SQLite lacked `phase`.
  Correcting the fixture, not the product source, resolved startup.
  LXC 110 production database schema version 3 already contains
  `phase`; the standard product Compose already passes `.env`.
- PASS: the test-only Compose healthcheck was adjusted to work with both
  BusyBox and Python images. The upgraded signed image was Docker
  `healthy` as well as HTTP ready.
- PASS: after an actual reboot of LXC 111, systemd restarted that pinned
  signed image and the synthetic Shelly RPC fixture. The digest, deployed
  version metadata, healthy Docker state, HTTP readiness and SQLite
  integrity persisted; no pending-update marker remained.
- CLEANUP: the QA test saved an acceptance JSON and post-upgrade SQLite
  snapshot in `/root/qa-positive-acceptance-20261105/` on LXC 111.
  The original `shelly-qa-http:old` fixture and original test SQLite
  (2 rows) were restored, the simulated RPC stopped, and update pinning
  metadata removed. The final QA `/ready` and SQLite quick check passed.
- LIMITATION: the signed image was tested against a simulated Shelly,
  not a real voltage transition; Telegram delivery, uncontrolled host
  power loss and promotion of candidate `0.5.13-hw` remain unverified.
- PRODUCTION: Shelly is offline with natural outage #10 open.
  LXC 110 stays on `0.5.12-hw`, without the updater/timer installed;
  no production deployment is authorized until recovery acceptance.

## 2026-11-05 LXC 111 signed updater and reboot acceptance

- PASS: Gateway reconfirmed that dedicated, unprivileged QA LXC 111
  `shelly-updater-qa` is running with Docker, Compose, Cosign 3.0.6, and a
  separate synthetic HTTP/SQLite monitoring fixture. No production Shelly,
  Telegram secret or SQLite history was copied into the fixture.
- PASS: updater dry-run from QA release `0.5.11-hw` to public
  `v0.5.12-hw`: fresh synthetic 230 V reading, GitHub target policy,
  GHCR signature identity and Cosign certificate, public archive SHA256,
  and `DRY-RUN PASS`. An initially stale fixture was correctly rejected.
- PASS: a real attempted signed-image replacement in QA failed to become
  healthy, as expected without the physical Shelly configuration.
  The updater exited nonzero and restored the prior
  `shelly-qa-http:old` container, the verified SQLite snapshot (2 rows),
  and original image choice. Both fixture HTTP readiness and SQLite
  integrity passed after rollback; pending journal was cleared.
- PASS: the QA-only Compose systemd service was installed and enabled with
  `ExecStartPre=apply-update.sh --recover`. A durable, QA-only pending
  marker and invalid override were prepared, then LXC 111 was actually
  rebooted. Startup logged `RECOVERY: interrupted update`, restored the
  previous image and SQLite, removed the pending marker/override, and
  returned healthy/ready responses.
- PASS: a second real QA reboot with an intentionally malformed pending
  marker made systemd refuse to start the Compose unit. No container was
  running. The bad journal was retained for investigation. After deleting
  only this QA test marker and resetting the failed unit, the old fixture
  returned healthy/ready.
- PASS: third real QA reboot with a checksum-corrupted SQLite backup.
  The Compose service detected SHA256 mismatch, refused to start, left the
  pending journal in place, and did not launch a container. After restoring
  the correct checksum from the QA-only fixture, systemd recovered the prior
  image, cleared the pending marker, and SQLite integrity plus /health and
  /ready all passed.
- LIMITATION: these were controlled LXC reboots with a prepared durable
  interruption marker. They do not demonstrate recovery from an actual
  uncontrolled power loss, filesystem corruption, or an unresponsive
  Docker daemon.
- COMPLETED in the later positive signed-update acceptance checkpoint:
  the published image started successfully in QA, wrote synthetic
  measurements, passed HTTP and Docker health checks, and survived reboot.
- PASS: corrupted-snapshot recovery was also tested through systemd.
  Still pending: public release promotion rules and Telegram delivery
  verification.
- PRODUCTION: LXC 110 still runs `0.5.12-hw` with natural outage #10 open,
  `/ready` HTTP 503 and no updater services installed. Do not install,
  reboot or promote the candidate while that condition persists.

## 2026-11-05 disposable LXC acceptance preparation

- PREPARED: dedicated QA container VMID 111, hostname
  `shelly-updater-qa`, Debian 13, unprivileged, nested container support,
  1 CPU, 1 GiB RAM, 6 GiB disk, and onboot disabled. No production secrets,
  Telegram credentials, or Shelly configuration were copied.
- BLOCKED: the Gateway executor lost the initial `pct start 111` job.
  A later start attempt also failed at the Gateway tool level. The last
  read-only `pct status 111` returned `stopped`; no Docker/reboot
  acceptance was performed in that container. Do not assume a Proxmox
  boot failure without additional host-side evidence.
- DONE: PR #62 implements fail-closed recovery of interrupted updates using
  a persisted journal, pre-start systemd recovery, and retained SQLite
  rescue snapshots. Its 258 unit tests and Python 3.11/3.12/3.13 CI passed.
- DONE: PR #63 fixes updater version selection: prefer a recorded deployed
  version, otherwise the installed `RELEASE_VERSION`, rather than assuming
  that a checked-out candidate `VERSION` is already installed. GitHub CI
  passed on Python 3.11, 3.12 and 3.13.
- NEXT: determine why the isolated LXC 111 did not start; install test-only
  Docker dependencies and exercise signed update, rollback, boot recovery
  and loss-of-power simulation without production credentials.
- BLOCKED: production LXC 110 remains on `0.5.12-hw`, with no updater
  timer or services installed. Do not deploy while Shelly is offline or
  outage #10 remains unresolved.

## 2026-11-05 interrupted-upgrade boot recovery

- IMPLEMENTED IN CODE (not deployed): write and flush a root-only
  `pending-update` journal before changing the pinned Compose image.
- IMPLEMENTED IN CODE (not deployed): the Compose systemd unit runs
  `apply-update.sh --recover` before starting the bot. An interrupted update
  stops the unconfirmed image and restores the former image selection and
  verified SQLite snapshot.
- SAFETY: malformed markers or damaged backups block startup; keep the
  pending journal for manual intervention. A first post-failure rescue copy
  is not overwritten during repeated recovery attempts.
- PASS: 6 isolated interruption tests, including no pending update, valid
  recovery, invalid journal, damaged backup, retained rescue and durable
  journal write.
- PASS: the complete 258/258 Python unit tests, pinned Ruff/maintainability
  gate, Bash syntax, systemd unit verification and release dry-run.
- NOT VERIFIED: an actual interrupted Docker replacement and full LXC
  reboot; these require a disposable test LXC before production acceptance.
- BLOCKED: no updater systemd unit, timer or new application image is
  installed in production. The physical Shelly is still offline with
  natural outage #10 unresolved.

## 2026-11-05 outage #10 and updater rollback acceptance

- OBSERVED: natural Shelly connectivity outage #10 is open on the physical
  Pro EM-50. The most recent valid voltage before disconnection was 215.1 V;
  it is a stale reading, not current mains voltage. Production `/ready`
  returns HTTP 503 as intended. Docker is healthy with zero restarts.
- PASS: an event-specific read-only recovery baseline was stored on LXC 110:
  `validation/0.5.12-hw-outage-10-baseline-20261105.json` (status PASS).
  It records outage id 10, 18,850 existing readings and no synthetic 0 V.
- PASS: installed Cosign 3.0.6 at `/usr/local/bin/cosign` independently
  validates the published `v0.5.12-hw` GHCR image signature. Debian Cosign
  2.5.0 does not discover that signature; the updater requires Cosign v3+.
- PASS: the published source's update preflight was executed read-only on
  the offline LXC and denied deployment due to HTTP 503. No updater systemd
  units, update timer or new application image were installed in production.
- FIXED in PR #59: use unique `mktemp` recovery files for SQLite rollback,
  preventing collision with an interrupted earlier rollback; 251/251 tests.
- FIXED in PR #60: preserve committed SQLite WAL records in the post-failure
  rescue snapshot using the SQLite backup API, not a main-file-only copy;
  252/252 tests passed, including a WAL-specific regression test.
- PASS: GitHub Actions on Python 3.11, 3.12 and 3.13 for both PRs.
- NEXT: after natural power recovery, verify outage #10 closure and stable
  voltage against its baseline, then validate the versioned signed update
  and crash/reboot rollback on a disposable test LXC before production.
- BLOCKED: no installation or promotion of the candidate while the active
  electrical/connectivity event remains unresolved.

## 2026-11-05 secure GitHub updater implementation

- IMPLEMENTED IN CODE (not deployed): read-only daily GitHub Releases checker with a 24-hour
  systemd timer; automatic installation explicitly disabled by default.
- IMPLEMENTED IN CODE (not deployed): administrator-only Telegram `/updates` command sends
  version information privately. The application has no Docker socket access
  and no authority to start the root updater.
- IMPLEMENTED IN CODE (not deployed): separate manually started root systemd oneshot downloads
  an immutable GHCR image, verifies its Cosign GitHub Actions identity and the
  published source archive SHA256, and refuses unlisted or older releases.
- IMPLEMENTED IN CODE (not deployed): enforce `/ready`, fresh voltage between 207 V and 253 V,
  SQLite integrity, no open outage, no active voltage episode and no
  unfinished stable-recovery timer before and after a candidate restart.
- IMPLEMENTED IN CODE (not deployed): create protected database/configuration/secret snapshots,
  preserve the image pin across reboots, and restore previous image/database
  after detected update failures with an additional post-failure DB rescue.
- PASS: 249 Python tests and the pinned Ruff/maintainability gate, systemd
  unit verification, Docker build and isolated Compose configuration, release
  and installer dry-runs, source checks and offline SQLite rollback tests.
- PENDING: full signed-image replacement and interrupted-host validation on a
  disposable LXC; no released v0.5.13-hw container exists yet.
- NOT DEPLOYED: no updater timer or host service is enabled in production.
  An actual update is blocked while outage/voltage acceptance is incomplete.
- PENDING: independently validate interrupted host reboot, Telegram delivery,
  and manual recovery on a disposable LXC before production promotion.

## 2026-11-05 outage #9 and v0.5.13-hw offline candidate audit

- OBSERVED: natural voltage readings fell from approximately 222.6 V to
  117.5 V on the physical Pro EM-50 immediately before monitoring
  connectivity was lost; the final valid sample was 120.1 V at 11:56:49
  Europe/Kyiv. These readings are device measurements, not synthetic faults.
- OBSERVED: connectivity outage id 9 opened at approximately 11:57:31
  Europe/Kyiv. No confirmed restoration had occurred at the last check;
  `/ready` returned HTTP 503, while Docker health and SQLite integrity
  remained healthy. No synthetic 0 V measurement was inserted.
- DONE: private, event-specific read-only acceptance baseline saved on the
  installation as `validation/0.5.12-hw-outage-9-baseline-20261105.json`.
  Do not reuse a baseline from earlier outages.
- NOT VERIFIED: whether Telegram delivered every critical warning. The
  recorded measurements and notification timestamp are not direct proof of
  Telegram message delivery; validate against the Telegram message history.
- DONE: PR #54 prepared an unreleased software candidate `0.5.13-hw`, while
  public release and production remain `0.5.12-hw` and the latest fully
  hardware-accepted checkpoint remains `0.5.6-hw`.
- DONE: PR #55 added a dry-run-first, atomic CSV apartment importer with
  bounded rows, duplicate rejection, and isolated tests. Python 3.11/3.12/
  3.13 CI passed.
- PASS: on the code merged via PR #55, two independent reproducible
  `0.5.13-hw` source archives had identical SHA256
  `34cf20066fc6c23b35752a583e58d862883af0f640c01e36806771d4d5c0f2b2`.
  The candidate Docker image built and passed an isolated module/version
  import; these were local checks, not published release artifacts.
- PASS: a private copy of the production SQLite accepted an example CSV
  without changing the existing measurements, outages or metadata. The
  live database and container were not modified for these tests.
- BLOCKED: do not publish, install, or promote this candidate while the
  current power-outage acceptance event is unresolved.
- NEXT: after natural recovery, verify exact outage #9 closure, freshness,
  voltage stabilization, and Telegram status rotation against its saved
  baseline. Then perform a backed-up candidate deployment and rollback
  acceptance with the live sensor online.

## 2026-11-05 apartment-to-phase development checkpoint

- DONE: PR #48 introduced the idempotent SQLite apartment-to-phase registry,
  administrator-only Telegram commands, atomic 1–200 apartment range assignment,
  private responses, validation and Docker/release packaging.
- DONE: PR #49 added private administrator phase-selector buttons. Apartment
  lists remain restricted to an authorized administrator's private chat.
- DONE: PR #50 added safe, nullable SQLite migration fields for entrance and
  floor; `/apartments place BUILDING NUMBER ENTRANCE FLOOR` requires an existing
  apartment assignment, preserving the assigned phase.
- DONE: PR #51 implemented read-only building, entrance and floor filters with
  bound SQL parameters and bounded output.
- DONE: PR #52 added `/apartments summary` with aggregate counts by building
  and phase, without including apartment numbers.
- PASS: on the PR #52 source, 227/227 Python tests, the pinned Ruff and
  maintainability gate, and Python 3.11/3.12/3.13 GitHub CI.
- Production intentionally remains at public `0.5.12-hw`; these additions
  have been merged to GitHub `main`, not deployed or hardware-accepted.
  No live SQLite migration or Telegram command change was performed.
- NEXT: plan a versioned candidate release and non-production migration smoke
  on a copied SQLite backup, then conduct controlled deployment acceptance
  without inducing an electrical event.
- NEXT: extend private Telegram management with optional structured CSV import
  and safely gated phase-group notifications, including explicit recipient
  authorization and no apartment lists in public status channels.
- SAFETY: a phase assignment is administrative metadata, never a measurement
  proving that an individual apartment has electricity.
- PENDING: full physical low-voltage/recovery acceptance for the software
  version following the hardware-accepted `0.5.6-hw` checkpoint.

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

## 2026-11-04 current checkpoint

- DONE: public `v0.5.12-hw` release is published at
  https://github.com/iSentinel/shelly-voltage-telegram/releases/tag/v0.5.12-hw.
  The tag points to commit `d5819ed9b110f887fc032c62dfadad70cff20b0f`.
- DONE: Source Release and Container Image GitHub workflows passed.
  Published source archive SHA256:
  `b716d59efff5858a2b7dfef1b8ee9bf47dfd1b744920fd77b560a037f2feef44`.
- DONE: public read-only local-connectivity troubleshooting guide merged
  through PR #39; CI passed on Python 3.11, 3.12 and 3.13.
- DONE: device IP is now DHCP-reserved. A local Pro EM-50 HTTP RPC check
  succeeded after reservation. Do not infer Docker readiness from device
  reachability; check `/ready` independently.
- DONE: independent Cosign verification for the immutable v0.5.12-hw
  GHCR digest `sha256:e725d628db6869c39225c5ff5a123bbd94399414fd8be15e13df8cfd9ab2fa46`.
  The signer identity matches the tag build workflow and the GitHub OIDC issuer.
- DONE: independently retrieved and SHA256-checked OCI manifests, both
  linux/amd64 and linux/arm64 image configs and each digest-linked in-toto
  attestation. Source revision matches tag commit `d5819ed9b110f887fc032c62dfadad70cff20b0f`.
  Both architectures include SPDX 2.3 packages and SLSA provenance linked
  to their exact image-manifest digests. Verification workflow run
  `41860089060` passed; Python 3.11/3.12/3.13 CI `41860089059` passed.
  Reusable independent verifier merged through PR #41.
- PENDING: physical voltage-threshold and stable-recovery acceptance after
  a natural electrical event; latest full hardware checkpoint is 0.5.6-hw.
- PENDING: physical Pro 3EM / Pro 3EM-3CT63 acceptance.
- ON HOLD: optional Mini App publication without a public HTTPS endpoint
  and server-side Telegram `initData` verification.

## 0.5.12-hw - software candidate / live acceptance

- PASS: exact GitHub `main` source deployed in production and source parity
  verified on the physical Pro EM-50 without changing monitoring behavior.
- PASS: quality gate, 200/200 unit tests, healthy/ready service, fresh read-only
  voltage samples, SQLite quick_check, zero synthetic 0 V rows and Telegram
  connectivity after recovering from a changed local DHCP address.
- Operational follow-up: reserve the device IP by its MAC address on the LAN
  DHCP server, so an electrical outage cannot change the address unnoticed.
- Physical voltage-threshold/recovery behavior remains awaiting a natural event.
- Prepared private `/start` menu, combined Status/settings and model-specific
  voltage topology. Pro EM-50 has one voltage phase displayed as L.
- Main CI and image workflow completed successfully. Public release
  metadata is promoted for `v0.5.12-hw`; physical acceptance is pending.
- Full electrical-transition hardware checkpoint remains 0.5.6-hw.
- Mini App preparation remains unpublished, as requested.


### 0.5.11-hw public release verification

- DONE: tag `v0.5.11-hw` points exactly to release commit `391be951b85a98b32a82de423d277f6cff595a1d`.
- DONE: Source Release #9, tag CI #314 and Container Image #80 completed successfully.
- DONE: published source SHA256 `6a1c321682cdf8bf990e2d48fb0c5517076fb9a7d233d3af30aec28c66ca8abe` matched `SHA256SUMS` and an independent rebuild byte-for-byte; archive privacy scan passed.
- DONE: immutable GHCR digest `sha256:df00b90a300ed27e0758554c5c1e2d99b1bc97b5b98f2505057f1a5d05f5a736` contains linux/amd64 and linux/arm64 images.
- DONE: independent Cosign v3.0.6 verification passed for the exact tag workflow identity.
- DONE: both architecture attestations contain SPDX 2.3 SBOMs (122 packages) and SLSA provenance bound to the exact release commit.
- DONE: live source synchronized to the exact public tag; health/ready, SQLite continuity and persisted Telegram settings remained clean.


### 0.5.11-hw compact settings UX

- PASS: replaced the oversized Telegram `/settings` keyboard with five compact top-level sections and focused subpages.
- PASS: navigation edits one message and does not persist settings by itself.
- PASS: old `cfg:*` callback actions remain supported.
- PASS: reset is moved behind an explicit confirmation page.
- PASS: RU/UA/EN labels cover the new menu hierarchy.
- PASS: QUALITY gate and full regression suite: 187/187 tests.
- PASS: clean Python 3.11/3.12/3.13 matrix: 187/187 on each version.
- PASS: installer, rollback, support-bundle, reproducible release and Docker candidate gates.
- PASS: PR #34 merged as exact `main` commit `0f0b5d691f6e8e608d33442e3fe5f42321c62f7b`; PR CI #309, main CI #310 and Container Image #78 passed.
- PASS: exact-main live promotion through the standard installer created rollback snapshot `20261103-031423-0.5.10-hw`.
- PASS: live `/settings` renders exactly five top-level buttons using persisted UA settings; navigation was validated without sending a Telegram message.
- PASS: post-promotion and restart continuity: healthy/ready, Shelly online, Telegram failures 0, SQLite `quick_check=ok`, zero open outages and zero synthetic `0 V` rows; status ID and notification timestamps/settings remained unchanged.
- NEXT: publish `v0.5.11-hw` from promoted release metadata, then independently verify source and signed container artifacts.
- FUTURE: optional Telegram Mini App settings frontend after a public HTTPS endpoint is available and Telegram `initData` is validated server-side.

# Roadmap

Status date: 2026-11-04
Runtime release: `0.5.12-hw` (live; public release published)
Software candidate / production runtime: `0.5.12-hw`
Latest full hardware checkpoint: `0.5.6-hw`
Published public release: `0.5.12-hw` (source, Cosign and OCI attestations verified)


## Current next steps

- DONE: merge the persistent edit-window implementation (PR #22) after explicit user approval.
- DONE: confirm physical Pro EM-50 readings, Telegram diagnostics and SQLite integrity.
- DONE: confirm no open outages or synthetic zero-voltage rows; save a verified online backup.
- DONE: software validation with 158 unit tests and Python 3.11/3.12/3.13 CI.
- DONE: publish v0.5.7-hw from approved source 874fc45d81bed3c41ac0005fd801d4e9db291611.
- DONE: merge PR #25 for `0.5.8-hw`; CI and Container Image workflows passed on exact `main` source `b446604a8e84ddc1a35fa30d375a83f336608f0f`.
- DONE: publish `v0.5.8-hw` from exact release commit `14f85134167b09c697a41c47f17077d9ef0a77b9`.
- DONE: independently verify the published source archive, multi-architecture GHCR digest, Cosign signature, SPDX SBOMs and SLSA provenance.
- DONE: install the exact public release through `scripts/install-release.sh` on the live Pro EM-50 deployment; checksum, restart, SQLite and Telegram continuity checks passed.
- DONE: `0.5.9-hw` software/live gate — 15-minute stable-recovery rotation, 174 tests, Python 3.11/3.12/3.13, reproducible release, Docker candidate, exact-main deployment, rollback snapshot and restart continuity all passed.
- DONE: verify all 57 source files, archive checksums, amd64/arm64 images, SBOM, provenance and Cosign signature.
- DONE: prepare `0.5.9-hw` public release metadata after software/live validation.
- DONE: publish exact `v0.5.9-hw` tag on `c92d86006f17441e42a0a33d0b4b7e56f213aa82`; Source Release `41544388759`, Container Image `41544388518` and tag CI `41544388631` all passed.
- DONE: independently verify source archive SHA256 `b73e4e958de960031cd4cac2be1cef842cf5c56c4ddd492e88e053b952ed31dc`, byte-for-byte reproducibility, GHCR digest `sha256:eb563f8b8243b3edfefb4c977f2629adca41c35f4a40bd48bca45c231ce3f8bb`, Cosign v3.0.6 signature, amd64/arm64 images and SPDX/SLSA attestations.
- DONE: install exact public `v0.5.9-hw` through `scripts/install-release.sh`; checksum, exact-tag source parity, rollback snapshot, restart, SQLite and Telegram continuity all passed.
- DONE: `0.5.10-hw` extends the existing read-only recovery acceptance CLI with `stable-baseline` / `stable-verify` so the 15-minute recovery rotation can be physically accepted on the next natural event without controlling Shelly or mutating SQLite.
- DONE: live pre-`0.5.9-hw` recovery context migrated successfully; status rotated silently, recovery metadata cleared, audible cooldown unchanged, restart continuity and rollback snapshot integrity passed.
- DONE: publish `v0.5.10-hw` from release commit `aff36d905fabab29e75d01cf37fb8d7efa14b6c0`; Source Release #8, Container Image #77 and tag CI #307 passed.
- DONE: independently verify `0.5.10-hw` source SHA256 `4e0e39da5d2add3985a37fbbc6375c62bea49d2a626693002600eb3aeb873240`, reproducibility/privacy, GHCR digest `sha256:b1a1771c3c35bcb6cac4977773394e88b29b37800087cf58127dd266c986c880`, Cosign signature, amd64/arm64 SPDX 2.3 SBOM and SLSA provenance.
- DONE: `0.5.11-hw` compact settings software/live gate and restart continuity passed on physical Pro EM-50 without changing persisted Telegram settings.
- DONE: `v0.5.11-hw` public source and signed multi-architecture image independently verified.
- CONTINUE: observe normal operation without inducing power cuts or voltage changes.
- WAITING FOR HARDWARE: physical Pro 3EM / 3CT63 L1/L2/L3 acceptance.
- Full physical voltage-transition acceptance for the new edit-window behavior remains outstanding.

These current steps take precedence over older historical version notes below.
`RELEASE_VERSION=0.5.12-hw` is promoted as the next public release target; the previous verified release is `0.5.11-hw`.
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
### 0.5.8-hw Telegram usability stage

- PASS: finalized event summary after the fixed edit window.
- PASS: persistent RU / UA / EN Telegram UI.
- PASS: administrator loud / silent / edit notification tests.
- PASS: restart-persistent unstable-grid indicator (4 transitions / 15 min).
- PASS: QUALITY GATE, 168/168 tests, installer/rollback/support fixtures, reproducible release archive, container build/import and live Pro EM-50 smoke.
- PASS: restart continuity; Telegram status message ID preserved; SQLite quick_check OK; no new outage and no synthetic `0 V`.
- PASS: live Telegram silent/edit diagnostics; real alert sound cooldown remained unchanged.
- SECURITY: protect `main` with required checks `test (3.11)`, `test (3.12)`, `test (3.13)` and `build`, block force-push/deletion, and require PRs. Application is pending GitHub repository Administration permission; the connected integration currently receives HTTP 403 on branch-protection APIs.
- NEXT: publish candidate PR and observe a natural threshold/recovery transition for hardware acceptance.
- BLOCKED separately: physical Pro 3EM / 3CT63 acceptance until hardware is installed.

