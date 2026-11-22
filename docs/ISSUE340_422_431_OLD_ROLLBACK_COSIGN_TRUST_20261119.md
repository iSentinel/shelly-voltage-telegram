# Issues 340/422/431: trust the old rollback Cosign binary

Date: 2026-11-19, Europe/Kyiv
Scope: QA source-only; no production update or runtime change.

## Gap

The legacy scripts/signed-rollback-pair-preflight.py invoked a
literal /usr/local/bin/cosign command when asked to check the
old OCI release signature. It did not first verify the executable
owner, path, permissions or version. Thus a replaced local
executable could falsely return exit 0 and cause the QA preflight
to label the old OCI signature verified.

## New read-only fail-closed gate

trusted_cosign() now requires:
- exact /usr/local/bin/cosign regular file with no symlink;
- root ownership and no group/other write permissions for the
  binary and every ancestor directory;
- executable permission;
- successful bounded cosign version command;
- version >= v3, matching the primary source release verifier.

Missing/untrusted paths, unsupported versions or command failure
return OLD_COSIGN_* blocking errors. After this gate, the
preflight still checks the exact source Git SHA, refs/tags/vVERSION,
OIDC issuer, workflow identity, repository and immutable OCI digest.
No verification failure can silently degrade into SHA256-only mode.

Production rollback is NEVER authorized by this QA status. A
source archive can be checked for its version and checksum while
its real authenticated source provenance remains unverified.

## Isolated QA evidence

- Combined old rollback and new pair policy tests: 65/65 PASS.
- Missing binary and pre-v3 executable fail-closed checks PASS.
- Ruff 0.14.2, project maintainability and formatting gates PASS.
- No actual release signed. No production Docker, systemd, SQLite,
  Telegram or Shelly restart.
