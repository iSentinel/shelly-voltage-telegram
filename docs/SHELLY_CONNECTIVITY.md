# Shelly shows online in its app, but Telegram says offline

This guide diagnoses local Shelly RPC, the running Docker configuration,
the monitoring loop, and Telegram delivery **in that order**.

The Shelly app can reach a device through its cloud service. This does not
prove that a container can reach the device over the local network.
Do not restart the bot or change Shelly settings until the failing layer is
identified. Diagnostic checks below are read-only. The optional remediation
in section 5 changes the Docker service only when you explicitly run it.

## 1. Confirm the active address

Reserve the Shelly address in the router's DHCP server so power restoration
cannot silently move it to another address.

On a Docker host, run:

```bash
cd /opt/shelly-voltage
grep '^SHELLY_HOST=' .env
docker exec shelly-voltage-bot printenv SHELLY_HOST
```

The two addresses must match. A change to `.env` does **not** automatically
update an already running Docker container. Do not print the entire `.env`
or all container environment variables because they can contain secrets.

For deployments inside Proxmox LXC, first enter the application container.
Replace `<CTID>` with your LXC ID:

```bash
pct enter <CTID>
```

## 2. Check the local Shelly API

Run this **inside the LXC or Docker host** with the reserved IP address:

```bash
SHELLY_IP=192.168.1.50  # replace with your device's reserved address
curl -sS --max-time 5 -w '\nHTTP %{http_code}\n' \
  "http://${SHELLY_IP}/rpc/EM1.GetStatus?id=0"
```

For a Pro EM-50, expect HTTP 200 with valid `voltage` and `freq` fields.
Other models use a different status RPC; use the selected model's endpoint.

If the local RPC fails, check the DHCP lease, LAN/VLAN isolation, firewall,
device IP, and route from the LXC. Do not confuse Shelly Cloud availability
with local RPC availability.

## 3. Check from the running bot

```bash
docker exec shelly-voltage-bot python /app/app.py probe
curl -sS --max-time 5 -w '\nHTTP %{http_code}\n' \
  http://127.0.0.1:8080/ready
curl -sS --max-time 5 http://127.0.0.1:8080/health
```

`probe` uses the meter type, host, authentication, and timeout configured
**inside the running bot**. It reads Shelly without sending Telegram messages.

- `probe` fails while direct RPC works: investigate the active Docker
  `SHELLY_HOST`, meter type, sensor ID, and authentication.
- `probe` works but `/ready` is HTTP 503: investigate the monitoring loop,
  its latest successful sample, and recent errors.
- `/ready` is HTTP 200 with `ready: true` and a small `age`: the running
  monitoring loop is receiving fresh measurements.
- Docker `healthy` or HTTP 200 on `/health` is **not** proof that measurements
  are fresh. Use `/ready` for that decision.

The local HTTP server normally binds only to `127.0.0.1:8080`, inside the
Docker host or LXC. Do not expose it publicly to simplify testing.

### Use current fields from `/status.json`

Older clients may use `voltage` and `frequency` as the
**last valid readings**. Those values intentionally remain stored
when Shelly becomes unavailable. New read-only fields distinguish
them from live telemetry:

- `live_voltage` and `live_frequency` are `null` when the
  last reading is stale or the device is offline.
- `sample_is_stale` is true if there is no valid fresh reading.
- `sample_age_seconds` reports the age of the last valid reading
  in seconds, or `null` before the first successful sample.

The same current-versus-cached fields are also available for each
configured `phases.L1`, `phases.L2` and `phases.L3` entry, so a
multi-phase dashboard can distinguish an offline line from a live
line. An unconfigured phase remains
`{"connectivity": "not_configured"}` without invented measurements.

New dashboards should use `live_voltage` and
`sample_is_stale` instead of displaying cached `voltage`
as current. The existing `/ready` HTTP 503 behavior and
Prometheus NaN values remain unchanged. These fields are
informational; they do not control electricity or send
Telegram messages.

## 4. If measurements are fresh but Telegram is stale

In the private Telegram control chat, send `/status`. Compare that response
with the status in the publication channel. Check log warnings:

```bash
docker logs --since=1h --tail=500 shelly-voltage-bot 2>&1 \
  | grep -Ei 'Telegram.*failed|status update failed|status recreation' \
  | tail -30 || true
```

The monitor normally edits its saved Telegram status message and recreates
a missing message when Telegram reports that it cannot be edited. If the
private status is current but the channel is not, investigate Telegram
delivery, chat permissions, the saved message ID, and any pinned old message.

Review logs locally: do not publish tokens, chat identifiers, private IP
addresses, or full environment files in public issues.

## 5. Changing a confirmed stale IP

Only after the correct IP has been verified, back up the private
`/opt/shelly-voltage/.env`, update **only** `SHELLY_HOST`, and check Compose.
Recreate just the bot service if the running environment needs to pick up the
new address:

```bash
cd /opt/shelly-voltage
docker compose config -q
docker compose up -d --no-deps --force-recreate shelly-voltage
```

Then repeat `probe` and `/ready`. Keep the SQLite volume, secret files,
history, and Telegram state intact. Do not force a power outage, induce
voltage changes, or issue Shelly control RPCs for diagnostics.

## 6. Natural power-recovery acceptance

After a **naturally occurring** outage and recovery, validate fresh samples
and the status-message recovery behavior against
[`ROADMAP.md`](../ROADMAP.md) and
[`scripts/recovery-acceptance.py`](../scripts/recovery-acceptance.py).
Do not advance the full hardware checkpoint solely from an online RPC test.
