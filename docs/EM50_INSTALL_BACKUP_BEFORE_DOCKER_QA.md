# Pre-upgrade recovery snapshot must precede Docker installation

## RED finding

Existing installation flow placed install_docker before snapshot_upgrade.
When Docker Engine or Compose was missing, apt-get or systemctl
could modify the host environment before the installed application
source and SQLite data had been captured and checked. A failure
while taking the snapshot would therefore leave a partially changed
host without a verified restore point.

## Fail-safe phase order

After existing target-path, protected-file, VERSION and stopped-writer
preflights, snapshot_upgrade now runs BEFORE install_docker and
all source-copy, configuration, systemd and compose deployment stages.
This does not change fresh-install behavior: without an existing
VERSION, snapshot_upgrade remains a safe no-op.

## Independent acceptance

Two new isolated main() harness tests replace all stage actions
with inert functions: one requires snapshot before Docker on success,
the other forces snapshot failure and proves Docker and source-copy
stages were not reached. Both tests were RED against the previous
installer and GREEN with the reordered stages.

- Writer-gate regression: 8/8 PASS.
- Full Python 3.13 isolated QA: 1330/1330 PASS.
- Ruff 0.14.2 and quality gate PASS.
- Four installer dry-run cases and Bash syntax PASS.

Draft stacked on PR #288, sibling PR #280 must also pass integration.
No production upgrade, Docker package change, service restart,
SQLite write, backup capture or Telegram/Shelly interaction.
Release remains gated by human source review, stopped-writer
complete SQLite/source history, protected YASNO setup, real
Telegram-client acceptance and signed deploy/rollback authorization.
