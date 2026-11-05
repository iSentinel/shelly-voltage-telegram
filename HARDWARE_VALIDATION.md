# Hardware Validation — Shelly Pro EM-50

Date: 2026-11-02
Checkpoint: `0.5.6-hw`
Timezone: `Europe/Kyiv`

## 2026-11-05 natural 15-minute recovery checkpoint

**PASS with the deployed `0.5.12-hw` recovery verifier:** A read-only
scheduled check of the real 22:11:29 Europe/Kyiv recovery completed at
22:28:14. It observed 76 additional samples, unchanged outage ID 10,
a cleared recovery timer/context, a changed fingerprint of the Telegram
status-message ID, and no change to the audible-notification timestamp.
SQLite integrity passed, with no open outages or synthetic 0 V rows.

Private accepted evidence on LXC 110:

```text
/opt/shelly-voltage/validation/natural-baselines/stable-1793909489360.json
/opt/shelly-voltage/validation/natural-baselines/stable-verify-20261105-2229.json
```

The recorded report belongs to the checker version that actually ran.
It **does not retroactively pass** the later stricter continuity check
merged in PR #91. In the new `main` candidate, `stable-verify`
also requires the original `normal` event in the retained transition
history, with no subsequent voltage transition during the interval.
Missing, invalid or expired evidence returns FAIL rather than an
unfounded PASS.

For an upcoming *natural* recovery, capture its baseline during the
active 15-minute stability timer, then inspect it after the interval:

```bash
python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  stable-baseline --output /root/stable-baseline-new.json

python3 scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  stable-verify --baseline /root/stable-baseline-new.json \
  --output /root/stable-verify-new.json
```

Use **unique filenames**: output creation is exclusive, with no
overwrites. Capture from an event actually in progress, not from
an earlier outage or after the timer has disappeared. Verify before
the retained one-hour transition-history evidence expires. Do not
simulate outages, change voltage, or call Shelly control RPCs.

This proves state changes recorded by the monitor, **not** independent
delivery or visibility of any Telegram channel message. Visual
administrator confirmation remains a separate release gate. The
`0.5.13-hw` candidate still requires its own acceptance before
production installation.

## 2026-11-05 natural low-voltage event — partial acceptance

The production `0.5.12-hw` monitor recorded a real warning-level
voltage drop after natural power restoration, without simulated
electrical failures:

- Configured low warning: **207 V**; critical low: **190 V**.
- Measured minimum: **200.0 V**; 18 samples below the warning threshold.
- Persisted phase L1 `low` transition: **19:31:47 Europe/Kyiv**.
- Persisted `normal` transition: **19:34:49 Europe/Kyiv**.
- Persisted low state: **182.4 seconds**.
- Last audible-notification timestamp matched the `low` transition.
- Production remained healthy/ready; SQLite integrity passed,
  no outage remained open, and no synthetic 0 V samples were stored.

Evidence is kept privately on LXC 110 as
`validation/2026-11-05-natural-voltage-transition.json`.
The report stores threshold/timing evidence, not Telegram identifiers
or device credentials.

**Acceptance boundary:** warning-level detection and recovery on
`0.5.12-hw` are physically observed. This does **not** establish
subscriber-visible Telegram delivery, reuse of the same message for
warning/recovery, the full 15-minute stable-status rotation, critical
voltage behavior, or production approval for the `0.5.13-hw` candidate.
The last full electrical-transition checkpoint remains `0.5.6-hw`
until the other mandatory checks pass.

### Repeat the read-only transition check

Run the reporting script against the existing SQLite database:

```bash
python3 scripts/soak-report.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  --hours 3 --json
```

The JSON fields `voltage_transition_history_status`,
`voltage_transition_count`, and `voltage_transition_events` show
retained `low` / `normal` / `high` changes with their phase and
local time. A status of `absent` means no saved transition history;
`invalid` means the saved data could not be parsed. Old events outside
the chosen time window are excluded. Clock changes are sorted in
actual chronological order.

This utility reads SQLite without writing to it. The retained list
is **not** a complete alert archive. It does not prove that Telegram
subscribers saw or heard any particular status update. Continue to
use the independent channel-delivery checklist for that acceptance.

## Device identity

Validated against a physical Shelly Pro EM-50:

- model: `SPEM-002CEBEU50`
- generation: 2
- stable firmware: `2.1.1`
- authentication during validation: disabled
- measurement channels: `EM1:0`, `EM1:1`
- configured CT type: `50A` on both channels
- MQTT: disabled
- Modbus: enabled
- relay/output: not used by this project

The private LAN address used for validation is intentionally kept only in the deployment `.env` and is not part of the public example configuration.

## RPC validation

Primary RPC:

```text
GET /rpc/EM1.GetStatus?id=0
```

Required fields confirmed on hardware:

- `voltage`
- `freq`
- `current`
- `act_power`
- `aprt_power`

Both `EM1:0` and `EM1:1` returned valid data.

### Short RPC soak

A pre-container hardware loop completed:

- requests: 10
- successful: 10
- errors: 0
- voltage observed: 222.3–223.1 V
- frequency: 50.0 Hz
- channel-to-channel voltage difference: 0.0 V in the sampled loop
- average RPC latency from the Proxmox environment: approximately 113 ms

After a real mains interruption and device restart, RPC returned normally again. A later sample after power restoration showed both channels at approximately 213.6 V / 50.0 Hz with RPC latency around 34–59 ms.

Shelly timezone was changed from `Asia/Shanghai` to `Europe/Kyiv` through `Sys.SetConfig`; it remained correct after the real device power cycle.

## Container runtime validation

Dedicated deployment target:

- Debian 13 unprivileged LXC
- 1 vCPU
- 1 GiB RAM
- 4 GiB disk
- Docker 26.1.5
- Compose 2.26.1
- container image: Python 3.13 slim

Validated controls:

- Compose syntax: PASS
- image build: PASS
- container start: PASS
- non-root UID 10001: PASS
- read-only root filesystem: PASS
- `cap_drop: ALL`: PASS
- `no-new-privileges`: PASS
- PID limit: 128
- restart policy: `unless-stopped`
- `/health`: PASS
- `/ready`: PASS
- `/metrics`: PASS
- SQLite `PRAGMA quick_check`: `ok`
- schema version: 2
- Telegram-disabled mode: PASS
- secret-file mounts: PASS

## Persistence/recovery validation

Container restart test:

- database existed before restart;
- container restarted successfully;
- `/ready` returned true after recovery;
- sample count increased after restart;
- SQLite data persisted.

Full LXC reboot test:

- Docker service recovered automatically;
- `shelly-voltage-bot` recovered automatically;
- `/ready` returned true;
- sample count increased from 29 to 33 during the validation sequence;
- result: PASS.

The Compose stack is also managed by `shelly-voltage-compose.service` and the LXC is configured for host autostart.

## Initial live sampling evidence

The first generated soak report contained:

- samples: 69
- voltage min: 211.8 V
- voltage max: 217.6 V
- voltage average: 215.071 V
- frequency min/max/average: 50.0 Hz
- maximum inter-sample gap: 13.818 s
- gaps over 30 s: 0
- SQLite quick check: `ok`

This is initial evidence only, not the final 24-hour soak result.

## Backup/supportability validation

- SQLite online backup: PASS
- compressed backup SHA256 verification: PASS
- daily systemd backup timer: enabled/active
- redacted support bundle: PASS
- support bundle SHA256 verification: PASS
- token/password redaction check: PASS

## Current acceptance status

The monitor-only hardware path is accepted, including the 24-hour soak, schema-v2
migration, real outage handling, backup during outage and restart persistence.
Remaining external-input validation is limited to:

1. Live Telegram token/chat validation.
2. `sendMessage` -> persisted `message_id` -> `editMessageText` validation.
3. Telegram status-message recreation after manual deletion.
4. Optional live-device test with Shelly HTTP authentication enabled; the software Digest-auth path is regression-tested.

No beta Shelly firmware was installed during validation.

## 2026-11-01 `0.4.6-hw` clean-install acceptance

`0.4.6-hw` fixes a publication blocker discovered only on a clean Debian 13
host: installer dry-run exited with status 1 when no previous target existed. The
root cause was a bare shell `return` inheriting a failed no-op test under `set -e`.
The same pattern was fixed for in-place snapshot skipping and rollback with no
current database. Runtime monitoring code (`app.py`) is byte-identical to 0.4.5.

Acceptance evidence:

- Python regression suite: 51/51 PASS with `ResourceWarning` as error;
- fresh-target installer dry-run: PASS;
- in-place installer dry-run: PASS;
- rollback with missing current DB dry-run: PASS, no empty DB created;
- clean `debian:13-slim` archive/manifests/privacy/shell syntax: PASS;
- clean Debian first-install dry-run without Docker/target: PASS;
- bootstrap placeholder remains fail-closed with exit 2;
- candidate/live 0.4.5 `app.py` SHA256 identity: PASS;
- isolated physical-Shelly smoke: PASS, `/ready` HTTP 200, three valid samples;
- synthetic `0 V` rows during smoke: 0;
- safe production promotion preserved `.env` and SQLite history;
- snapshot `20261101-162903-0.4.5-hw` source/database SHA256: PASS;
- rollback dry-run: PASS;
- production restart continuity: samples increased, two outages retained,
  `/ready` HTTP 200, and live installer dry-run fixtures still PASS.

## 2026-11-01 `0.4.5-hw` restart-observability acceptance

A production restart on `0.4.4-hw` exposed an observability-only gap: SQLite
retained two closed real outages, while `/health.last_outage_duration` returned
null after process restart. `0.4.5-hw` restores the newest closed outage duration
from SQLite during startup without changing schema, sampling or hysteresis.

Acceptance evidence:

- unit/regression suite: 51/51 PASS with `ResourceWarning` promoted to error;
- packaging/release/install/rollback/support-bundle fixtures: PASS;
- isolated runtime on an online copy of the real production DB: PASS;
- expected/restored latest outage duration: 23899.627554 / 23899.62755393982 s;
- isolated candidate restart preserved the same duration;
- safe production promotion preserved `.env` byte-for-byte and all history;
- post-promotion SQLite `PRAGMA quick_check`: `ok`;
- synthetic `0 V` rows: 0; outage count: 2; open outages: 0;
- source/running/candidate `app.py` identity: PASS;
- snapshot `20261101-154950-0.4.4-hw` source/database SHA256: PASS;
- rollback dry-run: PASS;
- production restart: samples continued, `/ready` HTTP 200, outage count unchanged,
  and `last_outage_duration` remained 23899.62755393982 s.

## 2026-11-01 `0.4.4-hw` promotion acceptance

`0.4.4-hw` was validated first in an isolated hardened container against the
physical Shelly, then promoted with the normal installer. The release preserves
the `0.4.3-hw` monitoring behavior while making outage-duration semantics
explicit and removing the duplicated hard-coded runtime version string.

Acceptance evidence:

- exact candidate runtime reported `0.4.4-hw`;
- isolated real-device smoke: PASS, `/ready` HTTP 200, three valid samples;
- synthetic `0 V` rows during smoke: 0;
- pre-promotion live history: 6450 measurements, two closed real outages;
- post-promotion history continuity: PASS;
- existing `.env` preserved byte-for-byte;
- SQLite `PRAGMA quick_check`: `ok`;
- outage count stayed at two with no duplicate/open event;
- deployed source, running image and validated candidate `app.py` SHA256 match;
- snapshot `20261101-153804-0.4.3-hw`: source/database SHA256 PASS;
- rollback dry-run: PASS;
- restart continuity: samples increased, `/ready` HTTP 200, outage count unchanged.

The two real outages observed by this point also confirm the intended semantics:
outage journal boundaries are hysteresis-confirmed connectivity transitions, not
exact electrical mains-loss boundaries. Each recovery persisted two valid samples
before the journal closed on the second successful poll.

## 2026-11-01 `0.4.3-hw` promotion acceptance

After mains power returned, the existing real outage closed automatically and
sampling resumed without synthetic `0 V` rows. The `0.4.3-hw-rc2` image then
passed an isolated real-device smoke test before production promotion.

Production promotion acceptance:

- live version: `0.4.3-hw`;
- physical Shelly measurement/readiness: PASS;
- existing deployment `.env`: preserved byte-for-byte;
- pre-upgrade valid measurement history: preserved;
- SQLite `PRAGMA quick_check`: `ok`;
- synthetic `0 V` rows: 0;
- real outage journal: exactly one closed event, with no duplicate on restart;
- post-promotion sample continuity: PASS;
- production `db-check`: PASS in read-only mode;
- pre-upgrade source/database snapshot SHA256: PASS;
- rollback dry-run: PASS;
- container restart after promotion: healthy, `/ready` HTTP 200, fresh samples
  resumed, total outage count unchanged.

The earlier Gateway restart job lost its regional executor after the remote
restart had completed. A separate live audit verified the resulting container,
readiness and database continuity, so the executor-loss event is infrastructure
evidence rather than an application failure.
## 2026-10-31 recovery and partial soak checkpoint

After mains power returned, Shelly Pro EM-50 resumed local RPC normally on its configured private LAN address. `probe` and `doctor` passed against hardware; SQLite `quick_check` remained `ok`; Telegram remained intentionally disabled because credentials are not configured.

Runtime recovery validation after Compose reconciliation:

- version: `0.4.1-hw`;
- container health: `healthy`;
- `/ready`: true;
- restart policy: `unless-stopped`;
- read-only root filesystem: enabled;
- PID limit: 128;
- memory limit: 256 MiB; memory+swap limit: 512 MiB;
- source `app.py` SHA256 matched the previously validated Docker image;
- backup timer remained enabled/active and online SQLite backup passed.

Partial soak evidence at roughly 4.0 h elapsed:

- regular interval median: 10.040 s;
- p95: 10.064 s; p99: 10.110 s;
- no regular interval exceeded 15 s;
- one long data gap exists from 21:44:18 to 00:42:03 local time and is recorded only as a data gap, not as an outage event;
- the final sample before that gap was a single transitional reading of 106.5 V / 48.5 Hz, preceded by normal 214-217 V / 50.0 Hz samples;
- first sample after the gap was 233.4 V / 50.0 Hz.

This was a partial checkpoint; the final 24 h acceptance was completed on 2026-11-01 and is recorded below.
## 2026-10-31 live outage and `0.4.2-hw` candidate checkpoint (historical)

A real mains outage made the physical Shelly unavailable while live
`0.4.1-hw` remained running in CT110. Evidence collected without restarting the
live service:

- 2382 stored valid samples;
- sample span: 13.54 h;
- voltage min/max/avg: 106.5 / 234.7 / 218.911 V;
- zero-voltage samples: 0;
- SQLite `PRAGMA quick_check`: `ok`;
- last valid sample: 214.0 V / 50.0 Hz;
- `/health`: HTTP 200;
- `/ready`: HTTP 503 because data is stale;
- Docker marks live `0.4.1-hw` unhealthy because its healthcheck targets
  `/ready`. The process itself is alive.

Candidate `0.4.2-hw` was developed separately under
`/opt/shelly-voltage-candidates/0.4.2-hw`; the live soak was not interrupted.
Candidate validation completed:

- Docker healthcheck changed to `/health`;
- connectivity hysteresis: 3 failures offline / 2 successes online;
- last-good restoration after restart;
- persistent outage journal;
- schema v1 -> v2 migration with history preservation;
- restart during open outage without duplicate event;
- simulated recovery closes the same event and records duration;
- Docker stays healthy while `/ready` correctly reports offline;
- Telegram offset persistence, ACL, edit throttle, 429 retry and
  `message is not modified` handling;
- optional one-shot outage/recovery alerts;
- 33 unit tests pass; compile/style/Compose/ResourceWarning gates pass.

The offline/restart/recovery runtime simulation used an isolated Docker network
and a fake Shelly response at 230.5 V / 50.0 Hz. It did not touch the live
SQLite database or live container. Real-device recovery for the current outage
remains pending until mains power returns.
### Release/upgrade safety validation

Candidate `0.4.2-hw` also passed upgrade-safety checks without modifying the live
container:

- installer dry-run: PASS;
- installer now gates deployment on `/health`, not Shelly `/ready`;
- online backup of the live 2382-sample SQLite DB: SHA256 PASS, quick_check `ok`;
- pre-upgrade source + SQLite snapshot generation: implemented;
- rollback fixture: source v2 -> v1 restore PASS;
- `.env` and secrets preserved by rollback;
- rescue backup of pre-rollback schema-v2 database verified;
- full CI-equivalent gate: PASS;
- release-candidate Docker image tag: `shelly-voltage:0.4.2-hw-rc`.
### Schema-v2 soak/support and release validation

- schema-v1 live database read by the candidate soak tool without migration: PASS;
- live sample span reported as 13.543 h with stale age reported separately: PASS;
- schema-v2 confirmed outage overlap fixture: PASS;
- sample gaps remain diagnostic and are not reclassified as outages: PASS;
- support bundle schema-v2 outage summary + secret redaction fixture: PASS;
- reproducible source release, byte-identical for fixed epoch: PASS;
- release private/generated-file exclusion and SHA256 manifests: PASS.
- offline `doctor` regression: PASS;
- Telegram transport-error token-redaction regression: PASS;
- final Python test count after these checks: 33.

## 2026-11-01 final `0.4.2-hw` hardware acceptance

The live deployment was switched to the exact tested `0.4.2-hw` RC2 image and
accepted on CT110. Schema-v1 history migrated to schema v2 without losing the
pre-switch measurement rows. Container restart persistence passed after the live
switch.

Final soak evidence:

- sample span: 26.947 h; wall elapsed at acceptance: 28.678 h;
- valid measurements: 4538;
- normal sampling cadence median/p95: 10.040 / 10.069 s;
- SQLite `PRAGMA quick_check`: `ok`;
- synthetic 0 V rows: 0;
- three >30 s data gaps are reported diagnostically and are not reclassified as outages.

A real mains blackout beginning at 2026-10-31 23:55:03+02:00 provided production
outage evidence. The monitor kept `/health` available, changed `/ready` to HTTP
503, retained 227.7 V / 50.0 Hz as the last-good sample, and opened exactly one
schema-v2 outage event. A SQLite backup taken during the outage contained all
4538 rows and passed SHA256 plus `quick_check`. Restarting the production
container while the outage remained open restored the same last-good sample and
the same single outage event without creating a duplicate.

Release/publication hardening was then revalidated: 45 tests, reproducible source
archive, fail-closed pinned installer, rollback fixture, support-bundle redaction,
and operational-file exclusion all pass.

## 2026-11-01 `0.4.8-hw` Telegram live acceptance

The production monitor was promoted from `0.4.7-hw` after the 55-test software
gate, reproducible release, installer, rollback and support-bundle fixtures passed.
The real Shelly probe passed before promotion, and SQLite continuity remained intact.

Telegram acceptance passed against the configured private destination:

- `doctor` validates bot identity and the destination chat;
- the stable status message is persisted and edits in place;
- deleting that status message causes one replacement message to be created and remembered;
- a live `/voltage` command-handler response was delivered;
- the complete command set is registered;
- persisted update-offset state remains present;
- sustained command-loop polling shows no `ReadTimeout` or Telegram transport errors;
- the `getUpdates` HTTP timeout retains 5 s headroom over the 20 s Bot API long poll.

No Telegram credential or private chat identifier is recorded in this public evidence.


## 2026-11-02 `0.5.4-hw` audit and promotion acceptance

The maintainability candidate was first validated in isolation and then promoted
to production after the full software, package and hardware gates passed.

- physical source: Shelly Pro EM-50;
- candidate doctor: Shelly status `ok`;
- observed voltage: approximately 216–217 V during the smoke;
- observed frequency: 50.0 Hz;
- SQLite `quick_check`: `ok`;
- schema version: 3;
- isolated `/ready`: HTTP 200 / online;
- three additional valid L1 samples were written to a copied database;
- synthetic `0 V` rows: 0;
- Telegram was disabled for the isolated runtime;
- production configuration and database were not modified by the smoke test.

## 2026-11-02 `0.5.4-hw` live hardware acceptance

Production was promoted from `0.5.3-hw` to the exact tested `0.5.4-hw`
release after pull-request and post-merge CI completed successfully.

Live acceptance on a physical Shelly Pro EM-50 confirmed:

- monitor connectivity remained online at approximately 219 V / 50 Hz;
- `/ready`, `/health`, `/metrics`, `/status.json` and `/dashboard` passed;
- `doctor` reported Shelly, SQLite and Telegram status as healthy;
- SQLite schema remained v3 and `quick_check` returned `ok`;
- no synthetic `0 V` measurements were present;
- measurement sampling continued after the container restart;
- Telegram stable-status and pin continuity were preserved;
- the hardened container remained non-root, read-only, capability-free and
  protected with `no-new-privileges`;
- a verified rollback snapshot was created before the upgrade;
- the release tag, source archive/checksums and versioned multi-architecture
  container image were produced from the exact merged release commit;
- Shelly access remains read-only and no load-control RPC was introduced.


Production acceptance after promotion:

- production version and `LIVE_VERSION`: `0.5.4-hw`;
- container health: healthy;
- `/ready`: HTTP 200 / online;
- physical Shelly Pro EM-50: online at approximately 222 V / 50.0 Hz during the post-promotion check;
- SQLite schema metadata: 3;
- SQLite `PRAGMA quick_check`: `ok`;
- open outage records: 0;
- synthetic `0 V` rows: 0;
- persisted Telegram status message retained across promotion;
- recent runtime logs contained no application error or warning entries;
- deployed `app.py` SHA256 matches the audited `0.5.4-hw` candidate.

## 2026-11-02 `0.5.6-hw` live hardware acceptance

Production recovery and promotion were validated on the physical Shelly Pro EM-50.

- outage #4 closed and the captured recovery baseline verified PASS;
- promotion gate PASS with the active backend restricted to `pro_em50`;
- rollback snapshot `20261102-212042-0.5.4-hw` source/database checksums PASS;
- production runtime version: `0.5.6-hw`;
- Shelly, SQLite and Telegram doctor checks: PASS;
- `/ready`: HTTP 200 / online;
- SQLite `PRAGMA quick_check`: `ok`;
- four outage rows preserved, zero open outages after recovery;
- synthetic `0 V` rows: 0;
- Telegram shared-status message ID remained stable across promotion and restart;
- measurement history continued increasing after promotion and after restart;
- runtime voltage-alert sound cooldown defaults to 5 minutes and supports
  Off/3/5/10 through administrator `/settings`;
- low/high voltage transitions use the shared Telegram status replacement path,
  publishing the replacement before deleting the previous status;
- production container remains non-root, read-only, capability-free and
  protected with `no-new-privileges`;
- `LIVE_VERSION` is now `0.5.6-hw`; public `RELEASE_VERSION` remains `0.5.5-hw`
  until the release is published.

Physical Pro 3EM / 3CT63 production enablement remains blocked until the actual
three-phase hardware is installed and passes L1/L2/L3 physical acceptance.
## Natural 15-minute stable-recovery acceptance (`0.5.10-hw`)

This acceptance is intentionally read-only. Do not create a power cut or force a
voltage threshold transition. Wait for a natural voltage recovery while the bot
is running. The checker opens SQLite with `mode=ro`, does not call Telegram, and
does not send any Shelly control/configuration RPC.

When `voltage_recovery_stable_since` is active after the natural recovery, save
a safe baseline:

```bash
python scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  stable-baseline \
  --output validation/stable-recovery-baseline.json
```

The baseline stores a one-way fingerprint of the Telegram status message, not
the raw message ID. After at least 15 unchanged minutes, verify the silent
replacement:

```bash
python scripts/recovery-acceptance.py \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  stable-verify \
  --baseline validation/stable-recovery-baseline.json \
  --output validation/stable-recovery-result.json
```

PASS requires the recovery status message to be replaced, the recovery timer and
transient event/episode context to be cleared, new valid samples to be present,
SQLite `quick_check` to remain `ok`, zero synthetic `0 V` rows, `/health` and
`/ready` to remain online, and the audible voltage-notification timestamp to be
unchanged. A result before the full 15-minute interval fails closed.
## 2026-11-03 `0.5.10-hw` migration and restart acceptance

This is operational migration evidence, not a substitute for the still-pending
natural voltage threshold/recovery acceptance. No electrical transition was
induced.

- exact merged source: `4ab02a16d5cbe51cadbaf89fd220fcb82165f69b`;
- pre-upgrade database had a persisted `recovered` event and episode but no
  `voltage_recovery_stable_since` key;
- startup migration restored the missing timer from the stored recovery time;
- after a confirmed normal poll, the old recovery status was replaced silently;
- transient recovery event, episode, edit-window and stability timer were cleared;
- audible notification cooldown timestamp remained unchanged;
- Shelly remained online and `/ready` returned HTTP 200;
- SQLite `quick_check`: `ok`; open outages: 0; synthetic `0 V`: 0;
- deployed `VERSION`, `app.py` and recovery-acceptance script matched exact
  merged source;
- restart continuity preserved the replacement status and did not recreate
  recovery context;
- pre-upgrade source and compressed SQLite snapshot checksums passed, and the
  restored snapshot database passed `quick_check` with zero synthetic `0 V`.
