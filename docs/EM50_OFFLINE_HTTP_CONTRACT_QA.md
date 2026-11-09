# Real HTTP offline contract: no stale voltage treated as live

## Real blackout observation

On 2026-11-09 after mains went off, Shelly became unreachable from
Proxmox and LXC 110. The installed 0.5.13-hw monitor remained healthy
but /ready returned HTTP 503 and SQLite stopped gaining measurements.
The last historical sample was 221.9 V / 50.0 Hz. /metrics correctly
returned NaN, and /dashboard did not show 221.9 as a current voltage.

The installed /status.json schema still exposes last-known voltage
without the future candidate's explicit live_voltage,
live_frequency, sample_age_seconds and sample_is_stale fields.
Consumers of the historical voltage field must not treat it as
proof of currently available mains. The candidate adds explicit
fields while preserving backwards-compatible history.

## Local HTTP end-to-end regression

The new fixture binds a disposable HTTP listener exclusively to
127.0.0.1 on an ephemeral port with an isolated SQLite file. It does
not start the Shelly polling loop, send Telegram messages, issue
hardware RPC, or access the production SQLite.

- Offline /status.json includes historical voltage only under legacy
  fields and exposes live_voltage=null, live_frequency=null and
  sample_is_stale=true, including L1 phase.
- Offline /ready is HTTP 503, /metrics exports NaN for V/Hz, and
  /dashboard contains no stale voltage number.
- A first valid post-outage reading is recovery-pending (not ready).
  Only the second consecutive success changes /ready to HTTP 200.
- Local responses are explicitly noncacheable.

## Independent evidence

- Focused HTTP end-to-end tests: 3/3 PASS.
- Disposable combined PR #291 + sibling #292 plus this test:
  Python 3.13 1343/1343 PASS, Ruff 0.14.2, quality gate,
  installer dry-run fixtures PASS.
- One known date-dependent YASNO fixture on standalone PR #291 is
  repaired by the sibling PR #292. Full integrated run passed.
- Real offline acceptance baseline in LXC 110 is private mode 0600
  and fingerprints the stable Telegram status ID without storing it.
- Do not claim current measured voltage while Shelly is offline.

This is a Draft source-only regression stage. No production Docker
restart, deployment, SQLite write, new backup, Shelly command,
YASNO activation or Telegram message/send/edit was performed.
Independent source review, quiesced complete-history snapshot,
protected YASNO setup, real-client Telegram visual signoff and
signed rollout/rollback authorization remain blocking.
