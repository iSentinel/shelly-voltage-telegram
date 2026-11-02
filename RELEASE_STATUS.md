# Release status

## 0.5.6-hw — software accepted / hardware pending

- Current software candidate: `0.5.6-hw`.
- Current public release: `0.5.5-hw`.
- Current production live checkpoint: hardware-accepted `0.5.4-hw`.
- Candidate/source, public release and live checkpoint versions are separated by
  `VERSION`, `RELEASE_VERSION` and `LIVE_VERSION`.
- Final source publication fails closed until `RELEASE_VERSION` is explicitly
  promoted to match `VERSION`.
- `0.5.6-hw` may be promoted on the current Pro EM-50 after the active blackout recovery baseline verifies and live Telegram/read-only hardware checks pass.
- Read-only Pro 3EM / 3CT63 physical-acceptance CLI is implemented and software accepted; switching production to the `pro3em` backend remains pending physical device installation.
- Local CI-equivalent acceptance: Ruff + quality PASS, 131/131 tests PASS,
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
- Prometheus current voltage/frequency metrics are stale-safe: offline/starting phases export `NaN` rather than replaying the last good value.
- Local dashboard phase cards also hide stale voltage while offline/starting and resume display only after a new recovering/online sample exists.
- Telegram stable status hides last-good V/Hz while offline and shows electrical values again only after a new recovering/online sample is received.

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
