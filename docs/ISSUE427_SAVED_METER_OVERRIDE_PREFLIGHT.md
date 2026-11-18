# Saved Shelly meter selection: fail-closed preflight

## Reproduced operational defect

On 2026-11-18 the owner authorized switching the production bot from
retired Shelly Pro EM-50 (192.168.131.178) to the newly configured
Shelly Pro 3EM-3CT63 (192.168.131.176).

The existing signed image v0.5.25-hw already supported pro3em, but
a new .env and restarted Compose service alone were **insufficient**.
Monitor.initialize() -> load_runtime_settings() loaded a persisted
SQLite meta key telegram_setting_meter_type=pro_em50 which overrode
the updated SHELLY_METER_TYPE=pro3em.

The process was healthy but its Shelly readiness remained HTTP 503,
only L1 was configured and no fresh samples were stored. The physical
three-phase RPC worked, proving a configuration precedence issue.

Under an owner-approved cutover and **stopped sole-writer**, the saved
setting was changed to pro3em in one SQLite transaction with an
append-only settings audit. The same signed image then reached
HTTP 200 ready, acquired all three phases and stored new measurements.
Older EM-50 L1 history and PHOTO identity were preserved.

## Read-only preflight for future hardware transitions

This source-only command checks that the desired meter type agrees with
both .env and any stored Telegram model selection:

~~~bash
python3 scripts/meter-cutover-runtime-guard.py \
  --env-file /opt/shelly-voltage/.env \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  --expected-meter pro3em \
  --jsonl
~~~

It reads SQLite with mode=ro and PRAGMA query_only=ON.
It never modifies .env, SQLite, Docker, systemd or Telegram,
and does not expose other .env values, tokens or user IDs.

- PASS_STATIC_METER_PARITY (exit 0): desired type matches .env;
  saved type matches or is absent. This is **not release approval**.
- BLOCKED (exit 2): a stored Telegram type overrides .env,
  .env differs from intended type, a type is unsupported, or
  a file is missing, linked, unreadable or ambiguous.

The guard is designed to run *before stopping any working container*.
It cannot guarantee a valid physical sensor or replace the independent
hardware acceptance and signed-image release checks.

## Correct conflict resolution

Do **not** automatically delete or ignore a saved preference. A
user-approved hardware cutover first needs read-only physical acceptance,
a newest full-history SQLite backup, a verified stopped writer and an
explicitly audited change of only the stored meter selection. Restore
the app using its existing pinned OCI image and confirm fresh samples
for every required phase, /ready, Telegram PHOTO identity, and that
no latest history was lost. Avoid restoring older backups over
newer phase measurements.

Issues #340 (real boot/restart ownership) and #422 (signed rollback)
remain separate production gates. This PR is source-only and cannot
authorize a new image deployment.

## QA

~~~bash
python3 -W error::ResourceWarning -m unittest discover -s tests   -p test_meter_cutover_runtime_guard.py -v
scripts/quality.sh
~~~
