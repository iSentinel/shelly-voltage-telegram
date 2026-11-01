# Shelly Voltage Telegram

Local-first mains voltage monitoring for **Shelly Pro EM-50** with a stable Telegram status message that is edited in place.

Current software candidate: **0.5.3-hw**. Hardware-accepted live checkpoint: **0.4.11-hw**. Candidate 0.5.0 adds a three-phase data model and the full analytics/operations feature set; live promotion is intentionally held while L1 hardware is offline due to a mains outage.

## Architecture

```text
L1 Shelly Pro EM-50    L2 sensor (optional)    L3 sensor (optional)
        \                 |                     /
         +-----------------+--------------------+
                           | read-only RPC
                           v
                  shelly-voltage-bot
      |-- SQLite history
      |-- /health /ready /metrics
      |-- daily min/max/avg
      `-- Telegram Bot API (optional)
             `-- editMessageText on one stable message
```

Shelly Cloud, Home Assistant, MQTT, Zigbee and an ESP32 are not required.

## Behavior

- Poll Shelly every 10 seconds by default.
- Read-only monitoring: the service calls only `EM1.GetStatus` and never sends Shelly control/configuration RPCs.
- Store only valid voltage/frequency samples.
- Never write a fake `0 V` sample when Shelly is unreachable.
- Mark the stable Telegram status as offline while keeping the last valid measurement.
- Update Telegram every 60 seconds or after a significant voltage change.
- Persist the Telegram status `message_id` in SQLite.
- Recreate the status message if it was deleted or can no longer be edited.
- Persist outage/recovery events and their duration in SQLite.
- Use hysteresis: 3 failed polls to declare offline, 2 successes to recover.
- Keep 90 days of history by default.
- Voltage threshold alerts and availability alerts are independently optional.

### Three-phase model

The existing Shelly is phase `L1`. Two future sensors can be configured independently as `L2` and `L3`:

```text
PHASE_L2_HOST=
PHASE_L2_EM1_ID=0
PHASE_L2_USERNAME=admin
PHASE_L2_PASSWORD_FILE=/run/secrets/shelly_l2_password
PHASE_L3_HOST=
PHASE_L3_EM1_ID=0
PHASE_L3_USERNAME=admin
PHASE_L3_PASSWORD_FILE=/run/secrets/shelly_l3_password
```

Blank hosts mean “not configured”; the Telegram status, dashboard, health JSON and Prometheus metrics still show all three phase slots. SQLite schema v3 adds a `phase` key to measurements and outages. Migration from existing v1/v2 databases preserves every historical row as `L1`.

### Advanced monitoring

- low-voltage alarm dwell: 15 seconds by default; recovery dwell: 30 seconds;
- configurable warning/critical low and high voltage levels;
- independent L1/L2/L3 outage journals and alert state;
- daily summary at 22:55 Europe/Kyiv by default;
- `/quality` voltage-quality statistics;
- `/chart [L1|L2|L3] [hours]` PNG chart, up to 168 hours;
- `/export [L1|L2|L3] [hours]` CSV export;
- `/outages` recent outage history;
- `/device` read-only firmware, uptime and clock-skew diagnostics;
- `/maintenance 60` and `/maintenance off` for temporary alert suppression;
- `/audit` admin settings audit log;
- hourly aggregate rollups, WAL checkpoint/optimize and conditional VACUUM;
- backup freshness in `/health`;
- monitor-loop and Telegram failure watchdog metrics;
- local read-only dashboard at `http://127.0.0.1:8080/dashboard`;
- stable status-channel message can be pinned automatically;
- read-only channel buttons provide refresh, today stats and outage summary;
- raw samples follow `RETENTION_DAYS`; hourly aggregates are created before pruning and retained long-term.

### Supported Shelly models

The bot is not tied to one physical deployment. The administrator can select any supported model from `/settings`; the network address and credentials are supplied separately through deployment configuration.

| Model | Phases | Read-only RPC backend |
| --- | ---: | --- |
| Shelly Pro EM-50 | 1 | `EM1.GetStatus` |
| Shelly EM Gen3 | 1 | `EM1.GetStatus` |
| Shelly PM Mini Gen3 | 1 | `PM1.GetStatus` |
| Shelly Plus PM Mini | 1 | `PM1.GetStatus` |
| Shelly Plus 1PM | 1 | `Switch.GetStatus` |
| Shelly 1PM Gen3 | 1 | `Switch.GetStatus` |
| Shelly 1PM Gen4 | 1 | `Switch.GetStatus` |
| Shelly 1PM Mini Gen3 | 1 | `Switch.GetStatus` |
| Shelly 1PM Mini Gen4 | 1 | `Switch.GetStatus` |
| Shelly Pro 3EM / Pro 3EM-3CT63 | 3 | `EM.GetStatus` |

For public/GitHub deployments, the repository contains no bot username, bot token, chat/channel ID, admin ID, Shelly IP, or password. `.env.example` contains placeholders only; real values live in `.env` and `secrets/`, both excluded from Git and release archives.

### Selecting the Shelly module in Telegram

An administrator can open `/settings` and select the monitoring backend directly:

- `📟 Pro EM-50` — one primary Pro EM-50 plus optional separately configured L2/L3 sensors;
- `⚡ Pro 3EM / 3CT63` — one Shelly Pro 3EM / Pro 3EM-3CT63 supplying L1/L2/L3.

The selected backend is stored in SQLite and takes effect immediately without a container restart. Network addresses and credentials remain outside Telegram in `.env`/secret files. Runtime settings are reloaded from SQLite on service startup, so the selected module and notification policy survive restarts. Only configured Telegram administrators can change the module.

### Shelly Pro 3EM-3CT63

The preferred future three-phase source is **Shelly Pro 3EM-3CT63** in its normal `triphase` profile. Configure:

```text
SHELLY_METER_TYPE=pro3em
SHELLY_HOST=<Pro-3EM address>
```

The bot performs one read-only `EM.GetStatus?id=0` call per polling cycle and maps phase A → `L1`, B → `L2`, C → `L3`. All three valid samples receive the same local poll timestamp. Missing/null phase values are treated as unavailable and are never converted to synthetic `0 V` rows. The existing `pro_em50` + optional separate L2/L3 mode remains supported.

### Recovery status rotation

When the primary meter changes from confirmed `offline` to confirmed `online`, the bot deletes the outage-era stable channel status message, clears its persisted ID and immediately creates a fresh online status message. The new message ID becomes the stable status ID for future edits. This avoids leaving the outage post as the long-lived channel status after power returns.

## Validated platform

The hardware checkpoint has been validated with:

- Shelly Pro EM-50, model `SPEM-002CEBEU50`;
- Shelly stable firmware 2.1.1;
- Debian 13 LXC;
- Docker 26.1.5;
- Docker Compose 2.26.1;
- Python 3.13 container image;
- Europe/Kyiv timezone.

See [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md).

## Install

Dry run:

```bash
sudo ./scripts/install.sh --dry-run --non-interactive
```

Interactive install/update:

```bash
sudo ./scripts/install.sh
```

The installer preserves `.env`, secret files, SQLite data and backups.

## Configuration

Copy the example if needed:

```bash
cp .env.example .env
```

Then use the secure configurator:

```bash
sudo ./scripts/configure.sh
```

The Telegram bot token is entered with hidden input and stored in a dedicated secret file instead of a command-line argument or shell history.

The production Compose configuration mounts:

```text
/run/secrets/telegram_token
/run/secrets/telegram_chat_id
/run/secrets/shelly_password
```

Direct environment values remain supported for development, but secret files are preferred.

Telegram notification policy is configurable in `.env`:

```text
TELEGRAM_SILENT_BY_DEFAULT=true
TELEGRAM_QUIET_START_HOUR=23
TELEGRAM_QUIET_END_HOUR=7
LOW_VOLTAGE_ALERT_ENABLED=true
LOW_VOLTAGE_ALERT_V=180
LOW_VOLTAGE_RECOVERY_V=185
```

With these defaults, ordinary bot messages are silent. A one-shot automatic alert below 180 V may notify normally from 07:00 through 22:59. From 23:00 through 06:59 all Telegram sends are forced silent. The low-voltage state resets only after recovery to at least 185 V, preventing repeated alerts around the threshold.

## Run manually

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f
```

## Service management

```bash
systemctl status shelly-voltage-compose.service
systemctl restart shelly-voltage-compose.service
```

The LXC may reboot independently. Docker plus the Compose systemd unit restore the monitor automatically.

## Health and metrics

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/metrics
curl http://127.0.0.1:8080/dashboard
```

`/health` is process liveness and is used by the Docker healthcheck. `/ready` is data readiness and becomes HTTP 503 when Shelly is offline or the last valid sample is stale. Optional availability alerts are controlled separately with `AVAILABILITY_ALERTS_ENABLED`.

## Diagnostics

Hardware/SQLite/Telegram doctor:

```bash
docker exec shelly-voltage-bot python /app/app.py doctor
```

24-hour report:

```bash
./scripts/soak-report.py --hours 24 --json
```

Redacted support bundle:

```bash
./scripts/support-bundle.sh
```

Online SQLite backup:

```bash
./scripts/backup.sh
```

Pre-upgrade snapshots are created automatically during an update. To validate a
rollback without starting the service:

```bash
sudo ./scripts/rollback.sh --dry-run
```

To restore the newest pre-upgrade snapshot:

```bash
sudo ./scripts/rollback.sh
```

A systemd timer runs the backup daily and retains the newest 14 copies by default.

## Telegram commands

When Telegram credentials are configured:

- `/start`
- `/help`
- `/voltage`
- `/status`
- `/today`
- `/minmax`
- `/history`
- `/outages`
- `/health`
- `/settings`
- `/chart`
- `/export`
- `/quality`
- `/summary`
- `/maintenance`
- `/audit`
- `/device`

### Separate status channel

The private control chat and public/private status channel can be configured independently:

```text
TELEGRAM_CHAT_ID=<private control chat>
TELEGRAM_STATUS_CHAT_ID=-1001234567890
```

Commands, `/settings` and admin callbacks stay in the control chat. The stable voltage status is created and edited in `TELEGRAM_STATUS_CHAT_ID`. The channel status uses a separate SQLite key, `telegram_status_message_id`, so the private-chat message history is not overwritten.

### Telegram settings menu

`/settings` shows the current notification policy. Users that are not listed in `TELEGRAM_ADMIN_USER_IDS` get read-only status; only admins receive inline edit buttons. Changes are stored in SQLite and apply immediately without restarting the container.

Admin controls include:

- silent-by-default ON/OFF;
- quiet-hours ON/OFF;
- quiet start/end by one-hour steps;
- low-voltage alert ON/OFF;
- low-voltage threshold by 5 V steps;
- recovery threshold by 5 V steps;
- outage alerts, daily-summary time, pinning and alert dwell controls;
- reset to `.env` defaults.

Example:

```text
TELEGRAM_ADMIN_USER_IDS=123456789
```

## GitHub one-line install

Install directly from this repository:

```bash
curl -fsSL https://raw.githubusercontent.com/iSentinel/shelly-voltage-telegram/main/scripts/bootstrap.sh | sudo bash
```


## Security

- The container runs as UID 10001.
- Root filesystem is read-only.
- All Linux capabilities are dropped.
- `no-new-privileges` is enabled.
- The health port is bound to `127.0.0.1` on the Docker host.
- Secret files are mode `0600`.
- `.env` and `secrets/` are excluded from Git.
- Support bundles redact token/password/secret/key fields.
- Shelly authentication is supported through HTTP Digest authentication.

Do not expose the Shelly RPC interface directly to the Internet.
