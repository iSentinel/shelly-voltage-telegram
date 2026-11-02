# Release status

## 0.5.5-hw

- Current software/public-release candidate: `0.5.5-hw`.
- Current production live checkpoint remains hardware-accepted `0.5.4-hw`.
- Runtime monitoring code is unchanged from the accepted 0.5.4 deployment.
- Versioned GHCR images are keyless-signed with Sigstore Cosign using GitHub OIDC.
- RC2 signing acceptance passed in GitHub Actions run `41373145505`.
- Independent Gateway verification passed for digest
  `sha256:df534c7b9b30e2a8f3dfc617b07a0ff6837ecc15b285331dc13e211130dcca4d`.
- CI covers Python 3.11, 3.12 and 3.13; source-release, installer, rollback,
  support-bundle redaction and reproducibility gates remain green.
- Public release artifacts exclude deployment `.env`, secrets, SQLite data,
  Telegram identifiers, Shelly credentials and local validation artifacts.

Final `v0.5.5-hw` publication is the remaining release action. Production does
not need an application upgrade for this supply-chain-only release.

Physical three-phase acceptance remains pending until a Pro 3EM / Pro 3EM-3CT63
is installed.
