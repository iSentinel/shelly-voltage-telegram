# YASNO staging and active readiness: Docker health and privilege guard

## Reproduced readiness gap

The YASNO Docker preflight checked a running, unpaused reader, an
approved read-only cache bind and restricted UID/capabilities, but
did not inspect Docker State.Health.Status or HostConfig.SecurityOpt.

With its declared healthcheck unhealthy, starting or missing, a
container could pass staging. Equally, a container without
no-new-privileges:true passed even though the hardened production
Compose configuration explicitly requires that security option.

## Guard behavior

Both staging and active phases now require
State.Health.Status=healthy and an explicit Docker
HostConfig.SecurityOpt entry no-new-privileges:true.
Missing or malformed health state, unhealthy/starting healthcheck
and missing/false privilege protection fail closed before YASNO
recipient data is accessed.

The existing production configuration includes the required
healthcheck and no-new-privileges:true. Fixture checks do not
access Docker, YASNO provider data, secrets or Telegram.

## Evidence

- RED: seven wrong PASS variants (four unhealthy/missing health
  states and three disabled/missing privilege protections).
- GREEN: all seven blocked; valid healthy fixture passes.
- Focused YASNO Docker preflight tests: 18/18 PASS.
- Isolated Python 3.13 full QA: 1328/1328 PASS.
- Ruff 0.14.2 and project quality PASS.
- Four installer dry-run fixtures PASS; Bash syntax PASS.

Draft source-only change stacked on PR #287, with sibling
PR #280 requiring integrated QA. Release still requires
independent human source review, quiesced full SQLite/source
snapshot, protected YASNO account/recipient, real Telegram
client visual acceptance and signed rollout/rollback. No
production operations, Docker restart, SQLite write,
YASNO activation or Telegram message mutation.
