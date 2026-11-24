# 0.5.27-hw

This release updates startup and recovery for Shelly Voltage Telegram.

- The ordinary systemd Compose service holds the updater lock during
  startup and reload. A pending update keeps the monitor stopped for
  signed recovery and preserves the current SQLite database.
- Docker restart policy is disabled for the monitor. Reboot testing with
  Docker 26.1.5 reproduced a monitor start while systemd was rejecting a
  damaged recovery archive.
- The signed single-writer supervisor, three-phase monitoring, graphical
  status and YASNO fixes from 0.5.26-hw are included.

For deployments using the ordinary Compose service, inspect the journal
and restart that service explicitly after an abnormal monitor exit.
Supervised deployment uses its signed cutover procedure. Verify the
source Sigstore bundle and the pinned image against the same release
commit before installation.
