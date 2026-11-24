# Scheduled release checks

After a signed supervisor upgrade verifies the monitor, it installs and
starts the release-check timer shipped with that release. The checker
runs with its own systemd identity and checks for updates once a day.

If the timer setup reports a warning, the monitor keeps running. Resolve
the reported timer problem and rerun the setup using the transaction ID
printed by the upgrade.

```bash
sudo python3 scripts/signed-forward-cutover.py sync-release-check --transaction <id>
systemctl list-timers shelly-voltage-update-check.timer
```

The retry checks the deployed source against its retained signed archive
and preserves local systemd overrides. It does not reinstall or restart
the monitor.
