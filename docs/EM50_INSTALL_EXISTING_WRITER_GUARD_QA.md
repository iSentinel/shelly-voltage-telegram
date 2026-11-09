# Fail closed on direct installation over a running SQLite writer

## P0 finding

The release installer validates source archive checksums and then
directly invokes install.sh. The previous install.sh main flow could
run Docker package setup, snapshot_upgrade, copy_source and
docker compose up without checking that the existing Shelly
writer was quiesced. This is a bypass of the separate release
readiness gate and can invalidate the source+SQLite backup pair.

## Source-only guard

Before calling install_docker or touching any project files, an
existing installation (TARGET/VERSION present) requires a verifiably
stopped Docker container named shelly-voltage-bot. Docker inspect
must return exactly false for State.Running; true, unknown or
a failed query aborts. A fresh install has no old writer to stop.
A dry-run prints the required prerequisite without invoking Docker.

This is a defense in depth safeguard, NOT a release authorization.
A stopped container is not enough: the operator must also ensure
no other SQLite writer exists and complete full-history backup
parity, independent code review, real-client Telegram visual
acceptance, and a signed source+database rollback plan.

## Safety

The synthetic tests source only isolated installer functions and
place a fake docker command first in PATH. They cannot invoke
install_docker, snapshot_upgrade, systemctl, real Telegram or Shelly.
The production instance remains online on 0.5.13-hw.
