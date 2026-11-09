# YASNO reader: only a running container satisfies staging or active QA

## Reproduced gap

The prerelease Docker-reader gate validated the non-root user,
read-only bind mount and disabled/enabled YASNO flags, but never
checked Docker's State object. A stopped, paused, restarting, dead
or state-less fixture could pass staging readiness despite the
documented requirement for an operating reader.

## Change

Require State.Running=true and State.Paused=false,
State.Restarting=false and State.Dead=false before inspecting
the YASNO feature flags, bind mounts and any recipient cache.
Reject a missing or malformed State object. Both staging
and active phases are covered. No Docker or provider calls
are made by the fixture-based test.

## Evidence

- RED: five unsafe Docker-state scenarios accepted by staging.
- GREEN: all unsafe states rejected and valid running staging accepted.
- Stopped active reader rejected before any protected cache read.
- Isolated Python 3.13: 1323/1323 unit tests PASS.
- Focused YASNO reader gate: 13/13 PASS.
- Ruff 0.14.2, project quality and all installer dry-run fixtures PASS.

This is draft source-only preparation on PR #285. The live Shelly
monitor is unchanged, YASNO remains unprovisioned and disabled.
Release is blocked on independent source review, quiesced complete
SQLite and source backup parity, protected YASNO producer/recipient
setup, real Telegram-client visual acceptance and signed rollout
and rollback approval.
