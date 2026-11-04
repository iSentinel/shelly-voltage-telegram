# Automatic capture of natural Shelly event baselines

## Purpose

A short power outage can end before a remote Gateway job or hourly check
connects. Run the existing acceptance tool locally once per minute to
capture a baseline **while the event is still active**. No control RPCs are
sent to Shelly. The tool does not edit Telegram messages, restart the bot,
or modify the monitoring SQLite database.

The tool records one private JSON file for each real open connectivity
outage, and one different file when a real voltage-recovery timer is active.
It does **not** declare hardware acceptance automatically. The saved
checkpoint must later be verified against the same outage and a continuous
recovery period.

## Prepare on a host with the matching release code

Install a version of the application containing the new
\`capture-once\` command. Do not copy only the script into a different
production release without validating its release manifest and rollback
procedure.

Make sure the SQLite path is correct and the bot is operational. For the
standard Proxmox LXC deployment, the commands are:

\`\`\`bash
cd /opt/shelly-voltage
python3 scripts/recovery-acceptance.py --help
install -d -m 0700 validation/natural-baselines
python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  capture-once --output-dir validation/natural-baselines
\`\`\`

\`status: skip\` is normal when there is no active outage or voltage
recovery timer. \`status: pass\` records the checkpoint filename and
whether it was newly created. Unexpected \`status: fail\` has exit code 2.

## Optional systemd timer

Review the units under \`systemd/\` and ensure their paths match your host.
They are **opt-in**; nothing installs or enables them automatically.

\`\`\`bash
cd /opt/shelly-voltage
sudo install -d -m 0700 validation/natural-baselines
sudo install -m 0644 systemd/shelly-natural-baseline.service \
  /etc/systemd/system/shelly-natural-baseline.service
sudo install -m 0644 systemd/shelly-natural-baseline.timer \
  /etc/systemd/system/shelly-natural-baseline.timer
sudo systemctl daemon-reload
sudo systemctl enable --now shelly-natural-baseline.timer
sudo systemctl list-timers shelly-natural-baseline.timer
\`\`\`

The timer runs the read-only check approximately once per minute, on
the same LXC as the SQLite volume. It does not depend on remote Gateway
availability. It never manipulates electrical power.

To test one invocation without changing the monitor:

\`\`\`bash
sudo systemctl start shelly-natural-baseline.service
sudo systemctl status shelly-natural-baseline.service --no-pager
sudo journalctl -u shelly-natural-baseline.service -n 20 --no-pager
\`\`\`

To stop automatic capture without altering the Telegram monitor:

\`\`\`bash
sudo systemctl disable --now shelly-natural-baseline.timer
\`\`\`

## Security and acceptance

- The checkpoint directory must have permission \`0700\`. New files are
  created with \`0600\`; existing filenames are never overwritten.
- Telegram message IDs are stored only as SHA-256 fingerprints. Keep these
  files private; they also contain measurement counters and timestamps.
- The SQLite database is opened read-only by the acceptance tool.
- The monitor and Shelly may be offline when an outage baseline is captured.
  Stable recovery baselines require both healthy local HTTP endpoints.
- A connectivity outage can come from a changed IP or local network failure.
  It is not by itself proof of the exact duration of a mains outage.
- Use \`baseline\` / \`verify\` for a saved open-outage baseline, and
  \`stable-baseline\` / \`stable-verify\` for a saved voltage-recovery timer.
  Ensure the outage identity and start/end times have not changed.
- Keep older baselines as evidence; do not repurpose them for later events.

The hardware checkpoint is completed only after the real validation
commands pass on the correct saved event, with fresh measurements, no
synthetic \`0 V\` samples, and the expected Telegram status behavior.
