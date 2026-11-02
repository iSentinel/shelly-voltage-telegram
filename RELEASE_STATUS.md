# Release status

## 0.5.8-hw — usability candidate

- Current software candidate: `0.5.8-hw`; current public release remains `0.5.7-hw`.
- Last fully hardware-accepted electrical-transition checkpoint remains `0.5.6-hw`.
- Adds event summaries, RU/UA/EN Telegram UI, notification diagnostics and unstable-grid indication.
- Software/release gates and live Pro EM-50 smoke PASS: 168/168 tests, reproducible archive, image import, SQLite continuity, restart continuity and Telegram continuity.
- Public release promotion remains separate; the electrical-transition hardware checkpoint remains `0.5.6-hw` until a natural threshold/recovery event is observed.

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
