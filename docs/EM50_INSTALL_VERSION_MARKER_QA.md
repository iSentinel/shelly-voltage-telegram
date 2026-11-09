# Require a trustworthy VERSION marker on existing Shelly deployments

## Reproduced P0 failure

A previously installed application may retain `compose.yaml`, `app.py`,
Telegram configuration and an active SQLite writer after `VERSION` has
been lost. The prior `require_quiesced_upgrade` and `snapshot_upgrade`
helpers both returned early when VERSION was missing. A disposable
source-only fixture reproduced successful exit despite an existing
application tree: neither quiescence nor upgrade snapshot was required.

## Fail-closed change

Before any Docker setup or write, treat an existing nonempty application
directory without VERSION as an unknown deployment. Only a fresh directory
or a preconfigured installation containing .env, .env.example and secrets/
may omit VERSION. A present VERSION must be a regular independent file,
never a symlink, directory or hardlink. Otherwise fail before installation
to preserve source and complete SQLite history.

## Evidence

- RED: unversioned source-containing tree bypassed writer/snapshot guards.
- GREEN: 4 new focused tests cover existing sources, poisoned markers,
  valid new installs and preflight ordering.
- Python 3.13 isolated quality environment: 1316/1316 PASS.
- Ruff 0.14.2 and project quality gate PASS.
- Installer dry-run fixture: 4/4 PASS; shell syntax PASS.

The draft does not authorize production deployment or merge. Manual
source review, stopped-writer full-history backup and signed rollback
acceptance, YASNO protected recipient and Telegram real-client visual
approval remain required. Production Shelly, Docker and SQLite were not
modified.
