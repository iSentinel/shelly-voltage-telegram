# Release status

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
