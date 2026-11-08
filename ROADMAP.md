## 2026-11-08 QA: read-only EM-50 release preflight gate

- New scripts/em50-release-preflight.py checks candidate marker,
  localhost /health and /ready, live SQLite quick_check and
  saved Telegram status ID existence, newest backup SHA256
  and age, and protected YASNO reader group/freshness.
- Script prohibits off-host HTTP and redirects, hides private
  Telegram message identifiers, and makes no changes to
  production SQLite, Shelly, Telegram or Docker.
- 0.5.14-hw remains UNPUBLISHED. Exit code 1 signifies a
  failed automated check. Exit 2 means automated checks
  PASS but real Telegram recipient acceptance, actual LXC
  systemd activation review and explicit signed release/
  rollback authorization are still PENDING. The program
  NEVER returns approved-release status automatically.
- Gateway live run: 5/5 automated checks PASS and
  release_authorized=false; expected exit 2.
  Local full QA 1120/1120 Python tests PASS,
  Ruff/quality and source-release reproducibility/private
  .env exclusion PASS.
- Next: CI, reviewed private Telegram recipient acceptance,
  operator-approved rollout. Production 0.5.13-hw unchanged.

## 2026-11-08 QA: numeric Linux UID YASNO handoff acceptance

- On Gateway LXC110 tested protected YASNO producer cache with
  numeric UID10002 (0700/0600), bounded exporter CAP_CHOWN+
  CAP_DAC_OVERRIDE, and UID10001 read-only container. PASS.
- New test_yasno_role_isolation.py reproduces the same real
  Linux identity boundary in an isolated temporary path using
  synthetic current-day source data; no Telegram sends, no
  provider HTTP, no production files touched. It skips
  privileged path on unprivileged CI workers.
- Full 1111/1111 tests PASS, Ruff/quality gate PASS, source
  archive reproducibility/private .env exclusion PASS.
- No production systemd units, bind mounts or release promotion.
  Native Telegram recipient/client visibility and explicit
  production rollout approval remain mandatory.

## 2026-11-08 QA: inactive systemd YASNO refresh-to-reader handoff

- Added a separate UNINSTALLED handoff.service.example,
  triggered only on successful refresh.service via OnSuccess.
  Source producer user is shelly-yasno; recipient exporter
  is root with only CAP_CHOWN + CAP_DAC_OVERRIDE to read
  private source and change recipient group 10001.
- Handed-off cache is read-only to nonroot bot 10001, source
  stays private; no Bot API or Shelly controls from exporter.
- All files remain .example and have NOT been installed,
  started, enabled or promoted to production.
- Full local 1110/1110 tests PASS, Ruff/quality PASS,
  static systemd-analyze verify PASS and reproducible source
  archive/private .env exclusion PASS.
- PR #217 verified handoff CI SUCCESS; this stage needs
  independent CI and review, real LXC systemd sandbox
  acceptance and private Telegram recipient test.
- Production still 0.5.13-hw with no Docker restart or
  channel message change. Candidate 0.5.14-hw unpublished.

## 2026-11-08 QA: verified YASNO handoff to read-only EM-50 bot

- On isolated 0.5.14-hw source candidate, added a manual,
  opt-in yasno_handoff.py: no provider HTTP, Telegram send,
  Shelly RPC, SQLite write, production volume change or restart.
- The producer original remains 0700/0600. The exporter
  validates pinned provider metadata, SHA256 integrity, Kyiv
  day, valid ScheduleApplies 48-slot plan and age. It preserves
  source capture time and refuses older data over newer output.
- Recipient directory owner:bot-group mode 0750 and file
  owner:bot-group mode 0640; symlink/hardlink/unsafe paths and
  parallel writers are rejected. Atomic fsync and replace,
  idempotent identical-copy SKIP.
- Real QA read-only check in isolated container (UID 10001,
  --network none, --read-only, read-only mount): READ PASS
  and write rights denied. No production Docker changes.
- New default-OFF Compose override example, CI merged-config
  syntax check and 14 targeted safety tests. Complete local
  1106/1106 Python tests PASS, Ruff/quality and private-env/
  reproducible source release PASS.
- Next: independent CI, operator-approved private Telegram
  recipient-visible testing, rollout mount/service review,
  stable release approval and rollback. Draft only.

## 2026-11-08 QA: unpublished candidate 0.5.14-hw for EM-50

- VERSION=0.5.14-hw in source-only QA candidate; no stable
  GitHub release or production deployment. RELEASE_VERSION
  stays 0.5.12-hw and historical LIVE_VERSION stays
  0.5.6-hw. Actual production /health currently 0.5.13-hw.
- This source includes opt-in standard Bot API text/YASNO
  status, protected cache and refresh, full image module
  packaging and networkless Docker import smoke.
- README in EN/RU/UA, CHANGELOG and release status reflect
  the UNPUBLISHED candidate without changing published
  release history or physical hardware checkpoint.
- Full local 1092/1092 Python tests PASS, Ruff/quality PASS,
  reproducible archive and private .env exclusion PASS.
  QA-only 602KB source tarball validated via SHA256SUMS.
  Manual QA image policy PASS; stable tag policy BLOCKED.
- GitHub PR #214 and #215 CI SUCCESS. Next: independent
  GitHub CI for candidate, recipient-visible Telegram,
  protected cache mount/UID, rollback and approval.
- No Telegram sends, Shelly writes, SQLite modifications,
  production restarts, or new systemd jobs.

## 2026-11-08 QA: Docker and release module completeness

- Identified that the old Dockerfile omitted new app dependencies
  despite successful source-only Python tests.
- Dockerfile now copies all root Python modules and preserves
  the unprivileged 10001:10001 runtime user. The GitHub CI
  imports app, yasno_cache and telegram_channel_yasno inside
  the actual built container with Docker --network none.
- Reproducible release.sh required-file manifest and privacy
  fixture now include the full imported local runtime set.
- AST-based tests protect against future module omissions.
  Local full 1088/1088 tests PASS, Ruff/quality PASS,
  release reproducibility + private .env exclusion PASS,
  release.sh dry-run PASS.
- No image published, installed or deployed; production
  0.5.13-hw remains healthy. Next: GitHub Docker smoke,
  versioned EM-50 candidate, real-client Telegram acceptance,
  protected cache mount and controlled rollout approval.

## 2026-11-08 QA: EM-50 standard Bot API YASNO status card

- Dedicated QA worktree implements optional (default OFF)
  TELEGRAM_YASNO_STATUS_ENABLED=1 text enhancement.
  It preserves the existing pinned message ID, sendMessage/
  editMessageText, buttons, event stats and recovery logic.
- Only YASNO_TRUSTED_CACHE_FILE with verified private
  provenance metadata can add a short RU/UK/EN forecast.
  The formatter does not call the provider or Telegram API.
  Possible/definite schedules remain separate from measured
  Shelly power; invalid/stale cache shows an unavailable line.
- Current Telegram text remains byte-for-byte unchanged
  without the explicit feature flag. A conservative UTF-16
  length guard refuses additions over 4096 code units.
- 12 dedicated tests and 1084/1084 full Python tests PASS,
  Ruff + quality gate PASS. No live Telegram send or rollout.
- EM-50-only production release remains gated by recipient
  client acceptance, protected cache read-only mount/UID,
  versioned image/rollback review and explicit approval.
  Pro3EM/3CT63 physical testing is a separate follow-up.

## 2026-11-08 QA: guarded YASNO cache refresh and systemd examples

- Gateway 03:18 Kyiv: live Shelly Pro EM-50 0.5.13-hw remains
  online at 212.9 V / 50.0 Hz, 1313 successful polls, /ready
  HTTP 200. SQLite quick_check=ok, 25832 measurements.
- Protected YASNO cache PR #212 passed push and PR GitHub CI.
- QA-only scripts/yasno-refresh.py supports explicit --status,
  --dry-run and --once. The first two never access the network
  or write a lock. --once holds an owner-only nonblocking
  exclusive lock and makes at most one HTTPS fetch when due,
  with >=900s default interval (allowed 300..7200s).
  Failed fetches preserve the prior sealed cache.
- Actual protected QA cache STATUS PASS, DRY_RUN PASS,
  guarded --once SKIPPED when fetch age was 603 seconds.
  The already-running production service was not touched.
- Added NOT-INSTALLED .example systemd service and timer
  files with dedicated service account, protected state
  directory, 15-minute schedule, jitter, and reviewed
  installation/rollback documentation. No timer enabled.
- New tests: 13, full 1072/1072 Python tests PASS,
  Ruff 0.14.2 + maintainability gate PASS.
- Outstanding: controlled source cache sharing with the
  production bot, standard Bot API channel fallback,
  recipient-visible acceptance, approved rollback/release.
  Physical Pro 3EM/3CT63 testing does not block EM-50-only.
- No Telegram sends, Shelly controls, SQLite production
  writes, Docker restart, or production deployment.

## 2026-11-08 QA: secure parent-directory guard for YASNO cache

- Follow-up hardening of Draft PR #212: the trusted local cache
  reader/writer now requires a protected parent directory,
  rejects group/world-writable parents and symlinked parent
  path segments, in addition to the 0600 file itself.
- Added explicit negative tests for world-writable parent and
  symlinked directory. Full integrated QA 1059/1059 PASS;
  Ruff and maintainability gate PASS.
- Direct public HTTPS fetch to protected QA location
  /tmp/shelly-yasno-qa-private/yasno.json succeeded;
  parent mode 0700, file 0600, 27079 bytes, group 10.1
  day 2026-11-08 has all 48 slots and definite windows
  04:00–11:30 / 14:30–22:00.
- A previous test-only file in shared /tmp was validated as
  this QA fixture and removed *after* a valid protected
  replacement existed. No application or backup files deleted.
- Still no scheduled refresh service nor promotion; production
  0.5.13-hw /ready 200, Telegram and Shelly untouched.
- NEXT: GitHub CI for the hardened cache stage, then a
  restricted/observed refresh policy and actual client QA.

## 2026-11-08 QA: provenance-labelled atomic YASNO cache

- Built yasno_cache.py for explicit direct public HTTPS retrieval
  from pinned YASNO Kyiv region 25 / DTEK DSO 902 endpoint.
  It validates source data and schedule before writing a private
  cache envelope with pinned URL, region/operator IDs, retrieved_at,
  payload and SHA-256 corruption checksum. It is not a digital
  signature and cannot defend against a compromised writer.
- Cache writes are restricted to an existing directory, no symlink,
  mode 0600, atomic rename + fsync file/directory. Invalid
  or unpublished fetched schedule never replaces the prior cache.
  Strict reads enforce file type/permissions, schema, provider IDs
  and checksum. Schedule still needs fresh actual capture <=2h,
  publication <=36h and today's Europe/Kyiv slots.
- Private QA /widget_preview now prefers explicitly configured
  YASNO_TRUSTED_CACHE_FILE over legacy YASNO_CACHE_FILE and
  refuses any silent downgrade to raw YASNO on failure.
  Separate community fallback remains labelled independently.
  No network request is made by the Telegram command handler.
- New local tests: 12 more; full integrated QA 1057/1057 PASS,
  Ruff and maintainability quality gate PASS. Real read-only
  YASNO API to /tmp/shelly-yasno-trusted-20261108.json succeeded;
  27079-byte cache 0600, group10.1 48 slots, windows
  04:00–11:30 and 14:30–22:00. Read-only preview from
  Shelly health: 219.7 V / 50.0 Hz and separately labelled
  YASNO graph. /ready remained HTTP 200.
- Production 0.5.13-hw not redeployed, no Telegram sends,
  no Shelly configuration changes, no production SQLite writes.
  No automatic trusted cache refresh or cron configured.
- NEXT: GitHub CI review of this cache stage; restricted
  scheduled cache refresh, monitoring/staleness diagnostics,
  real Telegram client acceptance, physical Pro3EM/3CT63,
  DTEK attribution review and explicit release approval.

## 2026-11-08 QA: YASNO source in private Telegram widget preview

- Direct YASNO provider source Draft PR #210 independently passed
  Python 3.11/3.12/3.13 GitHub push + PR CI. Public YASNO
  region 25 / DSO 902 maps to Kyiv / DTEK Kyiv networks.
- In a new dependent QA worktree, added a local-only cache
  preference for /widget_preview. Opt-in YASNO_CACHE_FILE and
  YASNO_GROUP select a strict parser of the pinned YASNO response.
  No network calls occur in a Telegram request handler.
- If YASNO cache is missing, invalid, unpublished or too old,
  the separate community cache may be used (labelled as such);
  otherwise only physical Shelly measurements are shown.
  Local file mtime alone cannot prove provider authenticity.
- Widget schema accepts distinct src=yasno and src=community,
  labels each correctly in RU/UK/EN, and hides expired
  schedules as before. Voltage/phase state is always sourced
  from Shelly, never forecast. HTML zlib9 3606/4096 bytes.
- Full combined QA: 1045/1045 Python tests PASS,
  Ruff and quality gate PASS. Read-only preview using actual
  Shelly health and YASNO Kyiv group 10.1 JSON displayed
  221.6 V / 49.9 Hz with forecast 04:00–11:30,
  14:30–22:00, separately labelled.
- No Telegram message was sent. The production 0.5.13-hw
  service was not deployed or restarted. SQLite untouched,
  /ready continued HTTP 200.
- NEXT: official QuickJS DOM and source labels, GitHub CI,
  then the real client/private recipient acceptance, cache
  provenance and scheduled read-only refresh architecture,
  Pro3EM/3CT63 hardware and explicit release approval.

## 2026-11-08 QA: Telegram widget public_data lifecycle checks

- New dependent isolated QA branch from responsive PR #208 tests
  a *single widget instance* under real upstream Telegram QuickJS:
  fresh three-phase EN state -> disconnected RU state with no
  forecast -> recovered UK state with L2 stale -> expired
  community forecast -> single-phase EN state. DOM probes assert
  L1/L2/L3, status language, schedule and group visibility.
- The test injects supported public_data bridge updates; it
  must not require sending another Telegram message. Explicitly
  rejects unexpected network requests or stopped widget runtimes.
- Added tests/test_widget_lifecycle_contract.py with three checks
  for pinned QuickJS CI, expected state transitions and no
  requests. Full local Python tests 1022/1022 PASS, Ruff and
  quality gate PASS. Actual QuickJS update results are pending
  the new GitHub CI; do not claim client-visible acceptance.
- Production 0.5.13-hw remains unchanged; this branch is QA only.
- NEXT: run GitHub Actions QuickJS lifecycle acceptance, fix
  any missing public-data notification or rendering defects;
  then resume direct official DTEK provenance, physical Pro
  3EM / 3CT63 acceptance and actual Telegram clients.

## 2026-11-08 QA: 3-phase Telegram widget and preview

- New dependent QA branch from PR #206 adds independently stale-safe
  Pro 3EM L2 and L3 measurements to Telegram widget public_data,
  fallback caption and admin-only /widget_preview.
- Pro 3EM always specifies both secondary phases in the payload.
  Each phase's online/offline/stale state uses a separate timestamp:
  a stale or missing L2/L3 value is "—", not 0 V or L1 voltage.
  L1 failure never masks fresh L2/L3 values.
- Non-three-phase devices such as live Pro EM-50 still render only
  L1, keeping their existing UI and messages unchanged.
- No secret/request metadata is serialized. The entire widget HTML
  is 9256 bytes raw / 3430 of 4096 bytes zlib9. Source remains
  unofficial for community forecast, separate from real measurements.
- Local full QA: 1018/1018 PASS, Ruff 0.14.2 and quality gate PASS.
  No physical Pro3EM/3CT63 hardware or Telegram recipient acceptance;
  production 0.5.13-hw has not been redeployed.
- NEXT: independent GitHub Actions CI and QuickJS smoke on this
  integrated QA branch; later physical multi-meter acceptance,
  supported Telegram client checks, direct official DTEK provenance
  and explicit release approval.

## 2026-11-08 QA: admin-only widget fallback preview and precise timestamps

- Dependent on combined QA source branch PR #205. Added an optional
  private-only /widget_preview command for an authenticated administrator;
  never responds in public channel or to an unauthorized private sender.
- /widget_preview renders only stale-safe plaintext from the widget
  public-data format; no experimental Telegram sendWidget calls.
  Actual command delivery occurs only if a real authorized administrator
  requests it after a future deployment. No sends in QA.
- Community forecast is opt-in via three environment settings
  COMMUNITY_OUTAGE_CACHE_FILE, COMMUNITY_OUTAGE_REGION and
  COMMUNITY_OUTAGE_GROUP, with a validated bounded local JSON file.
  Bad/missing/stale data are omitted, not interpreted as power events.
- Fixed fractional-second timestamp rounding (never convert a recent
  poll into a future sample). Added EN unit labels V/Hz.
- Full isolated QA suite: 1009/1009 PASS; Ruff/quality PASS;
  widget zlib9 remains 3205/4096 bytes. Production unchanged.
- NEXT: CI and private Telegram recipient acceptance after explicit
  client/Bot API support; official DTEK trust, real Pro3EM hardware
  acceptance and authorized release remain pending.

## 2026-11-08 Combined QA acceptance (not a release)

- Three independent staged changes are combined *only* in a new
  disposable QA worktree: widget/community schedule PR #202;
  DTEK numeric-day compatibility PR #203; exact Shelly identity
  read-only preflight PR #204. Source baseline b7016e8.
- Python comprehensive combined regression: 1000/1000 PASS,
  Ruff 0.14.2 PASS, project maintainability quality gate PASS.
  HTML widget 8439 bytes raw and 3205/4096 bytes zlib-9.
- This is source-level coexistence testing, not a completed merge
  of individual GitHub PRs, nor physical/recipient acceptance.
- Actual Shelly EM-50 identity was confirmed read-only against
  selected pro_em50; hardware 3EM and 3CT63 are NOT physically
  connected/tested. Official DTEK returns a challenge page.
- No Telegram API writes, no service restarts, no production
  SQLite writes. Continue independent cross-version GitHub CI,
  real-client widget checks, hardware acceptance, DTEK provenance,
  and approved promotion before release.

## 2026-11-06 15:48 Europe/Kyiv — HTTP monitoring freshness and browser hardening

- **REAL GATEWAY FIELD STATE:** installed `0.5.13-hw`
  remains Docker healthy, 0 restarts, /health HTTP 200,
  /ready HTTP 503, Shelly offline under natural outage
  #13 (opened 10:30:51). Last real sample 10:30:08
  (235.7 V / 50 Hz) is stale. SQLite quick_check OK,
  21,950 real measurements, zero artificial 0 V,
  and no new Telegram poll/status errors. Private
  baseline #13 mode 0600 unchanged. The user's
  approximate 17:00 Kyiv restoration time is not
  a verified event.
- **SOURCE REVIEW:** the local diagnostic server did not
  declare a cache policy for `/dashboard`, `/status.json`,
  `/ready` and `/metrics`. Downstream clients or
  intermediaries could retain earlier status content
  after live device recovery.
- **SOURCE-ONLY IMPROVEMENT:** send explicit
  `Cache-Control: no-store, max-age=0` and
  `Pragma: no-cache` on monitoring responses; also
  set `X-Content-Type-Options: nosniff` and
  `X-Frame-Options: DENY`. Set a restrictive
  HTML-only Content-Security-Policy permitting
  the existing inline CSS but no scripts,
  frames or external resources. No secrets,
  meters, database schema or Telegram behavior changed.
- **QA:** three deterministic regression tests verify
  cache, content-type and frame protections for
  JSON, metrics and HTML and the HTML-only policy.
  Full network-disabled, read-only Docker unit suite
  **459/459 PASS**, Ruff and max-40-line function
  quality gate PASS. Installed production is unchanged.
- **NEXT:** require GitHub CI on Python 3.11/3.12/3.13,
  then complete the natural #13 recovery baseline gate,
  15-minute stable status, real channel visibility,
  and controlled production upgrade only when safe.

## 2026-11-06 — Local Dashboard UI v1, source-only design phase

- **Goal:** make the existing local read-only HTML dashboard useful
  on phone and desktop without opening a public service, adding
  JavaScript dependencies, or controlling mains electricity.
- **SOURCE IMPLEMENTED:** responsive cards for each active
  configured phase; accessible headings and labels, explicit
  online/recovering/stale/offline states, last valid sample
  age, and frequency displayed only with a fresh voltage.
  Dynamic heading and phase labels are HTML-escaped;
  the page has no external resources and keeps its 30-second
  automatic refresh interval.
- **FRESHNESS / SAFETY:** reuse the PR #147 stale-safe
  `dashboard_rows` and add `live_frequency` from the same
  per-phase freshness check. A stale/unavailable reading
  is always displayed as unavailable (not 0 V). Shelly
  connectivity loss is not proof of electrical mains loss.
- **QA / SOURCE ONLY:** three new tests cover responsive
  layout and visible status labels, markup escaping, and
  suppression of stale frequency alongside voltage.
  Full isolated network-disabled unit suite **456/456 PASS**;
  Ruff, PEP8 and max-40-line-function gate PASS. Actual
  Pro 3EM physical acceptance and production deployment
  remain separate. The currently installed bot is still
  `0.5.13-hw`; outage #13 has not been forced or cleared.
- **NEXT:** GitHub CI Python 3.11/3.12/3.13, future local
  browser visual acceptance after a safe deployed update,
  natural Shelly outage #13 recovery comparison, Telegram
  channel visible-delivery check and 15-minute stability
  gate. Public release promotion remains blocked.

## 2026-11-06 15:17 Europe/Kyiv — local dashboard stale-measurement safety

- **GATEWAY / REAL HARDWARE:** Installed `0.5.13-hw` remained
  healthy, zero Docker restarts, HTTP /health 200 and /ready
  503 while Shelly Pro EM-50 was offline. Outage #13 has
  remained open since 10:30:51. Last real measurement at
  10:30:08 (235.7 V / 50 Hz) is **historical only**.
  SQLite quick_check OK with 21,950 measurements, no
  synthesized 0 V, and no new Telegram poll/status errors.
  Backup timer reported active/success and original private
  pre-recovery baseline #13 retained mode 0600.
- **SOURCE FINDING:** despite PRs #137/#139 correctly providing
  stale-safe `/status.json` phase values, the local HTML
  `/dashboard` independently rendered cached voltage whenever
  a phase said online/recovering, without enforcing the
  configured sample-age limit. A delayed poll could show
  an outdated number as current.
- **SOURCE-ONLY FIX:** reuse `phase_service_snapshot` and its
  `live_voltage` / `sample_is_stale` rules. Display an
  unavailable value and `stale` state for aged samples,
  with last valid sample age visible on the local page.
  Independent phases remain independent and missing
  measurements never become false 0 V.
- **QA:** three new tests cover aged-but-online readings,
  per-phase freshness and no samples. Network-disabled,
  read-only Docker unit suite **453/453 PASS**, Ruff, PEP8
  and max-40-line function quality gates PASS.
  Production is **not** upgraded during outage #13.
- **NEXT:** GitHub CI Python 3.11/3.12/3.13,
  natural Shelly recovery field gate, visible Telegram
  channel acceptance, 15-minute stable-status verification,
  and eventual controlled signed release. The owner's
  approximately 17:00 Kyiv restoration estimate is
  unconfirmed until fresh samples arrive.

## 2026-11-06 14:13 Europe/Kyiv — unpinned Telegram status cleanup fix

- **REAL FIELD / READ-ONLY:** Shelly Pro EM-50 stayed offline
  during outage #13 (opened 10:30:51 Kyiv). At 14:05
  the installed `0.5.13-hw` bot remained healthy with zero
  Docker restarts; /health HTTP 200, /ready HTTP 503;
  SQLite quick_check OK, 21,950 genuine measurements,
  zero fabricated 0 V, and one open outage. Last voltage
  235.7 V / 50 Hz at 10:30:08 is **stale**, not current
  mains telemetry. Telegram polling/status errors since
  this outage began: 0. Existing baseline #13 mode 0600
  remains preserved.
- **CODE AUDIT / DUPLICATE RISK:** the source implementation
  of `delete_previous_status()` attempted Telegram
  `unpinChatMessage` for *every* old status. Pinning is
  disabled by default; Telegram can reject an unpin
  request for a message that is not pinned. The function
  then returned before `deleteMessage`, leaving a
  duplicated old post after a successful new status send.
  This is a source-reviewed risk, not a verified channel
  duplicate from the current outage.
- **SOURCE-ONLY FIX:** consult the persisted pinned status
  ID. If the old message was not pinned by the bot, delete
  it directly. If it was pinned, explicitly unpin it
  before deletion. A benign `message to unpin not found`
  response is treated as already unpinned; other unpin
  failures still prevent deletion. Never unpin a
  different pinned message.
- **QA / PASS:** three new tests check unpinned removal,
  stale pin metadata, and unrelated pinned messages.
  Four existing tests now explicitly set a pin marker
  when checking the required unpin-before-delete order.
  Full network-disabled, read-only Docker test suite
  **450/450 PASS**, pinned Ruff 0.14.2, PEP8 and
  40-line function quality gate PASS. No live Telegram
  API use, real Shelly RPC control or production restart.
- **NEXT:** require GitHub Python 3.11/3.12/3.13 CI,
  inspect channel-visible status after natural recovery,
  and validate outage #13 using the accepted private
  baseline. The estimated 17:00 Kyiv restoration
  remains tentative. Do not promote the public release
  or claim real delivery based on synthetic tests.

## 2026-11-06 12:44 Europe/Kyiv — safe recovery status replacement candidate

- **FIELD / READ-ONLY:** Shelly Pro EM-50 remained offline under
  physical connectivity outage #13, open since 10:30:51 Kyiv.
  At 12:37 production `0.5.13-hw` was Docker healthy with
  0 restarts, /health=200, /ready=503. SQLite quick_check OK,
  21,950 real samples and no invented 0 V values; the last
  235.7 V reading at 10:30:08 is **stale**. Telegram and SQLite
  doctor checks were OK; Shelly doctor correctly reported
  unavailable. Existing private outage #13 pre-recovery
  baseline remains accepted, mode 0600.
- **REVIEW FINDING:** on confirmed connectivity recovery the
  installed implementation deletes the old status message
  *before* sending its replacement. A Telegram transport failure
  can therefore leave the channel without its previous status.
  This is a source-code defect candidate, not a witnessed
  failed delivery on the current offline production instance.
- **SOURCE-ONLY FIX:** send a new recovery status, persist
  its message ID, then unpin/delete the previous message.
  On publish failure preserve the original message and
  record a pending rotation in SQLite. Retrying works
  through regular status updates and after process restart;
  a new confirmed outage cancels the pending recovery.
  The recovery banner continues to say **Shelly connectivity**
  has recovered, not that mains power was restored.
- **QA:** four added regression tests cover failed publish,
  send-before-delete order, retry across restart and pending
  rotation cancellation by a new outage. Two older status
  tests now mock exact metadata keys. Isolated network-disabled
  Docker full suite **447/447 PASS**, Ruff, PEP8 and
  maximum 40-line functions gate PASS. This is a
  pre-deployment candidate, with **no live Telegram calls**.
- **NEXT:** require Python 3.11/3.12/3.13 GitHub CI and
  source review; keep installed production unchanged
  while Shelly offline. When it naturally reconnects,
  verify outage #13 with its own private baseline,
  check subscriber-visible status and 15-minute
  stabilization before deciding on any later deployment
  or public release.

## 2026-11-06 12:20 Europe/Kyiv — prolonged outage #13 and restart idempotence QA

- **PRODUCTION / READ-ONLY:** physical Shelly Pro EM-50 still offline
  under open outage #13 since 10:30:51. At 12:17, Docker
  installed `0.5.13-hw` was healthy, 0 restarts, `/health=200`,
  `/ready=503`; SQLite quick_check OK, 21,950 real measurements,
  zero artificial 0 V. Last real measurement at 10:30:08
  (235.7 V / 50 Hz) is historical only, not current voltage.
  Telegram API doctor OK, SQLite doctor OK, Shelly doctor error
  as expected. Prometheus returned NaN for offline live voltage
  and frequency, with offline connectivity correctly active.
- **SOAK / READ-ONLY:** the two-hour soak window contained 59
  real samples before the interruption, with no fabricated
  readings during the open outage. Sample-gap statistics
  describe observed sampling only; outage journal entries,
  not missing samples, define confirmed connectivity outages.
- **CODE AUDIT:** the production code records availability
  alerts only on a connectivity transition and uses an
  idempotent open-outage database check. The existing short
  test used a mocked outage writer and did not cover prolonged
  failures and a restored open-outage state across restart.
- **SOURCE-ONLY TESTS:** added two tests with a temporary real
  SQLite database: 120 consecutive failed polls generate
  exactly one open outage and one optional Telegram
  unavailability alert; a new monitor initialized with that
  same active outage never duplicates the outage or alert.
  The last good sample remains intact, there are no fake
  0 V rows, and the text says Shelly is unavailable rather
  than asserting confirmed electrical power loss.
  Isolated network-disabled Docker suite **443/443 PASS**;
  Ruff, PEP8, maximum 40-line function gate PASS. No live
  Telegram API call, fabricated physical reading or
  production restart.
- **NEXT:** retain valid private outage #13 pre-recovery
  baseline and verify the specific event after natural
  reconnection; then perform an independent 15-minute
  stable recovery check and inspect the real Telegram
  channel. The user's approximate 17:00 Kyiv restoration
  time remains tentative, not verified. Keep public
  promotion and alternate Shelly hardware acceptance
  pending.

## 2026-11-06 11:49 Europe/Kyiv — outage #13 still open; recovery CLI host-path fix

- **LIVE / FIELD:** LXC 110 installed production `0.5.13-hw`
  remained healthy with zero Docker restarts. At 11:48 Shelly
  was offline, /health HTTP 200 and /ready HTTP 503.
  Outage #13 remained open since 10:30:51, with last valid
  235.7 V at 10:30:08 now **stale**. SQLite quick_check OK,
  21,950 samples and no synthetic 0 V. Existing private
  baseline #13 unchanged and mode 0600. The 12 Telegram
  failures remain historical, with no new poll/status errors
  during this outage.
- **QA / SOURCE-ONLY PR #142:** the read-only
  `scripts/recovery-acceptance.py` now chooses
  `/data/voltage.sqlite3` when mounted in Docker, otherwise
  the host path `/var/lib/shelly-voltage/voltage.sqlite3`
  when present. Explicit `--db` still takes priority,
  and absent databases are not created. Four tests cover
  host/container precedence, missing files and explicit
  override. Full isolated network-disabled test suite
  **441/441 PASS**, Ruff, PEP8 and 40-line function gate PASS.
  GitHub CI Python 3.11/3.12/3.13 PASS and PR #142 merged
  into `main` as `b6b552c`. Production not updated.
- **FIELD CHECK / SOURCE ONLY:** executing the candidate
  read-only baseline function without an explicit `--db`
  on the real LXC correctly chose the host SQLite, returned
  `status=pass`, identified open outage #13 and did not
  expose the raw Telegram message ID. This checks the
  path and open-outage baseline only; it is **not**
  evidence of successful recovery or Telegram delivery.
  Do not overwrite the original accepted private baseline.
- **BACKUPS / PASS:** latest three retained daily archives
  passed checksum and temporary SQLite restoration, with
  newest under 36 hours (source-only newest-age policy).
- **RECOVERY FOLLOW-UP:** existing enabled hourly read-only
  task retains the correct outage #13 baseline and
  Europe/Kyiv timezone. Approximate user-provided
  electricity restoration time 17:00 remains tentative.
  After natural reconnection require real new samples,
  outage closure, /ready HTTP 200, explicit recovery
  verifier PASS, separate 15-minute stable-status
  verification and operator-visible Telegram channel check.
  No artificial outage or production restart.

## 2026-11-06 11:22 Europe/Kyiv — offline release gate and installer dry run

- **LIVE STATE:** outage #13 remained open from 10:30:51 Kyiv.
  At 11:17 the installed `0.5.13-hw` monitor was healthy,
  Docker restarts 0, /health=200, /ready=503, and
  SQLite quick_check OK with 21,950 real samples and 0 fake
  voltage-zero rows. Last real voltage was recorded 10:30:08
  (235.7 V); it is **stale** and cannot confirm live mains.
  Ten warnings during the offline period were classified as
  Shelly polling/network unavailability, with no new
  Telegram polling or channel-status update errors.
- **BACKUPS / PASS:** the latest three daily archives passed
  read-only checksum and SQLite restoration in isolated
  network-disabled Docker. The newest archive passed
  the 36-hour freshness check.
- **SOURCE / PASS:** PR #139 (`f95a169`) added independent
  stale-safe live voltage/frequency fields for each
  configured L1/L2/L3 phase. 437/437 isolated tests,
  Ruff/40-line quality gate and GitHub CI on Python
  3.11/3.12/3.13 PASS. Still source-only; no live upgrade.
- **INSTALL PREVIEW / PASS:** current `main` installer
  `install.sh --dry-run --non-interactive` returned
  `DRY-RUN PASS`, without changing production.
- **PROMOTION GATE / EXPECTED FAIL:** read-only
  `promotion-check.py` against the actual open outage
  #13 baseline and live services returned exit 2,
  `status=fail` and six recorded gate reasons, including
  open outage, missing post-baseline fresh samples,
  pending Telegram status rotation and /ready HTTP 503.
  This is a correct release block during offline,
  **not** a completed physical acceptance.
  The private 0600 evidence file stays on LXC 110:
  `validation/2026-11-06-outage13-promotion-gate-offline.json`.
- **RECOVERY:** the user's approximate 17:00 Kyiv estimate
  remains tentative. The existing hourly read-only
  automation uses the exact valid private outage #13
  baseline; no new duplicate automation was created.
  Do not run a real installer, force an outage, alter
  electrical thresholds, claim Telegram delivery or
  promote release without fresh field evidence.

## 2026-11-06 11:18 Europe/Kyiv — continued offline QA and phase-safe JSON

- **LIVE HARDWARE / UNCHANGED:** on Proxmox LXC 110, installed
  `0.5.13-hw` stayed Docker healthy with 0 restarts while Shelly
  Pro EM-50 was offline. Outage #13 remained open since
  10:30:51 Kyiv; at 11:17 /health HTTP 200 and /ready HTTP 503.
  Last actual reading was at 10:30:08 (235.7 V), now stale.
  SQLite quick_check OK, 21,950 samples, 0 manufactured
  0 V samples, no current live voltage claim.
- **LOGS / EXPECTED WARNINGS:** 10 prior WARNING lines in
  the outage interval categorized as Shelly polling/network
  unreachable, not Telegram command or status errors.
  Cumulative Telegram failure count remains 12 from the
  earlier recovered network interruption. No new Telegram
  poll/status-update errors observed for outage #13.
- **BACKUP RETENTION / PASS:** real production backups
  validated read-only in a network-disabled Docker container,
  with 3/3 SHA256 and SQLite restoration PASS and the
  newest archive under the 36-hour freshness limit.
- **SOURCE-ONLY PHASE IMPROVEMENT / PR #139:** the additive
  `/status.json` live/cached fields introduced by PR #137
  now apply independently to configured `phases.L1/L2/L3`.
  One disconnected or stale phase returns null live voltage
  without hiding valid live readings on other phases;
  unconfigured phases preserve their legacy shape.
  437/437 isolated unit tests PASS; Ruff, max 40-line
  functions and GitHub CI Python 3.11/3.12/3.13 PASS.
  Merged to `main` as `f95a169`; not installed on the
  currently running production bot. These synthetic tests
  are not physical Pro 3EM/3CT63 acceptance.
- **RECOVERY MONITORING:** existing enabled hourly, read-only
  automation is configured with the exact private outage #13
  pre-recovery baseline. The user's approximate 17:00 Kyiv
  electricity restoration estimate remains **unconfirmed**.
  After actual reconnection, require new real samples,
  outage #13 closure, /ready HTTP 200 and explicit
  recovery-acceptance.py verify PASS. Independently verify
  channel-visible Telegram status. Do not simulate a
  power event or promote public release on API checks alone.

## 2026-11-06 10:46 Europe/Kyiv — outage #13 offline, safe API and monitoring checkpoint

- **LIVE / READ-ONLY:** Proxmox LXC 110 remained up with deployed
  `0.5.13-hw`, Docker healthy and zero restarts; /health HTTP 200,
  /ready HTTP 503. Shelly Pro EM-50 still offline with open
  outage #13 (started 10:30:51), 21,950 valid SQLite measurements,
  PRAGMA quick_check OK and no synthetic 0 V records. The last
  valid 235.7 V / 50 Hz measurement was at 10:30:08 and is
  **stale**, not a current power measurement or proof that
  mains electricity is disconnected.
- **EXPECTED RESTORATION / TENTATIVE:** the operator estimates
  restoration around 17:00 Europe/Kyiv according to a local
  schedule. Do not interpret that schedule as a confirmed event
  or force a status transition.
- **AUTOMATED FIELD FOLLOW-UP:** the existing enabled hourly
  read-only Shelly recovery task was corrected from an old
  outage #7 baseline to outage #13's valid private 0600 file.
  Its hourly schedule remains unchanged. It must compare
  actual connectivity, fresh measurements, /ready and Telegram
  status rotation, without restarts or artificial events.
  A separate full 15-minute stable-recovery baseline is required
  if the natural stability timer becomes active.
- **SOURCE / PR #137:** added nonbreaking status JSON
  `live_voltage`, `live_frequency`, `sample_is_stale` and
  `sample_age_seconds` so downstream dashboards cannot confuse
  last-known voltage with live voltage while Shelly is offline.
  Legacy `voltage` and `frequency` remain unchanged.
  434/434 isolated network-disabled Docker tests, Ruff/PEP8,
  <=40-line functions and GitHub CI Python 3.11/3.12/3.13
  PASS. Merged into main as `61b9f53`. The feature is
  **not yet deployed** to the running bot.
- **NEXT:** await actual Shelly connectivity; verify outage #13
  against `validation/natural-baselines/outage-13-20261106-baseline.json`
  read-only and mark the exact PASS/FAIL reasons. Independently
  inspect the visible Telegram status channel, retain both
  historical and new SQLite measurements, and keep public
  release promotion gated. No forced network, relay or
  electrical test.

## 2026-11-06 10:35 Europe/Kyiv — natural Shelly outage #13, recovery baseline captured

- **LIVE / CONFIRMED:** Shelly Pro EM-50 became unreachable again at
  10:30:51 Kyiv. The last valid reading was 235.7 V / 50.0 Hz at
  10:30:08, and it is **stale**, not a present-voltage reading or
  confirmation of power loss. At 10:32 the 0.5.13-hw Docker
  container was still running/healthy with zero restarts;
  HTTP `/health=200` but `/ready=503` due to lost
  connectivity. SQLite quick_check OK, 21,950 measurements,
  one open outage (#13), and zero fabricated 0 V rows.
- **NATURAL RECOVERY BASELINE / PASS:** unlike the already
  closed prior outage #12, this time the operator captured
  an accepted recovery baseline for the **open** outage #13
  with the correct host-side SQLite argument:
  `--db /var/lib/shelly-voltage/voltage.sqlite3`.
  The private 0600 file
  `validation/natural-baselines/outage-13-20261106-baseline.json`
  holds outage ID, counts and a SHA256 fingerprint of the
  status message, not the raw private Telegram ID.
- **GATES / EXPECTED FAIL WHILE OFFLINE:** a read-only recovery
  check at 10:34:55 reported outage still open, no new valid
  measurements, no status-message rotation, Shelly offline
  and readiness not ready. The previous outage #12's
  15-minute stable baseline was also correctly rejected:
  it was interrupted by this new outage. Neither acceptance
  was marked successful.
- **BACKUP / PASS:** three retained daily backup archives
  passed SHA256 and temporary SQLite restoration, with
  the latest archive younger than 36 hours using source-only
  `--max-age-newest-only` (PR #132). The installed bot and
  database were not modified or restarted.
- **SOURCE REVIEW:** the current tests already cover rejecting
  a later outage during stable recovery. Do not duplicate
  this existing coverage; prioritize the natural field
  verification and Telegram visibility after recovery.
- **NEXT:** if Shelly reconnects, run the read-only
  `recovery-acceptance.py verify` with the exact outage #13
  baseline and correct host database path. Mark success
  only if the verifier reports PASS; separately inspect
  the real Telegram channel. No synthetic mains event,
  relay action or alert injection.

## 2026-11-06 10:22 Europe/Kyiv — natural Shelly recovery after low voltage

- **REAL EVENT / OBSERVED:** Shelly Pro EM-50 recorded a persistent
  205.0 V `warning_low` at 10:08:33. Voltage later reached approximately
  195.3 V but not the 190 V critical-low threshold; no artificial
  alert or control operation was performed.
- **CONNECTIVITY LOSS / OUTAGE #12:** Shelly became unavailable
  at 10:16:51 Europe/Kyiv. Docker remained healthy with zero
  restarts and HTTP `/health=200` while `/ready=503`
  correctly reflected loss of measurement readiness. The last
  valid 207.1 V measurement at 10:16:09 was **stale**, not a
  current reading or proof of mains power loss.
- **NATURAL RECOVERY / OBSERVED:** outage #12 closed at 10:20:01,
  duration 190.4 seconds. Two real 50 Hz samples (206.3 V,
  204.9 V) were stored during recovery hysteresis before
  the outage was finalized. Afterwards Shelly returned online,
  readiness 200, SQLite quick_check OK, 21,904 measurements
  at the 10:22 read-only check, no artificial zero-voltage
  samples or open outages. No Telegram send or poll errors
  appeared during this event.
- **PRIVATE EVIDENCE:** `validation/2026-11-06-outage-12-recovery-field-acceptance.json`
  with 0600 permissions on LXC 110. The baseline command was
  initially called using its container-only default DB path,
  then retried after the outage had already closed. Both
  attempts failed safely, so the event does **not** have
  a valid pre-recovery baseline; this limits acceptance
  claims. A failed-baseline file was labeled accordingly.
- **QA / SOURCE-ONLY:** PR #134 adds regression coverage for
  lower 207/190 V threshold boundaries, 15-second
  warning/critical dwell, 30-second recovery and brief
  sub-207 V dips. 432/432 isolated unit tests, Ruff and
  CI Python 3.11/3.12/3.13 PASS, merged to `main`.
- **NEXT:** confirm the natural low-voltage message and
  outage/recovery update in the actual Telegram status
  channel. A stored status message ID, lack of API errors
  and internal event metadata are not a visual receipt.
  Keep public promotion and alternate Shelly hardware
  acceptance open until separate verification.

## 2026-11-06 10:14 Europe/Kyiv — natural low voltage warning and lower-limit QA

- **LIVE / REAL PHYSICAL SHELLY:** Pro EM-50 remained online at
  50.0 Hz. After the earlier elevated 250+ V readings the
  voltage dropped below 207 V at about 10:08 and reached
  a read-only observed low of approximately 195.3 V
  at 10:10:45. By 10:14 voltage had recovered to
  202.3 V but remained below the 207 V warning threshold.
  Of the preceding ten minutes, 38 recorded measurements
  were at or below 207 V; none reached or passed the
  190 V critical-low threshold.
- **NOTIFICATION STATE / PERSISTED:** the live SQLite metadata
  recorded `last_voltage_event.kind=warning_low` at
  10:08:33 Europe/Kyiv with a 205.0 V measurement and a
  corresponding low transition entry. The stable status-message
  ID is stored (its value remains private); there were zero
  Telegram send/status-update errors and zero command-poll
  errors after the warning at the 10:14 check. These facts
  support internal event handling but are not a verified
  subscriber-visible channel delivery receipt.
- **PRODUCTION / UNCHANGED:** installed `0.5.13-hw` Docker
  healthy, zero restarts, HTTP `/ready=200`, SQLite
  `quick_check=ok`, 21,878 real samples, zero fabricated
  0 V rows and no open Shelly connectivity outage.
  The Telegram API doctor returned OK. Historical
  transport errors remain cumulative at 12; no new ones
  have occurred since 09:50.
- **QA / SOURCE ONLY:** two new regression cases exercise
  the exact 207 V warning-low and 190 V critical-low
  boundaries with 15-second warning/critical dwell,
  30-second recovery dwell, and a brief 202 V dip
  returning above 207 V before any alert. Tests use mocked
  local time and events, no live device or Telegram calls.
  The full isolated unit suite is 432/432 PASS; pinned
  Ruff and 40-line-function quality gate PASS.
- **NEXT:** keep physical-voltage thresholds unchanged,
  continue natural transition and Telegram-channel
  observation, check whether a subscriber actually sees
  the warning-low status, and retain verified rollback
  snapshots. Public release promotion and alternate
  Pro 3EM/3CT63 physical acceptance remain separate.

## 2026-11-06 09:53 Europe/Kyiv — restored transport and clearer Telegram diagnostics

- **LIVE / PASS:** Proxmox LXC 110 on installed `0.5.13-hw` remained
  healthy with zero Docker restarts and HTTP `/ready=200`. Shelly
  online at 227.5 V / 50.0 Hz, SQLite `quick_check=ok`,
  21,732 measurements, zero 0 V rows and zero open outages.
- **TELEGRAM / TRANSIENT NETWORK EVENT:** at about 09:48–09:49
  Kyiv, 11 Telegram `ConnectionError` and one `ReadTimeout`
  occurred during the same interval as a transient Gateway SSH
  timeout; five channel-status edits also failed due to transport
  connectivity. This is not the former `message is not modified`
  bug: there were zero such errors. Telegram/Shelly/SQLite
  read-only doctor returned OK at 09:52, and there were no new
  Telegram poll or status-update errors after 09:50 by 09:53.
  The existing `telegram_failures=12` counter is cumulative.
- **BACKUP / PASS:** all three retained daily archives validated
  SHA256 and temporary SQLite restore; newest archive was younger
  than 36 hours. Source-only PR #132 (`6bd7a3d`) adds opt-in
  `--max-age-newest-only` to check latest freshness while
  validating all retained copies. 428/428 tests and CI
  Python 3.11/3.12/3.13 PASS. This option is not yet deployed.
- **NEW SOURCE-ONLY DIAGNOSTIC:** retain the cumulative Telegram
  error counter but add consecutive command-poll failure streak
  and seconds since last successful poll to JSON watchdog and
  Prometheus. A successful cycle clears the streak without
  erasing historical failures; no previous successful poll is
  represented by null in JSON and NaN in metrics.
  430/430 isolated network-disabled Docker tests and pinned Ruff
  quality gate PASS. No production restart or config change.
- **TELEGRAM PRIVATE CHAT / OPERATOR PASS:** the owner confirmed
  repeated presses of the same button do not create duplicate
  messages, reinforcing the earlier Ukrainian `/preview`,
  `/status`, and `/start` visual review.
- **NEXT:** keep source-only observability changes behind CI;
  check production transport metrics after a separately
  authorized installation; inspect channel-message visibility
  after a naturally occurring transition. Real Pro 3EM / 3CT63
  acceptance and public release promotion remain separate.

## 2026-11-06 — operator accepts repeated Telegram button behavior

- **PRIVATE CHAT / OPERATOR PASS:** the owner explicitly tested
  pressing the same Telegram button multiple times after the
  deployed `0.5.13-hw` PR #128 no-op edit hotfix and confirmed
  that the actions work correctly **without creating new messages**.
  This closes the repeated-button duplicate-message UI acceptance
  check. It is a direct operator report, not a fresh server-log
  audit or proof of delivery to every channel subscriber.
- **PREVIOUS VISUAL / PASS:** the owner also showed readable
  Ukrainian `/preview`, `/status` and `/start` private-chat
  responses with plausible real Shelly voltage readings.
  The preview correctly indicated no public channel status edit.
- **GATEWAY ROUTING:** a typed read-only Proxmox host health check
  succeeded through Gateway's Europe execution plane.
  Host diagnostics found no failed systemd units; this does not
  independently establish LXC 110 bot log status at this checkpoint.
  Additional direct SSH and GitHub operations were temporarily
  blocked by tool safety checks before this host inspection.
- **NEXT:** verify current LXC 110 Docker readiness, SQLite,
  Telegram command-poll logs and fresh physical voltage when
  authorized command execution is available. Channel-visible
  message edit/delivery under natural transitions, public
  release promotion and real Pro 3EM/3CT63 hardware remain
  separate acceptance tasks. Do not fabricate an outage.

## 2026-11-06 09:08 Europe/Kyiv — real elevated voltage and threshold QA

- **FIELD / LIVE:** with the installed `0.5.13-hw` hotfix, Shelly
  Pro EM-50 remained online at 245.9 V / 50.0 Hz.
  Read-only SQLite windows: last 5 minutes 243.7–250.6 V
  (average 246.12), last 15 minutes 243.7–251.0 V,
  last 30 minutes 237.7–251.8 V, and last 60 minutes
  232.9–251.8 V. No sample reached the configured 253 V
  warning or 255 V critical threshold. SQLite quick_check
  OK, 21,489 rows, no fake 0 V and no open outage;
  Docker healthy/0 restarts, `/ready=200`,
  Telegram watchdog failures 0.
- **CONFIG / READ ONLY:** active warning/critical upper limits
  253/255 V and lower limits 207/190 V; warnings enabled,
  alert dwell 15 seconds, recovery dwell 30 seconds,
  language Ukrainian, ten-minute edit window and no
  automatic pinning. No voltage thresholds or electrical
  hardware were changed.
- **SCREENSHOT / PARTIAL TELEGRAM ACCEPTANCE:** the operator's
  private Telegram screenshot at 09:05–09:06 demonstrates that
  `/preview`, `/status` and `/start` generated readable
  Ukrainian voltage/status responses (about 244.9 V),
  and the preview explicitly stated it did not change
  the public status-channel message. This is not evidence
  of repeated-button no-op edit behavior or subscriber-wide
  channel delivery. The image remains private.
- **QA / SOURCE ONLY:** added regression cases for the
  exact 253.0 V warning and 255.0 V critical boundaries,
  15-second alert dwell, 30-second recovery dwell, and
  a transient 253.5 V spike reverting below threshold
  without generating an alert. All 424/424 isolated
  network-disabled Docker unit tests and pinned Ruff
  quality gate PASS. No artificial readings were sent
  to production or Telegram.
- **NEXT:** independently verify a harmless repeated
  status/menu callback in the private admin chat, observe
  the bot during any naturally occurring over-voltage
  transition and confirm status-channel visibility.
  Do not simulate outages, adjust device relays or
  reduce thresholds without operator authorization.
  Real Pro 3EM / 3CT63 acceptance stays separate.

## 2026-11-06 08:58 Europe/Kyiv — production Telegram no-op edit hotfix

- **FIX / DONE:** after owner-approved installation of `0.5.13-hw`,
  live Telegram command polling logged three benign API
  `message is not modified` responses as failures, increasing
  `telegram_failures` from 0 to 3. The Bot API doctor remained
  healthy. PR #128 merged as
  `394f06852ae5e700fc376ac3c640e1f236d67de3` and handles
  identical text edits and repeated inline keyboard clearing as
  successful no-ops; actual errors are still propagated.
- **QA / PASS:** 422/422 isolated, network-disabled Python tests;
  Ruff 0.14.2, PEP8 and maximum 40-line functions PASS. GitHub CI
  passed on Python 3.11, 3.12 and 3.13. Tested code and GitHub
  branch matched byte for byte before merge.
- **HOTFIX DEPLOY / PASS:** the same `0.5.13-hw` version was
  redeployed on LXC 110 at 08:55 with the merged fix. Installer
  returned PASS; actual source and Docker image contain the new
  error handling. At 08:58 Shelly remained online, real voltage
  polling continued (21,429 SQLite measurements), `/ready=200`,
  SQLite `quick_check=ok`, no open outages and no artificial 0 V.
  Docker healthy with zero restart count after hotfix start;
  post-hotfix Telegram watchdog errors 0 and startup log warnings 0.
- **CONFIG / PASS:** all six stored configuration/secret file
  digests matched the pre-install baseline. The 10-minute
  status-edit window and pin-off policy were not modified.
- **ROLLBACK / READY:** a separate private `0.5.13-hw` snapshot
  was created at
  `/var/backups/shelly-voltage/upgrades/20261106-085538-0.5.13-hw/`.
  Both SHA256 checks, SQLite temporary restore (21,413 samples)
  and rollback dry run PASS. The earlier `0.5.12-hw` snapshot
  is still available. No real rollback was executed.
- **PRIVATE EVIDENCE:** `validation/2026-11-06-post-hotfix-0.5.13-hw-acceptance.json`
  remains on LXC 110 with permissions 0600; private logs,
  Telegram IDs and live databases have not been published.
- **NEXT / OPERATOR CHECK:** the absence of further warnings
  immediately after a restart does not prove the repeated-edit
  path was exercised live. Use the authorized administrator's
  private bot chat and repeat a harmless status/menu button
  to confirm the reply is unchanged without a command-poll
  failure or a duplicate post. Confirm text/visibility in the
  configured channel separately. Do not simulate utility outages.
  Public promotion and other Shelly hardware variants remain
  outside this supervised test.

## 2026-11-06 08:44 Europe/Kyiv — owner-approved 0.5.13-hw production test installation

- **INSTALLED / PASS:** the owner explicitly authorized updating the
  running Telegram voltage monitor. LXC 110 completed the standard
  `scripts/install.sh --non-interactive` at 08:39 Kyiv from
  `main` `fd45c42fb01aad7035443a142bba4e9aa4bf56d5`.
  Both the on-host `VERSION` and running Docker `/app/VERSION`
  identify `0.5.13-hw`. Docker is healthy, with zero restarts;
  `/ready=200`.
- **REAL SHELLY / PASS FOR CONNECTIVITY AND MEASUREMENTS:** the live
  Shelly Pro EM-50 remained online at 50.0 Hz. In the first
  08:39–08:42 interval, 21 fresh measurements were recorded
  (237.7–241.8 V), and the subsequent read-only check reported
  21,342 total samples, SQLite `quick_check=ok`,
  zero 0 V samples and zero open outages.
- **TELEGRAM API / PASS, VISIBLE DELIVERY / PENDING:** the post-install
  `app.py doctor` returned Shelly/SQLite/Telegram `ok`; the
  watchdog reported zero Telegram failures. This does **not**
  establish that a new 0.5.13-hw Telegram message was delivered,
  edited or seen by a subscriber. Administrator visual acceptance
  remains open. Private-chat `/status` and `/preview` are safe
  user-initiated checks; do not simulate an electrical failure.
- **CONFIG PRESERVATION / PASS:** all six preinstall monitored
  configuration/secret files had identical SHA256 digests after
  installation; the persisted status-edit window remains 10
  minutes and pinning remains off. No credentials or chat IDs
  were included in public evidence.
- **ROLLBACK / READY:** installer created a dedicated private
  `0.5.12-hw` source + SQLite snapshot at
  `/var/backups/shelly-voltage/upgrades/20261106-083902-0.5.12-hw/`.
  Both SHA256 sidecars passed, the SQLite temporary restore
  contained 21,315 measurements, and the newly installed
  `rollback.sh --dry-run --no-start` passed. No real rollback
  was needed or executed.
- **PRIVATE EVIDENCE:** the 0600 owner-approved preinstall report,
  configuration-hash manifest, installer log and post-install
  acceptance JSON remain in `/opt/shelly-voltage/validation/`
  on LXC 110. Do not publish screenshots, Telegram IDs,
  tokens, local databases or private archives to GitHub.
- **RELEASE BOUNDARY:** `VERSION=0.5.13-hw` means the code is
  installed for supervised production testing, **not yet
  publicly promoted or fully hardware/message accepted**.
  Published `RELEASE_VERSION=0.5.12-hw` and historical
  `LIVE_VERSION=0.5.6-hw` have not been changed.
- **NEXT / operator acceptance:** inspect `/preview` and
  `/status` in the administrator's private chat, confirm
  correct RU/UK/EN status labels and message editing in
  naturally occurring events, watch for stale/duplicate
  channel statuses, and retain the verified rollback point.
  Pro 3EM/3CT63 acceptance still requires real hardware.

## 2026-11-06 08:23 Europe/Kyiv — rollback SQLite preflight and field verification

- **SOURCE / DONE:** PR #124 merged as
  `a53bf7faee2aae4fb1b61393f5d5572869240245`. The candidate
  `0.5.13-hw` installer now verifies the just-created SQLite rollback
  archive before replacing source files. The rollback script verifies
  SHA256, source tar safety (existing PR #121) **and** a bounded
  temporary SQLite restore before any rescue, Docker stop, source
  replacement, or live database overwrite.
- **FAIL-CLOSED QA:** malformed gzip, invalid SQLite, symbolic-link
  source archives, bad size limits and over-limit extraction are
  rejected. A temporary rollback fixture containing a deliberately
  corrupted SQLite archive with a matching SHA256 stops **before**
  the Docker stop action. No real rollback was performed.
- **QUALITY / PASS:** full network-disabled Docker suite **419/419**
  tests, Ruff 0.14.2, PEP8 and the 40-line-function gate PASS on
  Gateway. GitHub CI passed on Python 3.11, 3.12 and 3.13. Existing
  temporary-only rollback fixture and installer `--dry-run` PASS.
- **REAL BACKUP / READ-ONLY:** the private 0.5.12-hw versioned
  snapshot under `/var/backups/shelly-voltage/upgrades/` passed
  the new stdlib-only validator with 20,902 restored measurements
  and `PRAGMA quick_check=ok`, followed by rollback
  `--dry-run --no-start`. The on-host snapshot remains private.
- **LIVE / NO DEPLOYMENT:** at the latest 08:22 Kyiv Gateway check,
  the deployed `0.5.12-hw` was healthy with zero Docker restarts,
  Shelly online at 238.2 V / 50.0 Hz, `/ready=200`,
  SQLite quick_check OK, 21,218 samples, zero artificial 0 V
  samples and zero open outages.
- **CONCURRENCY:** PRs #120 and #121 already protect private
  runtime files and source archive paths. Earlier overlapping
  PR #122 was closed in favor of the minimal complementary
  validator in #124, to avoid overwriting those protections.
- **NEXT / owner-gated:** obtain explicit authorization before any
  live `0.5.13-hw` deployment; independently verify real
  Telegram send/edit/visibility and exercise a controlled rollback.
  Simulated hardware, a dry run, and a read-only restore do not
  establish post-upgrade acceptance. Pro 3EM/3CT63 real-device
  testing remains separate.

## 2026-11-06 08:11 Europe/Kyiv — validated rollback hygiene and archive gate

- **LIVE / untouched:** production LXC 110 remains on `0.5.12-hw`,
  Shelly online (238.9 V / 50.0 Hz at 08:09), Docker healthy
  with zero restarts, HTTP `/ready=200`. SQLite quick_check OK,
  21,140 real samples, zero fabricated 0 V rows and no open outages.
- **DONE / PR #120:** source archive filter now excludes
  `validation/`, local `dist*/`, caches, logs, SQLite data,
  private env backups and symlinks. Rollback preserves the
  operator-owned `.env*`, `secrets/` and `validation/` on the host.
  A disposable test rollback verified they survive a version change.
  Exact source parity PASS; 413/413 tests, Ruff and Python
  3.11/3.12/3.13 CI PASS. Merged `24e381325614fc9d9c5c5f3922de35456f8a53c5`.
- **REAL SNAPSHOT / 0.5.12-hw:** rebuilt the private source snapshot
  at `/var/backups/shelly-voltage/upgrades/20261106-072935-0.5.12-hw/`.
  It contains only 75 source entries, including `.env.example`,
  with zero private or generated entries. Source and SQLite
  SHA256 sidecars PASS. The standard rollback dry run PASS.
  The 20,902-measurement SQLite snapshot was previously
  restored successfully in a disposable directory; no real
  production rollback was performed.
- **DONE / PR #121:** `scripts/rollback.sh` validates tar members
  after SHA256 checks and **before** any service stop or file
  replacement, rejecting absolute/traversal paths, symbolic or
  hard links, special files, privileged modes and excessive
  archive size/member counts. Source restore also avoids
  overwriting operator config and evidence from legacy snapshots.
  Two checksum-consistent malicious fixture archives (traversal
  and symlink) were both rejected; a safe throwaway rollback
  passed. 413/413 tests, Ruff and Python 3.11/3.12/3.13 CI
  PASS. Merged `ab2638e5249e6c22f36308f63a7c9b8057cfe5fd`.
- **BACKUPS / PASS:** newest daily backup passed read-only
  SHA256, SQLite restore and an explicit 36-hour freshness
  check (1/1 PASS; archived 20,589 measurements).
- **CANDIDATE GATE / read-only PASS:** after PR #121, exact
  `main` at `ab2638e` (candidate `0.5.13-hw`) passed
  `scripts/promotion-check.py` against real natural Shelly
  outage #11 baseline; no errors, 0 exit status. Private
  report at
  `validation/natural-baselines/outage-11-after-safe-rollback-pr121.json`.
  This is not authorization or evidence of candidate deployment.
- **NEXT / hardware and operator-gated:** preserve installed
  `0.5.12-hw` until explicit authorization for a controlled
  `0.5.13-hw` production install, recipient-visible Telegram
  send/edit acceptance on the new version, and a verified
  version-specific live rollback procedure. Real Pro 3EM/3CT63
  acceptance remains separate until device hardware is available.

## 2026-11-06 07:50 Europe/Kyiv — secure snapshot, candidate QA, and public release safety

- **LIVE / no upgrade:** production LXC 110 remains on `0.5.12-hw`,
  Shelly online, Docker healthy (0 restarts), `/ready=200`,
  SQLite `quick_check=ok`, 21,021 measurements at the last
  read-only check, and no forced electrical actions.
- **DONE / version-specific rollback preparation:** created a private
  `0.5.12-hw` source + online SQLite snapshot in
  `/var/backups/shelly-voltage/upgrades/20261106-072935-0.5.12-hw/`.
  Verified both SHA256 sidecars and the standard
  `rollback.sh --dry-run --no-start`. Separately unpacked the
  source/template and restored 20,902 measurements into a disposable
  SQLite file; `PRAGMA quick_check=ok`, zero synthetic 0 V.
  The *real* production rollback has **not** been executed.
- **SECURITY / corrected before acceptance:** the original snapshot
  archive had captured a historical `.env.pre-*` configuration
  backup because the old installer excluded only `.env`.
  The original archive was discarded and rebuilt with 205 source
  entries, `.env.example` present, no private `.env*` or
  `secrets/` paths; snapshots remain private and were not uploaded.
  Local evidence: `validation/2026-11-06-rollback-ready-0.5.12.json`.
- **SOURCE / PR #117 DONE:** installer snapshots and source copying
  now share `scripts/source-tar.sh`, a filtered tar stream that
  retains the public template but excludes all local environment
  backups, secret folders, Git metadata, and generated bytecode.
  Source helper regression tests added, install dry run PASS,
  412/412 full Python tests PASS, Ruff quality PASS, CI 3.11/3.12/3.13 PASS.
- **SOURCE / PR #118 DONE:** public release packaging independently
  excludes `.env*` backups, restores only `.env.example`,
  and validates staged output against private config files.
  A fake-credential release fixture passed exclusion and reproducibility
  checks; 412/412 Python tests, Ruff, and CI 3.11/3.12/3.13 passed.
  No new public release was created or published.
- **ISOLATED 0.5.13-hw QA:** built latest candidate at
  `77bf2b77529d988344070f0fea3d0c5936d25a3b` on QA LXC 111
  with nonroot read-only Docker, private internal network, no
  production secrets or Telegram. Synthetic 230 V -> 196 V -> 230 V,
  two connectivity outages/recoveries, offline Prometheus `NaN`,
  162 measurements, 2 closed/0 open outages, 0 V rows=0 and
  SQLite quick_check=ok. Temporary candidate containers, network and
  image were removed. Private evidence:
  `/root/shelly-qa-candidate-20261106-0740/acceptance.json`.
  The pre-existing QA monitor remained healthy without restarts.
- **NEXT / blocked acceptance:** preserve running `0.5.12-hw`
  until an explicitly approved controlled `0.5.13-hw` upgrade
  with version-specific rollback and independent live Telegram
  delivery/visible status acceptance. Mock acceptance does not
  prove candidate behavior on physical Shelly. Pro 3EM/3CT63
  hardware testing also remains separate.

## 2026-11-06 07:24 Europe/Kyiv — Telegram screenshot accepted for current production

- **VISUAL / PASS (installed 0.5.12-hw):** operator-provided
  Telegram status-channel screenshot confirms that a recovery/status
  message was visible, with voltage, frequency, the natural 6 h 19 min
  connectivity outage and a 10-minute voltage event summary.
  This is evidence for that visible message, not a receipt for every
  subscriber or live Telegram acceptance of the uninstalled candidate.
- **EVENT DISTINCTION:** real Shelly communication outage #11 ended
  06:37:02 Kyiv; separate voltage normalization was recorded at
  06:57:46 Kyiv / 211.7 V. The displayed summary at 06:57–07:07
  covers only the post-normalization window; its 0 s out of normal
  cannot be extrapolated to the preceding low-voltage readings.
- **SOURCE / DONE:** PR #114 merged as
  `e80392cdc947ed083f39793c703dd767448f8471` to label
  Shelly **connectivity** restoration accurately in RU/UK/EN,
  instead of claiming utility mains restoration. PR #115 merged as
  `e0a8b1b60a4b93b705d5817b42514df6c9e17c38` to clarify
  recovery-first event-summary coverage. Neither changes voltage
  thresholds, monitor data, Telegram pin policy or send/edit logic.
- **QA:** 409/409 and then 410/410 isolated Docker unit tests PASS;
  Ruff 0.14.2 / PEP8 / <=40-line functions quality PASS.
  Both PRs passed GitHub CI on Python 3.11, 3.12 and 3.13.
  Exact merged-source parity on Gateway PASS.
- **LIVE / NO UPDATE:** LXC 110 production remains `0.5.12-hw`,
  healthy with zero Docker restarts, Shelly online, HTTP `/ready=200`,
  SQLite integrity OK and zero invented 0 V readings. Persisted
  settings: status edit window 10 min, pin off; no pinned status ID.
  Private visual-acceptance record:
  `validation/natural-baselines/outage-11-telegram-visual-20261106.json`.
  The screenshot itself is not published.
- **NEXT RELEASE GATES:** no automatic installation. Candidate
  `0.5.13-hw` still needs its own approved live Telegram/display
  acceptance, a fresh current-version rollback snapshot and verified
  rollback procedure, owner approval, and later real Pro 3EM/3CT63
  validation when that hardware is available.

## 2026-11-06 07:06 Europe/Kyiv — natural outage #11 recovery

- **REAL DEVICE / PASS:** Pro EM-50 resumed normal polling without a
  service restart. Outage #11 (L1) opened 00:17:46 and closed 06:37:02
  Europe/Kyiv on 2026-11-06, approximately 6 h 19 min. The service
  was healthy with 0 restarts, `/ready` returned HTTP 200 and fresh
  50 Hz voltage readings resumed.
- **RECOVERY ACCEPTANCE / PASS:** the read-only
  `scripts/recovery-acceptance.py verify` checked the exact baseline
  `validation/natural-baselines/outage-11-20261106-0404.json` and
  returned status `pass`, no errors. The outage closed, new SQLite
  measurements appeared, there were zero fabricated 0 V rows,
  `PRAGMA quick_check=ok`, and the status-message ID rotated.
  A changed message ID is **not** independent evidence of subscriber
  receipt or visual correctness.
- **READ-ONLY PROMOTION PREFLIGHT / PASS:** current candidate
  `0.5.13-hw` at `main` `88da3d7e7a3ce8d1ff50e7c178a1f674bf7df65f`
  passed `scripts/promotion-check.py` against the restored
  production `0.5.12-hw` and outage #11 baseline; exit 0, no errors.
  This is a preflight, **not** permission to install the candidate.
- **TELEGRAM DOCTOR / PASS:** read-only `app.py doctor` reported
  `shelly=ok`, `sqlite=ok`, `telegram=ok` and confirmed the
  destination type `channel`. No Telegram message was sent or edited.
- **ROLLBACK READINESS:** existing rollback script `--dry-run`
  validated the newest retained 0.5.11-hw snapshot checksum. Candidate
  `install.sh --dry-run --non-interactive` passed and explicitly
  planned a new versioned **0.5.12-hw** pre-upgrade source/SQLite
  snapshot while preserving secrets. That new snapshot does not yet
  exist; no rollback of candidate 0.5.13-hw has been exercised on live
  hardware.
- **PRIVATE EVIDENCE:** detailed read-only acceptance and promotion
  reports remain under `/opt/shelly-voltage/validation/natural-baselines/`
  on LXC 110, including `outage-11-natural-recovery-20261106.json`.
  They are not committed to public source control.
- **NEXT:** verify status editing/recovery visibly in the actual
  Telegram channel with the owner; independently confirm acceptable
  release and rollback behavior before a separately approved,
  controlled 0.5.13-hw production promotion. Physical Pro 3EM /
  3CT63 acceptance remains untested without that meter.

## 2026-11-06 05:53 Europe/Kyiv — backup freshness and live gate

- **DONE / source-only:** PR #111 merged as
  `39d5ce2f57304936a9d0080d30f6162d0d677bb8`. Backup validator
  now optionally rejects stale/future-dated backup archives with
  `--max-age-hours`; without this option, behavior is unchanged.
  The limit applies to all selected archives, keeps JSONL path-free,
  and does not enable any automatic checking schedule.
- **QA:** 408/408 isolated Docker tests PASS. Pinned Ruff 0.14.2,
  PEP8, import ordering and the 40-line function gate PASS.
  CI on Python 3.11/3.12/3.13 PASS; exact merged-source parity PASS.
- **Real read-only check:** most recent daily backup passed SHA256,
  SQLite restoration and a 36-hour freshness limit. A 1-hour limit
  produced the expected FAIL/exit 2. Private JSONL evidence:
  `/opt/shelly-voltage/validation/2026-11-06-backup-freshness.jsonl`.
- **Live production unchanged:** `0.5.12-hw` Docker remains healthy
  with zero restarts; physical Shelly outage #11 is still open and
  HTTP `/ready=503`. Read-only `doctor` reports Telegram API and
  SQLite OK, Shelly unavailable. Five cumulative Telegram transport
  failures were visible, with last warnings logged around 03:12.
- **Release gate:** read-only outage #11 verification correctly returns
  FAIL/exit 2 until the exact outage naturally closes, fresh samples
  resume and a stable Telegram status is observed. Actual recipient
  delivery and 0.5.13 physical acceptance remain unverified.
  Do not auto-install or simulate real electrical transitions.

## 2026-11-06 05:44 Europe/Kyiv — isolated candidate hardware simulation

- **DONE / candidate-only:** built `0.5.13-hw` from GitHub `main`
  `fcf33e8570ede3905e3f8839d39a7861e3198eae` inside
  standalone QA LXC 111. Test device was a local synthetic Pro EM-50
  HTTP RPC mock on a private Docker `--internal` network. No
  production secrets, Telegram token, Shelly access, or host port
  publishing were provided.
- **PASS / synthetic:** HTTP readiness started true at 230 V / 50 Hz;
  three consecutive failed polls caused offline readiness HTTP 503
  and one open connectivity outage. Mock recovery restored readiness,
  closed that exact outage, and resumed fresh samples.
- **PASS / synthetic low voltage:** a 196 V / 49.9 Hz test input
  produced five measurements below the 207 V warning threshold;
  returning the fixture to 230 V resumed normal measurements.
  Telegram was disabled, so no alarm delivery or channel-message
  edit was demonstrated by this test.
- **PASS / stale-safe:** on a second mock connectivity loss, exported
  Prometheus voltage/frequency gauges became `NaN`, not a replay of
  the last voltage or a false 0 V. After recovery the readings were
  restored, and both recorded outages were closed.
- **Final QA evidence:** 232 synthetic measurements, two closed
  outages, zero open outages, zero fabricated 0 V samples, and
  SQLite `PRAGMA quick_check=ok`. Local-only report at
  `/root/shelly-candidate-qa-20261106/acceptance.json` (LXC 111).
- **Cleanup:** the temporary candidate, fake RPC, Docker network and
  candidate image were removed. The original `shelly-qa-monitor`
  remains healthy with zero restarts. Production LXC 110 was not
  changed and remains on public `0.5.12-hw`.
- **NEXT / physical acceptance:** outage #11 on the real Shelly
  remains open as of the latest 05:36 check. Wait for natural
  restoration, verify the captured outage baseline, fresh voltage,
  stable-message rotation and actual Telegram recipient receipt.
  A software simulation must not be treated as real hardware or
  Telegram delivery acceptance. Do not auto-promote `0.5.13-hw`.

## 2026-11-06 04:46 Europe/Kyiv Gateway continuation checkpoint

- **DONE / source only:** PR #107 merged as `d471212219a2b78f6f464a3d28937ef847c93055`.
  The backup verifier now checks a SHA256 sidecar's optional archive
  filename, rejects sidecar symlinks and oversized checksum files,
  and supports legacy digest-only / absolute-path sidecars.
- **DONE / source only:** PR #108 merged as
  `46b566153e0df6f18ad0672fa2bfc88df11faa19`.
  Optional `--jsonl` reports one path-free read-only PASS/FAIL record
  with `passed`, `checked` and `requested` fields and unchanged exit codes.
  No automated verification schedule was enabled.
- **QA:** network-disabled isolated full suite 404/404 PASS,
  Ruff 0.14.2 and the 40-line/function quality gate PASS.
  GitHub CI passed on Python 3.11, 3.12 and 3.13 for both PRs.
  Source parity with merged `main` PASS. The latest three actual
  daily backup archives passed checksum and SQLite restoration
  checks (20,589 / 17,414 / 14,501 retained samples).
- **Live field snapshot (Gateway / LXC 110):** Docker remains healthy,
  restart count 0, production `0.5.12-hw`, physical-acceptance marker
  `0.5.6-hw`. Pro EM-50 is offline; `/ready=503`, outage #11 remains
  open, SQLite `quick_check=ok`, 20,589 samples and no synthetic 0 V.
  Private read-only evidence saved as
  `validation/2026-11-06-gateway-progress-0446.json` on production.
- **NEXT / hardware-gated:** verify the captured outage #11 after
  natural restoration, confirm fresh voltage samples and Telegram
  recovery/stable-message behavior, verify rollback readiness and
  administrator-visible Telegram features before considering a
  controlled `0.5.13-hw` promotion. Do not induce an electrical
  event, auto-promote the candidate or enable optional subscriptions.
  Pro 3EM / 3CT63 acceptance remains blocked until real hardware exists.

## 2026-11-06 automated read-only backup acceptance

- **Source stage:** added `scripts/verify-backups.py`, an opt-in CLI
  verifying SHA256 sidecars, bounded gzip extraction into disposable
  temporary files, SQLite `PRAGMA quick_check`, and retained
  measurement counts. It never touches the production database,
  changes the backup archives, restarts Docker, or contacts Shelly
  and Telegram. It exits 0 only if all requested copies pass,
  or 2 for any missing/invalid backup.
- **QA:** the isolated network-disabled container passed
  394/394 unit tests, Ruff and the project 40-line/function
  quality gate. Eleven new tests cover checksum mismatch,
  missing sidecar, bad gzip, broken SQLite, size limit,
  insufficient retention, success and operator `--help`.
- **Real restore evidence:** three latest daily
  `/var/backups/shelly-voltage` archives passed checksum,
  gzip and SQLite restore verification when mounted read-only
  into an isolated no-network container. Restored snapshots had
  17,414 / 14,501 / 12,709 measurements respectively.
- **Ops:** `shelly-voltage-backup.timer` was enabled and active,
  its previous service Result=success, and SQLite production
  `quick_check=ok`. Backup verification itself remains manual;
  no additional automated verification schedule has been enabled.
- **Release boundary:** live version remains `0.5.12-hw`,
  Shelly remains offline with `/ready=503`. A successful
  backup check is not a substitute for natural Pro EM-50
  hardware/Telegram acceptance and a verified rollback.
  The candidate must not auto-promote.

## 2026-11-06 Telegram administrator documentation checkpoint

- PR #103 merged source-only opt-in weekly digest delivery, with
  `/week subscribe` and `/week unsubscribe` accepted in the
  administrator's direct chat. Public channel arguments are ignored.
  Isolated full-suite test: 383/383 PASS; CI 3.11/3.12/3.13 PASS.
- Added an operator-facing administrator guide covering `/events`,
  `/week`, `/preview`, `/view`, weekly scheduling, opt-out,
  privacy, coverage interpretation and a no-disruption acceptance
  checklist. Main README and Telegram UX proposal now point to it
  and distinguish source candidate from installed production.
- Last read-only field checkpoint: Proxmox LXC 110 running,
  production `0.5.12-hw` Docker healthy, restart count 0,
  Shelly unavailable with HTTP `/ready=503`, one open
  connectivity-loss episode, SQLite `quick_check=ok` and no
  synthetic zero-voltage records.
- **Blocked by hardware:** `0.5.13-hw` promotion requires live
  Pro EM-50, a new natural recovery baseline, visible Telegram
  administrator acceptance, owner approval and verified rollback.
  Pro 3EM / 3CT63 acceptance must wait for the actual meter.

## 2026-11-06 optional weekly private digest

- **Candidate source only:** admins may explicitly opt in from a
  one-to-one Telegram chat with `/week subscribe`, and opt out
  with `/week unsubscribe`. Plain `/week` returns an on-demand
  read-only report without subscribing. No one is subscribed
  by default. All confirmations are localized RU/UK/EN.
- **Schedule:** Sunday at/after 20:00 in `TIMEZONE`
  (currently `Europe/Kyiv`). A private digest goes only to
  the opted-in administrator's chat and is sent silently,
  not to the public channel. The weekly report continues
  to distinguish observed data from unknown periods.
- **Deduplication and reliability:** a per-user ISO-year/week
  SQLite marker is written after a successful Telegram API
  send. The scheduler suppresses subsequent sends that week,
  including after restart. Failed sends are rate-limited to
  at most one retry attempt per hour per running process.
  A crash after remote acceptance but before saving the marker
  may require a duplicate; this is a documented delivery
  limitation rather than a claim of exactly-once semantics.
- **Security:** only explicitly authorized administrators can
  opt in. Each preference is independently stored and audited;
  channel or unauthorized user commands cannot change it.
- **Release boundary:** this is not deployed on `0.5.12-hw`,
  does not affect the existing daily report, public channel
  status, recovery/edit window, pin policy, or Shelly power.
  CI, isolated tests, natural hardware acceptance and Owner
  Approval remain requirements before any deployment.

## 2026-11-06 opt-in per-administrator preview layout

- **P2 source stage:** private admin commands `/view compact` and
  `/view detailed` set the corresponding administrator's preview
  layout. `/preview` displays that choice. New accounts default
  to compact; the preference is stored in SQLite and persists
  across application restarts.
- **Public-channel isolation:** the command is accepted only from
  an authenticated administrator's direct Telegram chat; the
  channel's existing status format, 10-minute edit window,
  notification cooldown and default pin=false are unchanged.
- **Security:** invalid commands are read-only; another admin's
  preference is stored under a separate key. A successful change
  is recorded in the existing settings audit journal. All
  private replies are silent and RU/UK/EN localized.
- **Data quality:** detailed mode reuses the existing localized
  full-status renderer. The outage-safe phase renderer continues
  to hide stale voltage when Shelly is offline.
- **Acceptance gate:** the feature is in source, not deployed in
  production `0.5.12-hw`. No physical Shelly or Telegram channel
  action is necessary to validate its software-only behavior.
  GitHub CI, physical hardware testing and Owner Approval remain
  separate gates before any future production promotion.

## 2026-11-06 read-only promotion check while Shelly is offline

- **Observed via Gateway:** LXC 110 and the `0.5.12-hw` Docker
  service remain running, with zero restarts. Shelly was offline
  after a real electricity interruption; `/ready=503`, while
  SQLite `quick_check=ok`, no synthetic 0 V entries and one open
  lost-connection episode were observed.
- **Release blocked as intended:** the read-only `promotion-check.py`
  using the private historical baseline on production returned
  `status=fail`, exit code 2. Reasons: Shelly not online, readiness
  unavailable, and the current lost-connection event does not match
  the old captured outage. No promotion or deployment occurred.
- **Missing baseline UX fix:** private validation evidence is not
  included with a fresh public source checkout. Without an explicit
  `--baseline` path the old CLI returned only `ValueError`.
  The candidate CLI now identifies missing/inaccessible required
  inputs by safe **labels** without exposing host paths, and returns
  a redacted error message for unexpected failures.
- **Safety:** no physical power, Shelly relays, Telegram channel
  messages, deployment settings or live SQLite records were changed.
- **QA:** 360/360 isolated tests PASS, pinned Ruff and short-function
  quality gates PASS, with new tests for missing baseline and
  secret-safe unexpected exceptions.
- **Next release gate:** once electricity returns, capture a
  new natural recovery baseline, repeat the real hardware
  verification and obtain administrator-visible Telegram evidence
  before considering `0.5.13-hw` promotion.

## 2026-11-06 cached-voltage labeling during power interruptions

- **Software guard:** `RuntimeState.restore()` intentionally preserves
  the latest persisted voltage without claiming a live Shelly
  connection. Previously `phase_line()` displayed that number
  unqualified in the `starting` state.
- **Fix:** restored, as-yet-unconfirmed phase voltage now carries a
  localized `сохранено / збережено / cached` explanation. A fresh
  successful Shelly poll removes the cached label; an offline phase
  still conceals the old value entirely.
- **No fabricated data:** status timestamps continue to identify
  last validated measurements, not imagined 0 V events. No Shelly
  writes, Telegram sends, DB mutations, or production restart.
- **QA:** 358/358 isolated tests PASS, Ruff 0.14.2 PASS, project
  function-size and PEP8 quality gate PASS. New regressions cover
  RU/UK/EN, successful poll transition, actual offline status, and
  the private compact preview.
- **Deployment gate:** this behavior is in the undeployed candidate
  until physical Pro EM-50 acceptance and a verified rollback route.
  Production remains `0.5.12-hw` while power is unavailable.

## 2026-11-06 private compact-status preview

- **P2 source stage:** `/preview` is an administrator-only Telegram
  command accepted in the administrator's private chat, not the
  public status channel or general control chat. Its response is
  silent and contains only a localized compact read-only view.
- **Safety:** preview does not send to, edit or pin the channel
  status, change persisted preferences, query Shelly, generate
  measurements, or send test voltage notifications.
- **Offline semantics:** when Shelly has no active connection,
  the current voltage is explicitly unknown; a previously valid
  reading may only contribute its last-observed timestamp. Preview
  never claims cached voltage is live or fabricates a zero reading.
- **Localization:** RU/UK/EN title and explanatory note are checked
  with the existing translation-key and placeholder parity tests.
- **Defense in depth:** unknown commands now produce no response
  even during a connectivity outage. Channel messages cannot invoke
  the private-only preview command.
- **QA:** 353/353 unit tests PASS in a network-disabled isolated
  Docker test run, Ruff 0.14.2 PASS and project quality gate PASS.
- **Deployment gate:** `/preview` is candidate source functionality,
  unavailable on live `0.5.12-hw`. The current power outage must not
  be used to force a release; production changes require a separately
  accepted Pro EM-50 hardware candidate and rollback readiness.
- **Next work:** optional compact/detailed display preference in
  private chat and opt-in weekly report scheduling only after
  explicit user-facing behavior approval and deployment QA.

## 2026-11-06 on-demand weekly voltage-quality digest

- **P2 software stage:** candidate adds read-only `/week`, a rolling
  seven-day quality report in RU/UK/EN. The summary includes
  phase-specific sample min/max/average, measured and unknown time,
  estimated time below/above configured voltage thresholds, and
  *confirmed lost-connection intervals* with their overlap duration.
- **Confidence guard:** only consecutive valid samples separated by
  at most 30 seconds contribute to observed time; longer or missing
  periods are counted as UNKNOWN. The bot never assumes zero voltage
  during a Shelly connection loss. Threshold durations are estimates
  from the preceding sample and are not a certified power-quality log.
- **Delivery safety:** `/week` is on demand only. No Telegram
  scheduled sends, device-control RPC, pin policy, edit-window policy,
  production restart, or SQLite write was introduced.
- **QA:** tests cover 80-second unknown gaps, separate L1/L2 phases,
  missing readings, clipping outage spans to the seven-day window,
  Ukrainian report labels and RU/UK/EN command registration.
- **Release gate:** continue production `0.5.12-hw` until an approved
  `0.5.13-hw` hardware acceptance, rollback plan and channel-visible
  Telegram confirmation. The new CLI alone is not a release approval.
- **Next work:** opt-in weekly scheduling and a compact/detailed
  message setting, after explicit behavior acceptance and hardware QA.

## 2026-11-05 Telegram UX v2 source stage and locale parity

- P1 SOURCE COMPLETE: PR #96 merged the localized compact power-status
  view, a read-only `/events` timeline, private-menu entries for
  events and summary, and automated RU/UK/EN key/placeholder checks.
  Unit tests: 335/335 PASS in an isolated network-disabled container
  and GitHub CI on Python 3.11, 3.12, and 3.13 PASS.
- FOLLOW-UP LOCALE AUDIT: remove the Russian `avg`, `отключений`,
  `maintenance mode` and `Maintenance:` fragments, translate the
  L1 activation label for RU/UK, list `/events` in help, render
  voltage units through the selected language, and translate missing
  clock-difference diagnostics. Additional regression tests are part
  of this candidate-only source stage.
- SAFETY: a disconnected Shelly is not proof of physical mains outage;
  current voltage remains unknown when no valid sample exists.
  Public channel messages do not receive private admin controls.
- PENDING: independent Telegram delivery/visual acceptance of the new
  layout, complete Pro EM-50 hardware acceptance for candidate
  `0.5.13-hw`, retained source/rollback verification, and user-approved
  promotion before touching the healthy production `0.5.12-hw`.
- LATER: optional detailed/compact preference, opt-in weekly quality
  summaries, and Pro 3EM phase comparison only after physical meter
  installation. Mini App requires HTTPS and signed initData validation.

## 2026-11-05 recovery banner visibility window

- The user supplied a real Ukrainian status example showing
  `✅ Живлення відновлено · outage 5г 54хв` long after the outage
  had closed. The recovery banner was stored in in-memory
  `last_recovery_duration` and never expired until process restart.
- Candidate fix: display the one-time recovery banner for a bounded
  30-minute period after a confirmed restore, without altering
  historical outage rows or changing notification policies.
  Keep the exact power-recovery message immediately after an outage.
- Regression tests verify that the banner appears shortly after
  recovery but disappears from the general status after 30 minutes.
  This code is not deployed to the production `0.5.12-hw`.
- The user-provided text proves the message contains voltage,
  daily range, restored state and a timestamp; independent channel
  metadata, subscriber delivery and duplicate-message absence still
  require a private administrator screenshot/confirmation.

## 2026-11-05 candidate promotion preflight version semantics

- FIX IN SOURCE: the read-only `scripts/promotion-check.py` compared
  the running service version to `LIVE_VERSION`. That marker records
  the *latest fully accepted hardware checkpoint* (`0.5.6-hw`),
  not the running Docker release (`0.5.12-hw`). It would incorrectly
  reject an otherwise healthy runtime when the hardware checkpoint lags.
- The checker now verifies that the running `/health` version matches
  `RELEASE_VERSION` (the published/deployed `0.5.12-hw` marker),
  keeping `LIVE_VERSION` as an independent historical checkpoint.
  It also rejects a candidate matching either the deployed release
  or the accepted checkpoint.
- This is a **preflight consistency correction**, not authorization
  to install or promote `0.5.13-hw`. User-visible Telegram delivery,
  real hardware acceptance, rollback readiness and source-artifact
  parity remain separate release conditions.
- Source and unit tests can be checked independently in CI, while
  the real LXC 110 read-only check requires restored Gateway/Tailscale
  connector access. No server files were modified by this change.

## 2026-11-05 stable-recovery continuous-voltage evidence hardening

- PREVIOUS HARDWARE CHECKPOINT: the automated read-only verification
  for the natural 22:11:29 Europe/Kyiv recovery reported PASS at
  22:28:14, with new measurements, no new Shelly outages, a rotated
  Telegram status message ID, and an unchanged audible cooldown.
  Its private report is stored on LXC 110 under
  `validation/natural-baselines/stable-verify-20261105-2229.json`.
  This is not independent subscriber-visible Telegram delivery.
- SOFTWARE HARDENING: a separate read-only continuity guard now checks
  retained `voltage_transition_history` during `stable-verify`,
  requires the captured `normal` transition to be present, and
  rejects any later transition (including a second `low → normal`
  cycle). Missing, corrupted, or older-than-one-hour evidence must
  not produce a false PASS.
- QA: regression cases include an otherwise valid status rotation
  after another low-voltage episode, absent or malformed history, and
  expired evidence. This guard is candidate code, not an automatic
  deployment to production `0.5.12-hw`.
- RELEASE GATE: keep independent confirmation of user-visible
  Telegram messages and complete `0.5.13-hw` hardware acceptance
  distinct from tests that only validate state persisted in SQLite.
  Pro 3EM / 3CT63 hardware validation still waits for user installation.

## 2026-11-05 official Shelly EM.GetStatus error handling

- BUG CONFIRMED against Shelly Gen2 EM status documentation:
  https://shelly-api-docs.shelly.cloud/gen2/ComponentsAndServices/EM/ .
  The three-phase meter reports global faults in `errors`
  (`power_meter_failure`, `phase_sequence`, `ct_type_not_set`);
  individual phase errors appear in `a_errors`, `b_errors`,
  `c_errors`, and optional neutral faults in `n_errors`.
  The undocumented `total_errors` name cannot replace these fields.
- FIX IN UNDEPLOYED CANDIDATE: `Pro3EMClient.read_phases()` rejects
  nonempty global or per-phase fault arrays, as well as malformed
  error fields, before accepting any voltage/frequency sample.
  The legacy `total_errors` alias is also checked defensively.
  Recognized faults produce concise diagnostic codes; unexpected
  remote strings are redacted rather than echoed to logs.
- SAFETY: a shared meter fault rejects the entire three-phase polling
  cycle. No invented voltage readings are stored when the RPC reports
  `power_meter_failure`, even if numeric phase values are present.
  Empty error arrays remain valid; alarm `a_flags` and analogous
  phase flags are distinct from meter errors.
- QA: synthetic regression tests cover global, phase, neutral, malformed,
  unknown and legacy error fields, plus the no-fabricated-reading
  monitor path. This is software-only validation; the Pro 3EM/3CT63
  physical acceptance waits until the user reports device installation.
- PRODUCTION: `0.5.12-hw` continues monitoring Pro EM-50 without
  restart, version promotion, meter selection change or control RPC.

## 2026-11-05 new naturally triggered stable-recovery checkpoint

- NATURAL PHYSICAL EVENT: On production Shelly Pro EM-50,
  phase L1 fell below the configured 207 V warning threshold at
  22:04:13 and again at 22:10:28 Europe/Kyiv, then recovered at
  22:08:57 and 22:11:29. The one-hour minimum observed was
  196 V; the 190 V critical threshold was not crossed.
- PASS: a real active post-recovery stability timer began at
  **22:11:29 Europe/Kyiv**. The existing read-only `capture-once`
  recorded its own accepted baseline; it was preserved privately on
  LXC 110 as
  `/opt/shelly-voltage/validation/natural-baselines/stable-1793909489360.json`
  (directory mode 0700, file mode 0600).
- PASS: an intentional *early* `stable-verify` failed with exit code 2
  because the full 15-minute interval had not elapsed, message ID
  had not rotated, and recovery context was still active.
  These are expected observations, not a release blocker regression.
- PENDING: the later read-only verification must confirm uninterrupted
  15-minute stability, an unchanged outage identity and audible
  cooldown, a rotated status message ID, timer cleanup and fresh
  measurements. A one-time check has been scheduled for after
  the interval; this checkpoint is **not yet accepted**.
- EVIDENCE: the earlier warning/recovery transitions and minimum
  voltage are recorded at
  `/opt/shelly-voltage/validation/2026-11-05-evening-natural-voltage-episodes.json`.
  No Telegram receipt was independently proven and none of the
  measurements proves subscriber-visible delivery.
- SAFETY: no power manipulation, Shelly write RPC, Telegram message,
  production deployment or restart was performed. Physical 3EM/3CT63
  acceptance remains on hold until the user confirms installation.

## 2026-11-05 published release archive vs running container

- PASS: the official `v0.5.12-hw` source archive was downloaded from
  GitHub Releases into temporary storage, and its SHA256 matched the
  published checksum
  `b716d59efff5858a2b7dfef1b8ee9bf47dfd1b744920fd77b560a037f2feef44`.
- PASS: of 64 regular files in the public archive, 57 match the
  production host tree exactly; none are missing. The seven differing
  files are `FILES.SHA256`, `README.md`, `README_EN.md`,
  `README_RU.md`, `README_UA.md`, `RELEASE_STATUS.md` and
  `ROADMAP.md` (deployment documentation/checksums).
- PASS: all six code/runtime files inside the **running** Docker
  container exactly match the official archive by SHA256:
  `app.py`, `advanced_features.py`, `telegram_i18n.py`,
  `notification_features.py`, `VERSION` and
  `requirements.txt`. This is stronger than comparing only host files.
- The separate `SOURCE_SHA256SUMS` on the deployment host is
  not a member of the public release archive; its historical
  mismatches must not be mistaken for running-code drift.
  Do not regenerate or rewrite existing manifests simply to hide
  differences.
- EVIDENCE: private read-only verification report on LXC 110:
  `/opt/shelly-voltage/validation/2026-11-05-release-runtime-parity.json`.
- SAFETY: verification used only local/archive reads and
  `docker exec sha256sum`. Production `0.5.12-hw` was not
  updated, restarted, or reconfigured.

## 2026-11-05 Pro 3EM delivery and offline QA

- WAITING FOR USER INSTALLATION: Shelly Pro 3EM / Pro 3EM-3CT63 is
  expected within approximately two weeks. The user will explicitly
  report when it has arrived and is installed.
- Do not claim physical acceptance or query the new meter until the user
  confirms installation and availability. This does not block other
  software work or Pro EM-50 production monitoring.
- PREPARED IN SOFTWARE: The existing read-only three-phase acceptance CLI
  now rejects unexpected phases, invalid or nonfinite voltage/frequency,
  invalid RPC latency and nonfinite acceptance limits. Its JSON output
  excludes nonfinite numbers so results remain machine-readable.
- OFFLINE QA: Negative scenarios use simulated L1/L2/L3 readings,
  not the real Pro 3EM device, and may pass before it arrives.
- AFTER USER CONFIRMATION: Check `Shelly.GetDeviceInfo`,
  `EM.GetStatus?id=0`, atomic L1/L2/L3 mapping, live readings,
  dashboard/Telegram phase presentation, outage/recovery and export
  integrity. Record private evidence before selecting `pro3em`
  in production.
- RELEASE GATE: Continue operating physical Pro EM-50 using the
  published `0.5.12-hw`; do not enable automatic candidate installation.

## 2026-11-05 natural low-voltage threshold observation (partial hardware acceptance)

- PASS on physical Shelly Pro EM-50 with production `0.5.12-hw`: after
  natural outage #10 had closed, measured voltage dropped below the
  configured 207 V warning threshold (minimum 200.0 V). The critical
  threshold remained 190 V and was not crossed.
- PASS: persisted phase L1 transition history records `low` at
  19:31:47 and `normal` at 19:34:49 Europe/Kyiv, a 182.4-second
  low-state interval. The last audible-notification timestamp matches
  the transition into `low`. The persisted warning dwell is 15 seconds,
  and recovery dwell is 30 seconds.
- PASS: follow-up read-only verification found healthy/ready production,
  no open outages, SQLite quick_check=ok, and no fabricated 0 V readings.
  A private redacted evidence record was saved at
  `/opt/shelly-voltage/validation/2026-11-05-natural-voltage-transition.json`.
- LIMIT: These observations validate a real warning-level detection and
  recovery in the deployed `0.5.12-hw` runtime. They do not prove
  Telegram subscriber receipt, same-message editing, a full 15-minute
  stable-recovery rotation, a critical-voltage transition, or physical
  threshold handling in the unpromoted `0.5.13-hw` candidate.
- SAFETY: no Shelly control commands, test power cuts, Telegram
  messages, deployment upgrades, or production restarts were performed.

## 2026-11-05 independent image attestation and QA checkpoint

- PASS: Production release `v0.5.12-hw` has a public OCI image index
  `sha256:e725d628db6869c39225c5ff5a123bbd94399414fd8be15e13df8cfd9ab2fa46`.
  Cosign verified one signature with the expected GitHub Actions workflow
  identity and OIDC issuer.
- PASS: Both `linux/amd64` and `linux/arm64` image subjects match
  their immutable manifest digests. Each platform contains digest-verified
  SPDX 2.3 (122 packages) and SLSA provenance v1 attestations. Source
  revision matches tag commit
  `d5819ed9b110f887fc032c62dfadad70cff20b0f`.
- EVIDENCE: The read-only registry verification report is stored locally
  on production LXC 110 at
  `/opt/shelly-voltage/validation/2026-11-05-oci-attestations.json`.
  This validates published software artifacts, not the physical voltage
  alarm or Telegram message delivery.
- PASS: Natural outage #10 closed at 19:24:12 Europe/Kyiv. A read-only
  recovery verification passed with online/ready service, new measurements,
  SQLite integrity, zero open outages and no synthetic 0 V samples.
- QA: In an isolated, network-disabled, read-only Docker test environment,
  293/293 tests passed on the `0.5.13-hw` candidate after fixing metadata
  scans of private `.env` backup files. PR #80 passed CI and was merged
  as `92a538a1e21ae75c1c005914e6cdbc77a0bde366`.
- SAFETY: Production remains at `0.5.12-hw`; no Telegram messages,
  Shelly writes, auto-installation, or production restarts were performed.
  The current `LIVE_VERSION=0.5.6-hw` names the latest fully accepted
  physical voltage-transition checkpoint.
- PASS: All 63 entries in the current deployed `FILES.SHA256`
  manifest match the production source tree. An older separate
  `SOURCE_SHA256SUMS` reports 20 mismatches; do not rewrite it
  blindly. Reconcile its historical scope during controlled acceptance.
- PASS: After a temporary Gateway regional executor loss, a fresh
  read-only updater preflight completed successfully on the live system.
  The public release check returned installed=latest=`0.5.12-hw`,
  available=false and auto_install=false. The container remained healthy.
- STILL PENDING: Physical `0.5.13-hw` acceptance, a clean isolated
  15-minute recovery-status verification, administrator-confirmed
  Telegram message receipt, and physical Pro 3EM / 3CT63 testing.

## 2026-11-05 Telegram channel access and message acceptance boundary

- PASS: following natural Shelly recovery and outage #10 closure,
  read-only Telegram Bot API `getMe`, `getChat`, and `getChatMember`
  succeeded from LXC 110 using the configured production credentials.
  The status destination is a channel; the bot is an administrator with
  permission to post and edit messages.
- PASS: the existing read-only outage #10 recovery acceptance check
  confirmed SQLite message-ID rotation and fresh measurements.
- NOT PROVEN: actual user-visible receipt of the specific Telegram
  recovery/status message. Bot API has no general message-history lookup
  by chat/message ID. No messages were sent or edited during this check.
- DOCUMENTED: `docs/TELEGRAM_DELIVERY_ACCEPTANCE.md` explains the
  authorized administrator's visual verification and safe evidence
  collection without exposing secrets or inducing an outage.
- RELEASE GATE: published production stays at `0.5.12-hw`. The
  locally built `0.5.13-hw` candidate has passed synthetic Docker
  tests, but remains unpromoted pending its own physical acceptance.

## 2026-11-05 current candidate isolated Docker acceptance

- PASS: Candidate `0.5.13-hw` was built in disposable QA LXC 111
  from GitHub main commit
  `f802e3bb96491bc6dfbab406977103e087127d68`.
  This was a local unsigned QA build, not a published release.
- PASS: Nonroot UID 10001, read-only image filesystem, dropped Linux
  capabilities, private internal Docker network and no host port mapping.
  Telegram was disabled and no real Shelly endpoint, password or
  production database was provided to the candidate container.
- PASS: the app read a synthetic Shelly EM1 RPC at 230 V / 50 Hz,
  reported candidate version 0.5.13-hw through /health, and returned
  /ready true while the mock sensor was online. Test-only SQLite
  was created and successfully checked with PRAGMA quick_check.
- PASS: isolated synthetic RPC stop caused /ready false, connectivity
  offline and exactly one open outage without inserting 0 V readings.
  Restarting the mock RPC caused /ready true again, closed the same
  outage, and resumed successful measurements. Final snapshot:
  63 test measurements, one closed synthetic outage, zero open
  outages and zero 0 V readings.
- CLEANUP: acceptance evidence is stored privately on LXC 111 at
  `/root/shelly-candidate-qa-20261105/acceptance.json`.
  The candidate and mock containers, private test network, and
  temporary candidate Docker image were removed. The original
  `shelly-qa-http:old` remained healthy with no pending update.
- SEPARATE HARDWARE RESULT: production Shelly naturally came back
  online and outage #10 passed the read-only recovery baseline with
  new fresh voltage readings. Production remains unchanged at
  `0.5.12-hw`.
- NOT YET ACCEPTED: release-signing and publication of candidate
  0.5.13-hw, hardware acceptance of this candidate, uncontrolled
  host power loss, and confirmation of user-visible Telegram delivery.
  No automatic production upgrade is permitted.

## 2026-11-05 natural outage #10 recovery and host updater API acceptance

- PASS: Gateway confirmed Shelly online and production `/ready` HTTP 200
  with fresh 216.6 V / 50.0 Hz at 19:26 Kyiv time. Natural outage #10
  closed on its own; SQLite recorded no open outages, no synthetic 0 V,
  and `PRAGMA quick_check=ok`. Production LXC 110 remains at
  `0.5.12-hw`, Docker healthy with zero restarts.
- PASS: the read-only `scripts/recovery-acceptance.py verify` compared
  production with the previously captured outage #10 baseline. Exit code
  zero, status `pass`, empty errors, latest outage id 10 ended,
  fresh measurements continued, no active voltage episode and the
  Telegram status-message ID rotated. A database ID change is not an
  independent Telegram delivery receipt.
- IMPLEMENTED IN SOURCE (not deployed): the separate host-side updater
  `scripts/github_update.py` now bounds GitHub/GHCR JSON to 128 KiB,
  rejects malformed metadata or release assets and requires both the
  matching source tarball and SHA256SUMS. Invalid OCI metadata is denied;
  the release URL is generated from the validated tag.
- PASS: isolated Gateway checkout with 292/292 unit tests, pinned Ruff,
  PEP8 and short-function quality gates. A real read-only GitHub check
  returned installed=latest=`0.5.12-hw`, auto_install=false.
  A real GHCR digest lookup matched the previously verified
  `sha256:e725d628db6869c39225c5ff5a123bbd94399414fd8be15e13df8cfd9ab2fa46`.
- NEXT: GitHub Actions CI/merge for the host checker hardening, then
  private Telegram delivery acceptance with an authorized administrator
  and candidate release tests on isolated LXC 111. Do not induce a power
  failure, publish an unaccepted release or update production automatically.

## 2026-11-05 offline software checkpoint after PR #72-75

- CURRENT PHYSICAL STATE: Shelly Pro EM-50 is offline during natural
  outage #10. LXC 110 reports `/ready` HTTP 503, 18,850 historical
  samples, zero fabricated 0 V measurements, one open outage and
  SQLite `PRAGMA quick_check=ok`. The production bot stays at
  `0.5.12-hw`, Docker healthy with zero restarts, no updater/timer.
- PASS: an isolated source checkout of latest `main` ran the updater
  electrical preflight against the read-only live SQLite. It rejected
  installation with HTTP 503. Nothing was installed, promoted or
  restarted in production.
- DONE: PR #72 bounded GitHub Releases response size to 128 KiB and
  rejected malformed API data, noncanonical release tags and missing
  assets; 280 Python unit tests passed.
- DONE: PR #73 rejected non-string GitHub release asset filenames without
  crashing private `/updates` messages; 282 tests passed.
- DONE: PR #74 enabled administrator-allowlisted direct Telegram
  `/apartments` and `/updates` commands. Sender ID must match a
  private chat ID, and unrelated commands remain blocked; 285 tests.
- DONE: PR #75 hardened the **existing**, nonduplicated apartment CSV
  importer against files over 256 KiB, ambiguous headers, and symlinks.
  Verified atomic SQLite rollback on second-row failure; 288 tests.
- PASS: final PR #75 GitHub Actions passed on Python 3.11, 3.12 and 3.13,
  including the strict ResourceWarning gate. Test fixtures now explicitly
  close SQLite connections. Quality uses pinned Ruff 0.14.2, PEP8
  formatting and a 40-line function limit.
- QA LXC 111: disposable HTTP monitor remains healthy with no pending
  updater journal. Earlier published signed-image install, rollback,
  reboot recovery and read-only daily checks were accepted in QA.
- HARDWARE NEXT: after **natural** Shelly reconnection, compare live
  outage #10 closure to
  `validation/0.5.12-hw-outage-10-baseline-20261105.json`,
  confirm fresh stable voltage readings and private Telegram delivery.
  Do not simulate voltage failures, clear the pending event manually,
  deploy unreleased `0.5.13-hw`, or enable automatic installation.
- LIMITS: real Telegram delivery, uncontrolled host power-loss recovery
  and candidate `0.5.13-hw` physical acceptance are not yet verified.

## 2026-11-05 existing apartment CSV importer safety audit

- REUSED: existing `scripts/import_apartments.py` CSV implementation and
  its test suite. An initial duplicate prototype was discarded before
  merge to keep the code simple and maintainable.
- HARDENED IN SOURCE (not deployed): refuse CSV files larger than
  256 KiB, reject repeated/missing header columns, and deny symlinks
  as input CSV or target SQLite paths. The established 1000-row limit,
  explicit `--apply`, configured-phase checks and all-or-nothing
  transaction remain in force.
- PASS: 288/288 tests on an isolated Gateway checkout, pinned Ruff and
  maintainability gate. New regression tests cover input-size denial,
  duplicate headers, symlink protection and transaction rollback on a
  second-row SQLite constraint error.
- PENDING: GitHub CI and branch merge. No production database was
  opened for writing, and no Telegram command or image was deployed.
- HARDWARE: natural outage #10 remains open, physical Shelly offline.
  Production LXC 110 stays on 0.5.12-hw pending stable recovery.

## 2026-11-05 secure private administrator commands

- IMPLEMENTED IN SOURCE (not deployed): Telegram administrators may send
  `/updates` and `/apartments` directly to their own private bot chat,
  instead of posting apartment-management commands in a shared control
  chat. Existing authenticated control-chat navigation is preserved.
- SECURITY: the direct message must have `chat.type=private`, the chat
  ID must match the sender ID, and the sender must be in the explicit
  administrator allowlist. Nonadministrators, forged chat IDs, and
  unrelated group-only commands are denied.
- PASS: 285/285 tests on an isolated Gateway source checkout, pinned
  Ruff/PEP8 and maintainability checks. Tests include private updates,
  apartment modifications, denied spoofing and unrelated direct commands.
- PENDING: GitHub CI/merge and authorized real Telegram delivery. No
  production restart or update during physical Shelly outage #10.

## 2026-11-05 malformed GitHub release asset hardening

- IMPLEMENTED IN SOURCE (not deployed): GitHub Releases update notices now
  ignore asset names that are not strings. Unexpected nested lists or
  dictionaries cannot crash the administrator's read-only `/updates`
  command; required source archive and SHA256SUMS are still mandatory.
- PASS (isolated Gateway checkout): 282/282 unit tests and the pinned
  Ruff/maintainability quality gate. Regression tests cover a malformed
  required filename and an unrelated malformed optional asset.
- PENDING: GitHub Actions acceptance and PR merge. No new stable release
  is being published while the physical Shelly is offline.
- HARDWARE WAIT: natural outage #10 remains open in LXC 110, which stays
  on `0.5.12-hw` with automatic installation disabled and no updater
  service or timer installed.

## 2026-11-05 defensive GitHub update response validation

- DONE: PR #71 merged localized, administrator-only GitHub update
  information for RU/UK/EN and rejected stable releases missing
  the matching source archive or SHA256SUMS. Automatic installation
  remains disabled.
- IMPLEMENTED IN SOURCE (not deployed): constrain public GitHub API
  response size to 128 KiB; reject malformed JSON, unexpected response
  shapes and noncanonical release tags before showing an update notice.
- TESTING: regression tests cover malformed metadata, missing assets,
  invalid JSON and oversized responses. GitHub CI must pass before merge.
- PRODUCTION: physical Shelly remains offline in natural outage #10;
  LXC 110 stays on 0.5.12-hw without any updater service/timer deployed.
- NEXT: after natural recovery, verify real Telegram delivery and exact
  outage #10 closure using the private event baseline. Do not force
  a hardware outage or an in-place update during the current event.

## 2026-11-05 private and localized update-check follow-up

- IMPLEMENTED IN SOURCE (not deployed): administrator-only `/updates`
  and the Telegram Updates menu now use the configured Russian, Ukrainian
  or English language instead of English-only messages.
- IMPLEMENTED IN SOURCE (not deployed): only published stable GitHub
  Releases containing both the matching source archive and SHA256SUMS are
  eligible for an availability notice; incomplete release assets are
  rejected. The displayed URL uses the validated tag on the canonical
  project GitHub Releases page.
- IMPLEMENTED IN SOURCE (not deployed): network or metadata errors produce
  a short private localized message, without exposing internal details
  into the shared Telegram control chat.
- TESTING: full CI and isolated quality acceptance are required before
  merge. No production Docker update, timer installation or Telegram
  delivery is authorized during natural outage #10.
- NEXT HARDWARE STEP: after natural recovery, verify actual private
  Telegram delivery and close the outage #10 hardware acceptance baseline.

## 2026-11-05 final read-only updater and Git tag acceptance

- PASS: a real Github Releases API request from the isolated LXC 111
  returned latest public `0.5.12-hw` relative to installed QA
  `0.5.11-hw`, with `Update available: yes` and automatic installation
  disabled. No Telegram message, release publication or Docker action
  was performed.
- PASS: the QA-only `shelly-voltage-update-check.service` executed under
  systemd with DynamicUser, stored its JSON status, and exited normally.
  Its timer was temporarily enabled and its 24-hour cadence parameters
  inspected, then disabled again. The test container's ID, start time and
  restart counter remained identical.
- PASS: the new release policy CLI was exercised against a temporary,
  real Git repository and real `v0.5.13-hw` tag: matching
  `VERSION=RELEASE_VERSION=0.5.13-hw` and matching commit were accepted.
  Changing `RELEASE_VERSION` to `0.5.12-hw` was denied. No tag was
  created in GitHub and no image was published.
- PASS: PR #69 implemented signed release promotion controls, 274/274
  unit tests, pinned Ruff/maintainability checks, and GitHub CI on
  Python 3.11, 3.12 and 3.13.
- LIMITATION: actual Telegram delivery of an update notice to a
  privileged administrator still requires separate authorized acceptance;
  checking GitHub and showing text in a synthetic fixture do not prove
  Telegram delivery.
- PRODUCTION: LXC 110 remains at `0.5.12-hw`, Shelly offline with
  natural outage #10 open, SQLite intact and no updater timer installed.
  Do not upgrade, restart, publish a new release or modify real meter data
  until stable power recovery and acceptance.

## 2026-11-05 daily release check and secure image promotion QA

- PASS: In isolated LXC 111, `shelly-voltage-update-check.service`
  completed as an unprivileged DynamicUser oneshot. Its status JSON reported
  installed QA `0.5.11-hw`, latest public `0.5.12-hw`, available=true and
  auto_install=false. The QA Docker container ID, start time and restart
  count were unchanged. The configured 24-hour timer was started and
  inspected, then disabled again in the QA LXC.
- NOT YET OBSERVED: an entire 24-hour real-time timer interval. The
  `OnUnitActiveSec=24h` configuration and an immediate systemd service
  execution were verified, not a complete calendar-day soak.
- IMPLEMENTED FOR CI: `image.yml` now invokes
  `scripts/image_release_policy.py` before publishing an image. Stable
  signed tags `vX.Y.Z-hw` require a matching tag push, matching
  `VERSION` and `RELEASE_VERSION`, and an exact checkout of that tag's
  commit. Manual builds use `manual-*` tags only and cannot promote an
  arbitrary commit as a signed stable release.
- PASS: release-promotion policy regression tests, complete 274/274
  Python tests, pinned Ruff and maintainability quality gate.
- PRODUCTION: no GitHub update check timer or updater has been installed
  in LXC 110. The physical Shelly remains offline with natural outage #10
  open. No release was tagged, published or installed.

## 2026-11-05 positive signed-update acceptance in isolated LXC 111

- PASS: The full updater installed published, Cosign-verified
  `v0.5.12-hw` over a disposable `0.5.11-hw` QA fixture using the real
  GitHub Release, immutable GHCR digest and source archive SHA256.
  Preflight, storage checks, restricted backup and post-update checks passed.
  `apply-update.sh` returned `UPDATE PASS: 0.5.12-hw`.
- PASS: The upgraded application (not a stub) polled an isolated synthetic
  Shelly `EM1.GetStatus` endpoint at 230.0 V and 50.0 Hz. Telegram was
  explicitly disabled. `/health` returned version `0.5.12-hw` and
  `/ready` returned HTTP 200; new rows were written to test SQLite.
  No production network, meter or Telegram credentials were used.
- QA FIXTURE: initial attempts failed because a local test-only Compose
  file omitted `env_file` and the synthetic SQLite lacked `phase`.
  Correcting the fixture, not the product source, resolved startup.
  LXC 110 production database schema version 3 already contains
  `phase`; the standard product Compose already passes `.env`.
- PASS: the test-only Compose healthcheck was adjusted to work with both
  BusyBox and Python images. The upgraded signed image was Docker
  `healthy` as well as HTTP ready.
- PASS: after an actual reboot of LXC 111, systemd restarted that pinned
  signed image and the synthetic Shelly RPC fixture. The digest, deployed
  version metadata, healthy Docker state, HTTP readiness and SQLite
  integrity persisted; no pending-update marker remained.
- CLEANUP: the QA test saved an acceptance JSON and post-upgrade SQLite
  snapshot in `/root/qa-positive-acceptance-20261105/` on LXC 111.
  The original `shelly-qa-http:old` fixture and original test SQLite
  (2 rows) were restored, the simulated RPC stopped, and update pinning
  metadata removed. The final QA `/ready` and SQLite quick check passed.
- LIMITATION: the signed image was tested against a simulated Shelly,
  not a real voltage transition; Telegram delivery, uncontrolled host
  power loss and promotion of candidate `0.5.13-hw` remain unverified.
- PRODUCTION: Shelly is offline with natural outage #10 open.
  LXC 110 stays on `0.5.12-hw`, without the updater/timer installed;
  no production deployment is authorized until recovery acceptance.

## 2026-11-05 LXC 111 signed updater and reboot acceptance

- PASS: Gateway reconfirmed that dedicated, unprivileged QA LXC 111
  `shelly-updater-qa` is running with Docker, Compose, Cosign 3.0.6, and a
  separate synthetic HTTP/SQLite monitoring fixture. No production Shelly,
  Telegram secret or SQLite history was copied into the fixture.
- PASS: updater dry-run from QA release `0.5.11-hw` to public
  `v0.5.12-hw`: fresh synthetic 230 V reading, GitHub target policy,
  GHCR signature identity and Cosign certificate, public archive SHA256,
  and `DRY-RUN PASS`. An initially stale fixture was correctly rejected.
- PASS: a real attempted signed-image replacement in QA failed to become
  healthy, as expected without the physical Shelly configuration.
  The updater exited nonzero and restored the prior
  `shelly-qa-http:old` container, the verified SQLite snapshot (2 rows),
  and original image choice. Both fixture HTTP readiness and SQLite
  integrity passed after rollback; pending journal was cleared.
- PASS: the QA-only Compose systemd service was installed and enabled with
  `ExecStartPre=apply-update.sh --recover`. A durable, QA-only pending
  marker and invalid override were prepared, then LXC 111 was actually
  rebooted. Startup logged `RECOVERY: interrupted update`, restored the
  previous image and SQLite, removed the pending marker/override, and
  returned healthy/ready responses.
- PASS: a second real QA reboot with an intentionally malformed pending
  marker made systemd refuse to start the Compose unit. No container was
  running. The bad journal was retained for investigation. After deleting
  only this QA test marker and resetting the failed unit, the old fixture
  returned healthy/ready.
- PASS: third real QA reboot with a checksum-corrupted SQLite backup.
  The Compose service detected SHA256 mismatch, refused to start, left the
  pending journal in place, and did not launch a container. After restoring
  the correct checksum from the QA-only fixture, systemd recovered the prior
  image, cleared the pending marker, and SQLite integrity plus /health and
  /ready all passed.
- LIMITATION: these were controlled LXC reboots with a prepared durable
  interruption marker. They do not demonstrate recovery from an actual
  uncontrolled power loss, filesystem corruption, or an unresponsive
  Docker daemon.
- COMPLETED in the later positive signed-update acceptance checkpoint:
  the published image started successfully in QA, wrote synthetic
  measurements, passed HTTP and Docker health checks, and survived reboot.
- PASS: corrupted-snapshot recovery was also tested through systemd.
  Still pending: public release promotion rules and Telegram delivery
  verification.
- PRODUCTION: LXC 110 still runs `0.5.12-hw` with natural outage #10 open,
  `/ready` HTTP 503 and no updater services installed. Do not install,
  reboot or promote the candidate while that condition persists.

## 2026-11-05 disposable LXC acceptance preparation

- PREPARED: dedicated QA container VMID 111, hostname
  `shelly-updater-qa`, Debian 13, unprivileged, nested container support,
  1 CPU, 1 GiB RAM, 6 GiB disk, and onboot disabled. No production secrets,
  Telegram credentials, or Shelly configuration were copied.
- BLOCKED: the Gateway executor lost the initial `pct start 111` job.
  A later start attempt also failed at the Gateway tool level. The last
  read-only `pct status 111` returned `stopped`; no Docker/reboot
  acceptance was performed in that container. Do not assume a Proxmox
  boot failure without additional host-side evidence.
- DONE: PR #62 implements fail-closed recovery of interrupted updates using
  a persisted journal, pre-start systemd recovery, and retained SQLite
  rescue snapshots. Its 258 unit tests and Python 3.11/3.12/3.13 CI passed.
- DONE: PR #63 fixes updater version selection: prefer a recorded deployed
  version, otherwise the installed `RELEASE_VERSION`, rather than assuming
  that a checked-out candidate `VERSION` is already installed. GitHub CI
  passed on Python 3.11, 3.12 and 3.13.
- NEXT: determine why the isolated LXC 111 did not start; install test-only
  Docker dependencies and exercise signed update, rollback, boot recovery
  and loss-of-power simulation without production credentials.
- BLOCKED: production LXC 110 remains on `0.5.12-hw`, with no updater
  timer or services installed. Do not deploy while Shelly is offline or
  outage #10 remains unresolved.

## 2026-11-05 interrupted-upgrade boot recovery

- IMPLEMENTED IN CODE (not deployed): write and flush a root-only
  `pending-update` journal before changing the pinned Compose image.
- IMPLEMENTED IN CODE (not deployed): the Compose systemd unit runs
  `apply-update.sh --recover` before starting the bot. An interrupted update
  stops the unconfirmed image and restores the former image selection and
  verified SQLite snapshot.
- SAFETY: malformed markers or damaged backups block startup; keep the
  pending journal for manual intervention. A first post-failure rescue copy
  is not overwritten during repeated recovery attempts.
- PASS: 6 isolated interruption tests, including no pending update, valid
  recovery, invalid journal, damaged backup, retained rescue and durable
  journal write.
- PASS: the complete 258/258 Python unit tests, pinned Ruff/maintainability
  gate, Bash syntax, systemd unit verification and release dry-run.
- NOT VERIFIED: an actual interrupted Docker replacement and full LXC
  reboot; these require a disposable test LXC before production acceptance.
- BLOCKED: no updater systemd unit, timer or new application image is
  installed in production. The physical Shelly is still offline with
  natural outage #10 unresolved.

## 2026-11-05 outage #10 and updater rollback acceptance

- OBSERVED: natural Shelly connectivity outage #10 is open on the physical
  Pro EM-50. The most recent valid voltage before disconnection was 215.1 V;
  it is a stale reading, not current mains voltage. Production `/ready`
  returns HTTP 503 as intended. Docker is healthy with zero restarts.
- PASS: an event-specific read-only recovery baseline was stored on LXC 110:
  `validation/0.5.12-hw-outage-10-baseline-20261105.json` (status PASS).
  It records outage id 10, 18,850 existing readings and no synthetic 0 V.
- PASS: installed Cosign 3.0.6 at `/usr/local/bin/cosign` independently
  validates the published `v0.5.12-hw` GHCR image signature. Debian Cosign
  2.5.0 does not discover that signature; the updater requires Cosign v3+.
- PASS: the published source's update preflight was executed read-only on
  the offline LXC and denied deployment due to HTTP 503. No updater systemd
  units, update timer or new application image were installed in production.
- FIXED in PR #59: use unique `mktemp` recovery files for SQLite rollback,
  preventing collision with an interrupted earlier rollback; 251/251 tests.
- FIXED in PR #60: preserve committed SQLite WAL records in the post-failure
  rescue snapshot using the SQLite backup API, not a main-file-only copy;
  252/252 tests passed, including a WAL-specific regression test.
- PASS: GitHub Actions on Python 3.11, 3.12 and 3.13 for both PRs.
- NEXT: after natural power recovery, verify outage #10 closure and stable
  voltage against its baseline, then validate the versioned signed update
  and crash/reboot rollback on a disposable test LXC before production.
- BLOCKED: no installation or promotion of the candidate while the active
  electrical/connectivity event remains unresolved.

## 2026-11-05 secure GitHub updater implementation

- IMPLEMENTED IN CODE (not deployed): read-only daily GitHub Releases checker with a 24-hour
  systemd timer; automatic installation explicitly disabled by default.
- IMPLEMENTED IN CODE (not deployed): administrator-only Telegram `/updates` command sends
  version information privately. The application has no Docker socket access
  and no authority to start the root updater.
- IMPLEMENTED IN CODE (not deployed): separate manually started root systemd oneshot downloads
  an immutable GHCR image, verifies its Cosign GitHub Actions identity and the
  published source archive SHA256, and refuses unlisted or older releases.
- IMPLEMENTED IN CODE (not deployed): enforce `/ready`, fresh voltage between 207 V and 253 V,
  SQLite integrity, no open outage, no active voltage episode and no
  unfinished stable-recovery timer before and after a candidate restart.
- IMPLEMENTED IN CODE (not deployed): create protected database/configuration/secret snapshots,
  preserve the image pin across reboots, and restore previous image/database
  after detected update failures with an additional post-failure DB rescue.
- PASS: 249 Python tests and the pinned Ruff/maintainability gate, systemd
  unit verification, Docker build and isolated Compose configuration, release
  and installer dry-runs, source checks and offline SQLite rollback tests.
- PENDING: full signed-image replacement and interrupted-host validation on a
  disposable LXC; no released v0.5.13-hw container exists yet.
- NOT DEPLOYED: no updater timer or host service is enabled in production.
  An actual update is blocked while outage/voltage acceptance is incomplete.
- PENDING: independently validate interrupted host reboot, Telegram delivery,
  and manual recovery on a disposable LXC before production promotion.

## 2026-11-05 outage #9 and v0.5.13-hw offline candidate audit

- OBSERVED: natural voltage readings fell from approximately 222.6 V to
  117.5 V on the physical Pro EM-50 immediately before monitoring
  connectivity was lost; the final valid sample was 120.1 V at 11:56:49
  Europe/Kyiv. These readings are device measurements, not synthetic faults.
- OBSERVED: connectivity outage id 9 opened at approximately 11:57:31
  Europe/Kyiv. No confirmed restoration had occurred at the last check;
  `/ready` returned HTTP 503, while Docker health and SQLite integrity
  remained healthy. No synthetic 0 V measurement was inserted.
- DONE: private, event-specific read-only acceptance baseline saved on the
  installation as `validation/0.5.12-hw-outage-9-baseline-20261105.json`.
  Do not reuse a baseline from earlier outages.
- NOT VERIFIED: whether Telegram delivered every critical warning. The
  recorded measurements and notification timestamp are not direct proof of
  Telegram message delivery; validate against the Telegram message history.
- DONE: PR #54 prepared an unreleased software candidate `0.5.13-hw`, while
  public release and production remain `0.5.12-hw` and the latest fully
  hardware-accepted checkpoint remains `0.5.6-hw`.
- DONE: PR #55 added a dry-run-first, atomic CSV apartment importer with
  bounded rows, duplicate rejection, and isolated tests. Python 3.11/3.12/
  3.13 CI passed.
- PASS: on the code merged via PR #55, two independent reproducible
  `0.5.13-hw` source archives had identical SHA256
  `34cf20066fc6c23b35752a583e58d862883af0f640c01e36806771d4d5c0f2b2`.
  The candidate Docker image built and passed an isolated module/version
  import; these were local checks, not published release artifacts.
- PASS: a private copy of the production SQLite accepted an example CSV
  without changing the existing measurements, outages or metadata. The
  live database and container were not modified for these tests.
- BLOCKED: do not publish, install, or promote this candidate while the
  current power-outage acceptance event is unresolved.
- NEXT: after natural recovery, verify exact outage #9 closure, freshness,
  voltage stabilization, and Telegram status rotation against its saved
  baseline. Then perform a backed-up candidate deployment and rollback
  acceptance with the live sensor online.

## 2026-11-05 apartment-to-phase development checkpoint

- DONE: PR #48 introduced the idempotent SQLite apartment-to-phase registry,
  administrator-only Telegram commands, atomic 1–200 apartment range assignment,
  private responses, validation and Docker/release packaging.
- DONE: PR #49 added private administrator phase-selector buttons. Apartment
  lists remain restricted to an authorized administrator's private chat.
- DONE: PR #50 added safe, nullable SQLite migration fields for entrance and
  floor; `/apartments place BUILDING NUMBER ENTRANCE FLOOR` requires an existing
  apartment assignment, preserving the assigned phase.
- DONE: PR #51 implemented read-only building, entrance and floor filters with
  bound SQL parameters and bounded output.
- DONE: PR #52 added `/apartments summary` with aggregate counts by building
  and phase, without including apartment numbers.
- PASS: on the PR #52 source, 227/227 Python tests, the pinned Ruff and
  maintainability gate, and Python 3.11/3.12/3.13 GitHub CI.
- Production intentionally remains at public `0.5.12-hw`; these additions
  have been merged to GitHub `main`, not deployed or hardware-accepted.
  No live SQLite migration or Telegram command change was performed.
- NEXT: plan a versioned candidate release and non-production migration smoke
  on a copied SQLite backup, then conduct controlled deployment acceptance
  without inducing an electrical event.
- NEXT: extend private Telegram management with optional structured CSV import
  and safely gated phase-group notifications, including explicit recipient
  authorization and no apartment lists in public status channels.
- SAFETY: a phase assignment is administrative metadata, never a measurement
  proving that an individual apartment has electricity.
- PENDING: full physical low-voltage/recovery acceptance for the software
  version following the hardware-accepted `0.5.6-hw` checkpoint.

## 2026-11-04 natural restoration evidence (partial hardware acceptance)

- PASS: a naturally occurring Pro EM-50 availability outage was recorded
  as SQLite outage id `6`, L1, from 18:49:40 to 19:46:30 Europe/Kyiv
  (56 minutes 50 seconds). The operator reported that power was off.
  These are *monitor-connectivity* boundaries, not independently verified
  electrical mains-on/off timestamps.
- PASS: at approximately 20:07 local time the device's local EM1 RPC
  returned HTTP 200 (236.8 V, 50 Hz), the Docker monitor was healthy,
  `/ready` reported fresh online measurements, SQLite `quick_check`
  was `ok`, zero-voltage measurements remained at 0, and no outage
  remained open. The database contained 16,689 measurements.
  The Telegram watchdog failure counter was 0 at inspection time.
- OBSERVED: the Telegram status-message fingerprint differed from the
  earlier 18:44 stable baseline and the persisted recovery context
  had been cleared. This is evidence of a changed status message, not
  proof of uninterrupted 15-minute stable recovery.
- PENDING: a separate open-outage baseline was **not** captured for
  event id 6 because Gateway's Tailscale regional executor was unavailable.
  The earlier stable baseline belongs to a prior voltage episode and must
  not be reused to promote the physical acceptance checkpoint.
- DONE: PR #44 added a read-only baseline validation guard that rejects
  a stable-verification result if the recorded outage changed since its
  baseline. Python 3.11, 3.12 and 3.13 CI passed. This tool update is
  in GitHub `main`, not deployed as a new physical release.
- NEXT: capture a fresh event-specific baseline during a future natural
  event and verify closure, status rotation, and continuous stability
  without issuing any control RPCs or artificially disconnecting mains.

## 2026-11-04 current checkpoint

- DONE: public `v0.5.12-hw` release is published at
  https://github.com/iSentinel/shelly-voltage-telegram/releases/tag/v0.5.12-hw.
  The tag points to commit `d5819ed9b110f887fc032c62dfadad70cff20b0f`.
- DONE: Source Release and Container Image GitHub workflows passed.
  Published source archive SHA256:
  `b716d59efff5858a2b7dfef1b8ee9bf47dfd1b744920fd77b560a037f2feef44`.
- DONE: public read-only local-connectivity troubleshooting guide merged
  through PR #39; CI passed on Python 3.11, 3.12 and 3.13.
- DONE: device IP is now DHCP-reserved. A local Pro EM-50 HTTP RPC check
  succeeded after reservation. Do not infer Docker readiness from device
  reachability; check `/ready` independently.
- DONE: independent Cosign verification for the immutable v0.5.12-hw
  GHCR digest `sha256:e725d628db6869c39225c5ff5a123bbd94399414fd8be15e13df8cfd9ab2fa46`.
  The signer identity matches the tag build workflow and the GitHub OIDC issuer.
- DONE: independently retrieved and SHA256-checked OCI manifests, both
  linux/amd64 and linux/arm64 image configs and each digest-linked in-toto
  attestation. Source revision matches tag commit `d5819ed9b110f887fc032c62dfadad70cff20b0f`.
  Both architectures include SPDX 2.3 packages and SLSA provenance linked
  to their exact image-manifest digests. Verification workflow run
  `41860089060` passed; Python 3.11/3.12/3.13 CI `41860089059` passed.
  Reusable independent verifier merged through PR #41.
- PENDING: physical voltage-threshold and stable-recovery acceptance after
  a natural electrical event; latest full hardware checkpoint is 0.5.6-hw.
- PENDING: physical Pro 3EM / Pro 3EM-3CT63 acceptance.
- ON HOLD: optional Mini App publication without a public HTTPS endpoint
  and server-side Telegram `initData` verification.

## 0.5.12-hw - software candidate / live acceptance

- PASS: exact GitHub `main` source deployed in production and source parity
  verified on the physical Pro EM-50 without changing monitoring behavior.
- PASS: quality gate, 200/200 unit tests, healthy/ready service, fresh read-only
  voltage samples, SQLite quick_check, zero synthetic 0 V rows and Telegram
  connectivity after recovering from a changed local DHCP address.
- Operational follow-up: reserve the device IP by its MAC address on the LAN
  DHCP server, so an electrical outage cannot change the address unnoticed.
- Physical voltage-threshold/recovery behavior remains awaiting a natural event.
- Prepared private `/start` menu, combined Status/settings and model-specific
  voltage topology. Pro EM-50 has one voltage phase displayed as L.
- Main CI and image workflow completed successfully. Public release
  metadata is promoted for `v0.5.12-hw`; physical acceptance is pending.
- Full electrical-transition hardware checkpoint remains 0.5.6-hw.
- Mini App preparation remains unpublished, as requested.


### 0.5.11-hw public release verification

- DONE: tag `v0.5.11-hw` points exactly to release commit `391be951b85a98b32a82de423d277f6cff595a1d`.
- DONE: Source Release #9, tag CI #314 and Container Image #80 completed successfully.
- DONE: published source SHA256 `6a1c321682cdf8bf990e2d48fb0c5517076fb9a7d233d3af30aec28c66ca8abe` matched `SHA256SUMS` and an independent rebuild byte-for-byte; archive privacy scan passed.
- DONE: immutable GHCR digest `sha256:df00b90a300ed27e0758554c5c1e2d99b1bc97b5b98f2505057f1a5d05f5a736` contains linux/amd64 and linux/arm64 images.
- DONE: independent Cosign v3.0.6 verification passed for the exact tag workflow identity.
- DONE: both architecture attestations contain SPDX 2.3 SBOMs (122 packages) and SLSA provenance bound to the exact release commit.
- DONE: live source synchronized to the exact public tag; health/ready, SQLite continuity and persisted Telegram settings remained clean.


### 0.5.11-hw compact settings UX

- PASS: replaced the oversized Telegram `/settings` keyboard with five compact top-level sections and focused subpages.
- PASS: navigation edits one message and does not persist settings by itself.
- PASS: old `cfg:*` callback actions remain supported.
- PASS: reset is moved behind an explicit confirmation page.
- PASS: RU/UA/EN labels cover the new menu hierarchy.
- PASS: QUALITY gate and full regression suite: 187/187 tests.
- PASS: clean Python 3.11/3.12/3.13 matrix: 187/187 on each version.
- PASS: installer, rollback, support-bundle, reproducible release and Docker candidate gates.
- PASS: PR #34 merged as exact `main` commit `0f0b5d691f6e8e608d33442e3fe5f42321c62f7b`; PR CI #309, main CI #310 and Container Image #78 passed.
- PASS: exact-main live promotion through the standard installer created rollback snapshot `20261103-031423-0.5.10-hw`.
- PASS: live `/settings` renders exactly five top-level buttons using persisted UA settings; navigation was validated without sending a Telegram message.
- PASS: post-promotion and restart continuity: healthy/ready, Shelly online, Telegram failures 0, SQLite `quick_check=ok`, zero open outages and zero synthetic `0 V` rows; status ID and notification timestamps/settings remained unchanged.
- NEXT: publish `v0.5.11-hw` from promoted release metadata, then independently verify source and signed container artifacts.
- FUTURE: optional Telegram Mini App settings frontend after a public HTTPS endpoint is available and Telegram `initData` is validated server-side.

# Roadmap

Status date: 2026-11-04
Runtime release: `0.5.12-hw` (live; public release published)
Software candidate / production runtime: `0.5.12-hw`
Latest full hardware checkpoint: `0.5.6-hw`
Published public release: `0.5.12-hw` (source, Cosign and OCI attestations verified)


## Current next steps

- DONE: merge the persistent edit-window implementation (PR #22) after explicit user approval.
- DONE: confirm physical Pro EM-50 readings, Telegram diagnostics and SQLite integrity.
- DONE: confirm no open outages or synthetic zero-voltage rows; save a verified online backup.
- DONE: software validation with 158 unit tests and Python 3.11/3.12/3.13 CI.
- DONE: publish v0.5.7-hw from approved source 874fc45d81bed3c41ac0005fd801d4e9db291611.
- DONE: merge PR #25 for `0.5.8-hw`; CI and Container Image workflows passed on exact `main` source `b446604a8e84ddc1a35fa30d375a83f336608f0f`.
- DONE: publish `v0.5.8-hw` from exact release commit `14f85134167b09c697a41c47f17077d9ef0a77b9`.
- DONE: independently verify the published source archive, multi-architecture GHCR digest, Cosign signature, SPDX SBOMs and SLSA provenance.
- DONE: install the exact public release through `scripts/install-release.sh` on the live Pro EM-50 deployment; checksum, restart, SQLite and Telegram continuity checks passed.
- DONE: `0.5.9-hw` software/live gate — 15-minute stable-recovery rotation, 174 tests, Python 3.11/3.12/3.13, reproducible release, Docker candidate, exact-main deployment, rollback snapshot and restart continuity all passed.
- DONE: verify all 57 source files, archive checksums, amd64/arm64 images, SBOM, provenance and Cosign signature.
- DONE: prepare `0.5.9-hw` public release metadata after software/live validation.
- DONE: publish exact `v0.5.9-hw` tag on `c92d86006f17441e42a0a33d0b4b7e56f213aa82`; Source Release `41544388759`, Container Image `41544388518` and tag CI `41544388631` all passed.
- DONE: independently verify source archive SHA256 `b73e4e958de960031cd4cac2be1cef842cf5c56c4ddd492e88e053b952ed31dc`, byte-for-byte reproducibility, GHCR digest `sha256:eb563f8b8243b3edfefb4c977f2629adca41c35f4a40bd48bca45c231ce3f8bb`, Cosign v3.0.6 signature, amd64/arm64 images and SPDX/SLSA attestations.
- DONE: install exact public `v0.5.9-hw` through `scripts/install-release.sh`; checksum, exact-tag source parity, rollback snapshot, restart, SQLite and Telegram continuity all passed.
- DONE: `0.5.10-hw` extends the existing read-only recovery acceptance CLI with `stable-baseline` / `stable-verify` so the 15-minute recovery rotation can be physically accepted on the next natural event without controlling Shelly or mutating SQLite.
- DONE: live pre-`0.5.9-hw` recovery context migrated successfully; status rotated silently, recovery metadata cleared, audible cooldown unchanged, restart continuity and rollback snapshot integrity passed.
- DONE: publish `v0.5.10-hw` from release commit `aff36d905fabab29e75d01cf37fb8d7efa14b6c0`; Source Release #8, Container Image #77 and tag CI #307 passed.
- DONE: independently verify `0.5.10-hw` source SHA256 `4e0e39da5d2add3985a37fbbc6375c62bea49d2a626693002600eb3aeb873240`, reproducibility/privacy, GHCR digest `sha256:b1a1771c3c35bcb6cac4977773394e88b29b37800087cf58127dd266c986c880`, Cosign signature, amd64/arm64 SPDX 2.3 SBOM and SLSA provenance.
- DONE: `0.5.11-hw` compact settings software/live gate and restart continuity passed on physical Pro EM-50 without changing persisted Telegram settings.
- DONE: `v0.5.11-hw` public source and signed multi-architecture image independently verified.
- CONTINUE: observe normal operation without inducing power cuts or voltage changes.
- WAITING FOR HARDWARE: physical Pro 3EM / 3CT63 L1/L2/L3 acceptance.
- Full physical voltage-transition acceptance for the new edit-window behavior remains outstanding.

These current steps take precedence over older historical version notes below.
`RELEASE_VERSION=0.5.12-hw` is promoted as the next public release target; the previous verified release is `0.5.11-hw`.
`LIVE_VERSION=0.5.6-hw` remains the latest full physical acceptance checkpoint.

## Completed

### 1. Shelly hardware/API discovery — DONE

- Physical Shelly Pro EM-50 identified and queried locally.
- `EM1.GetStatus` validated on both channels.
- Voltage/frequency fields confirmed on hardware.
- Stable firmware 2.1.1 retained; beta firmware intentionally skipped.
- Device timezone corrected to Europe/Kyiv.

### 2. Core monitoring service — DONE

- Polling every 10 s by default.
- SQLite WAL history.
- Daily min/max/avg.
- Retention pruning.
- No fake `0 V` samples when Shelly is unavailable.
- Last valid data preserved.
- Digest authentication support.
- Monitor-only safety is regression-tested: production code permits the measurement
  `GET /rpc/EM1.GetStatus` path and contains no Shelly control/configuration RPCs.

### 3. Telegram implementation — DONE / LIVE ACCEPTED

Implemented:

- optional Telegram integration;
- one stable status message;
- `message_id` persisted in SQLite;
- `editMessageText` updates;
- automatic recreation after deleted/uneditable status message;
- `/voltage`, `/status`, `/today`, `/minmax`, `/history`, `/health`;
- optional threshold alerts, disabled by default;
- secret-file token/chat configuration.

Candidate `0.4.2-hw` additionally validates `/start`, `/help`, `/outages`,
persisted `getUpdates` offset, optional user-ID ACL, edit throttling, bounded 429
retry, and `message is not modified` handling.

Live Telegram acceptance is complete; current bot and status-channel destinations are validated by `doctor` and the live command/status flow.

### 4. Container/runtime hardening — DONE

- Debian 13 LXC 110 deployment.
- Docker/Compose runtime validated.
- non-root container.
- read-only root filesystem.
- capabilities dropped.
- no-new-privileges.
- PID and memory limits.
- Docker log rotation.
- persistent bind-mounted SQLite data.

### 5. Recovery/autostart — DONE

- LXC autostart.
- Docker autostart.
- Compose systemd unit.
- container restart persistence test.
- full LXC reboot recovery test.

### 6. Health/observability — DONE

- `/health`
- `/ready`
- `/status.json`
- `/metrics`
- runtime RPC latency
- SQLite integrity check
- `doctor`
- soak report CLI

### 7. Backup/supportability — DONE

- online SQLite backup;
- backup SHA256;
- daily persistent systemd timer;
- 14-copy default retention;
- redacted support bundle;
- bundle SHA256;
- secret redaction validation.

### 8. Installation UX — DONE / PUBLIC BOOTSTRAP ACCEPTED

- `install.sh --dry-run`;
- idempotent local install/update;
- secure interactive configuration;
- hidden Telegram token input;
- non-interactive mode;
- public `curl | bash` bootstrap, including clean Debian 13 dry-run validation;
- public `.env.example` separated from deployment `.env`.

## `0.4.2-hw` hardening and live acceptance

### `0.4.2-hw` — DEPLOYED / HARDWARE ACCEPTED

- Docker healthcheck now uses `/health`; `/ready` remains Shelly-data readiness.
- 3 consecutive poll failures -> `offline`; 2 successes -> recovery.
- last-good voltage/frequency restored from SQLite after process restart.
- no synthetic `0 V` samples during outage.
- repetitive outage warnings are rate-limited.
- persistent SQLite outage journal, schema version 2.
- schema v1 -> v2 migration preserves existing measurements.
- open outage survives service restart without creating a duplicate event.
- recovery closes the same event and records duration.
- optional one-shot Telegram outage/recovery alerts.
- outage count/duration metrics and `/outages` command.
- 45 unit tests pass; compile/style/Compose/ResourceWarning and supply-chain gates pass.
- isolated runtime simulation passed: offline -> restart while offline ->
  recovering -> online using a fake Shelly at 230.5 V / 50.0 Hz.
- installer acceptance uses `/health`, so a valid update does not fail merely because
  Shelly is offline; `/ready` remains a diagnostic data-readiness endpoint.
- pre-upgrade snapshots preserve source + online SQLite backup with SHA256.
- `scripts/rollback.sh` restores source/database, preserves `.env`/secrets, and
  creates a rescue backup of the state being rolled back.
- rollback fixture is part of CI and passed end-to-end.
- online backup against the live 2382-sample database passed SHA256 + quick_check.
- CI-equivalent release-candidate gate passed end-to-end on 2026-10-31.
- reproducible source-release fixture produces byte-identical tarballs for a fixed epoch,
  verifies internal/external SHA256 manifests, and excludes `.env`, secrets, SQLite,
  logs and pycache.
- release-hygiene regression now also excludes operational `validation/`, `dist-publish/`
  and `ROADMAP.md.pre-*` files; the final source-archive checksum is kept in the
  external `SHA256SUMS` release asset and validation evidence, avoiding a self-referential hash.
- schema-aware soak report remains compatible with schema v1 and separately reports
  diagnostic sample gaps, last-sample age and confirmed schema-v2 outage events.
- support bundle schema-v2 fixture validates outage diagnostics and secret redaction.
- `doctor` remains usable while Shelly is offline, so hardware unavailability does not
  suppress service/database/Telegram diagnostics.
- Telegram transport failures are sanitized so exception text cannot expose the bot
  token embedded in the Bot API URL.

Live acceptance on 2026-10-31 is complete for the monitor-only path: isolated real-Shelly smoke PASS; a live schema-v1 database copy migrated to schema v2 with all 3848 original measurement rows byte-stable by row hash; the production switch preserved all 3962 pre-switch rows; SQLite `quick_check=ok`, zero-voltage rows remain 0, and container restart persistence PASS. Telegram remained disabled because its secret files are empty.

## Active validation

### 9. Long-running hardware soak — DONE

24 h soak acceptance is complete. The database spans 26.947 h of real samples
(28.678 h wall time at acceptance), with 4538 measurements. While Shelly was
reachable, median cadence was 10.040 s and p95 cadence was 10.069 s. SQLite
`quick_check=ok`; zero-voltage samples remain 0. The report separately records
three >30 s diagnostic gaps and does not reinterpret them as outages.

A real mains blackout beginning at 2026-10-31 23:55:03+02:00 was observed during
acceptance. `0.4.2-hw` kept `/health` live, moved `/ready` to 503, retained the
last-good 227.7 V / 50.0 Hz sample, and opened exactly one schema-v2 outage event.
An online SQLite backup taken during the outage passed SHA256 and `quick_check`
with all 4538 measurement rows. Restarting the production container during the
same open outage preserved that single outage event (no duplicate), restored the
last-good sample, kept zero synthetic 0 V rows, and returned to `offline` state.

Soak statistics at acceptance:

- sample span: 26.947 h;
- wall elapsed: 28.678 h;
- samples: 4538;
- cadence median / p95 while sampling: 10.040 / 10.069 s;
- voltage min/max over retained history: 106.5 / 234.7 V;
- frequency min/max: 48.5 / 50.0 Hz;
- diagnostic gaps >30 s: 3; maximum 26610.197 s;
- confirmed outage journal: 1 open real blackout event;
- SQLite `quick_check`: `ok`;
- synthetic 0 V rows: 0.

The monitor remains intentionally online as a service while the physical Shelly is
off; data collection will resume automatically after power returns and two
successful polls confirm recovery.

## Offline-safe supply-chain audit — DONE

- GitHub workflows use explicit least-privilege permissions.
- CI uses `pull_request`, not privileged `pull_request_target`.
- release checkout is pinned to the exact release tag and verifies `VERSION`.
- source assets are checksum-verified and private/generated files are rejected.
- GHCR build emits SBOM and max provenance for amd64/arm64.
- runtime Python dependencies have upper bounds.
- bootstrap refuses the placeholder repository; pinned release installer verifies SHA256.
- workflow security regression tests are included in the normal unit-test suite.
- publication metadata/version consistency and operational-file exclusion are
  regression-tested; the frozen `0.4.2-hw` publication gate passed 45 tests plus
  installer, rollback, release reproducibility and support-bundle fixtures.
- final publication gate rebuilds the pinned container image to the exact
  hardware-accepted image ID and verifies source/image `app.py` identity; the only
  GitHub-publication input still missing is the destination `owner/repo`.
- All external GitHub Actions are pinned to full immutable commit SHAs; this is
  protected by the normal regression suite.

## `0.4.3-hw` hardening and live acceptance

- `version` CLI command no longer requires Shelly/runtime configuration.
- `db-check` no longer requires Shelly config and uses SQLite read-only mode;
  a missing database fails with exit 2 instead of being silently created/migrated.
- candidate work was isolated from the then-live `0.4.2-hw` until hardware promotion.
- full candidate software suite passes 49/49 tests plus compile/shell, Compose,
  reproducible release, installer, rollback and support-bundle fixtures.
- `0.4.3-hw-rc2` passes isolated blackout validation with no measurements or
  synthetic 0 V rows, one outage event and SQLite `quick_check=ok`.
- cold-start blackout readiness now reports the confirmed `shelly offline`
  state even before the first successful measurement exists.
- operational `db-check` against the live WAL database leaves the main SQLite
  file SHA256 and mtime unchanged; a read-only bind mount is not a supported
  WAL diagnostic mode because SQLite may require shared-memory access.
- after mains recovery, real-device smoke passed on the physical Shelly;
  `0.4.3-hw` was promoted to live CT110 with the existing `.env` preserved.
- pre-promotion snapshot `20261101-035941-0.4.2-hw` passed source/database
  SHA256 verification and rollback dry-run validation.
- post-promotion SQLite continuity passed: no synthetic 0 V rows, no duplicate
  outage, `quick_check=ok`, and sampling resumed normally after container restart.

## `0.4.4-hw` hardening and live acceptance

- Outage duration semantics are now explicit: confirmed connectivity interval
  after configured offline/recovery hysteresis, not exact mains-loss boundaries.
- Telegram `/outages`, Prometheus HELP and soak-report JSON use the same semantics.
- Software candidate version is separated from the hardware-accepted live version.
- Recovery regression asserts two valid recovery samples are persisted and the
  outage closes only on the second successful poll.
- Runtime version now has one source of truth (`VERSION`); the Docker image and
  `app.py version` can no longer silently retain a previous release string.
- Isolated real-Shelly smoke PASS before promotion.
- Safe promotion PASS with preserved `.env`, SQLite continuity, verified snapshot,
  rollback dry-run and post-promotion restart continuity.
- Live `0.4.4-hw` is now the hardware-accepted checkpoint.

## `0.4.5-hw` hardening and live acceptance

- Real restart evidence showed `last_outage_duration` became null although SQLite
  retained the latest closed outage.
- Candidate restores that historical duration from SQLite during startup without
  changing outage detection, hysteresis or schema.
- Regression coverage includes a newest closed outage followed by an open outage,
  preserving both the historical duration and current offline state.
- Isolated real-production-DB restart validation: PASS.
- Safe promotion, snapshot verification, rollback dry-run and production restart: PASS.
- Live `0.4.5-hw` is now the hardware-accepted checkpoint.

## `0.4.6-hw` hardening and live acceptance

- Clean Debian 13 release gate exposed a first-install dry-run failure caused by
  bare `return` inheriting status 1 from a failed no-op condition under `set -e`.
- Candidate makes expected snapshot/rollback no-op paths return 0 explicitly.
- CI now covers both a fresh target and in-place installer dry-run.
- Clean Debian 13 first-install acceptance: PASS.
- Isolated real-Shelly smoke: PASS.
- Safe promotion, snapshot verification, rollback dry-run and production restart: PASS.
- Live `0.4.6-hw` is now the hardware-accepted checkpoint.

## `0.4.7-hw` supportability hardening and live acceptance

- `soak-report.py` now opens the requested SQLite database read-only.
- A missing or mistyped `--db` path fails closed with exit 2 and a concise diagnostic instead of creating an empty SQLite file or emitting a traceback.
- 52/52 Python regression tests pass; release, installer, rollback and support-bundle fixtures pass.
- Live SQLite read-only validation preserves SHA256 and mtime; missing DB paths fail closed without creating files.
- Isolated real-Shelly probe PASS before promotion.
- Safe promotion from `0.4.6-hw` created and checksum-verified the rollback snapshot.
- Post-promotion continuity and container restart PASS: no synthetic 0 V rows, two outage rows preserved, `quick_check=ok`.
- Live `0.4.7-hw` is now the hardware-accepted checkpoint.

## `0.4.8-hw` Telegram hardening and live acceptance

- Preserve the live long-poll fix: `getUpdates` uses a 20 s Bot API long poll with 25 s HTTP timeout headroom.
- Add `telegram-discover`, which validates the configured bot token and lists pending chats without printing secret material.
- Discovery works before a chat ID is configured and reports `waiting_for_update` cleanly when no pending update exists.
- Software gate passes 55/55 tests plus syntax, Compose, installer, reproducible release, rollback and support-bundle fixtures.
- Isolated real-Shelly probe PASS before promotion.
- Safe promotion from `0.4.7-hw` created a rollback snapshot and preserved SQLite continuity.
- Telegram `doctor` validates both bot identity and destination chat.
- Stable status message edit-in-place PASS with the persisted message ID unchanged.
- Deleted status message recreation PASS: one replacement message was created and its new ID persisted.
- Live `/voltage` command handler response PASS in the configured chat; all expected bot commands are registered.
- Sustained live command-loop check reports zero `ReadTimeout`, transport or polling failures.
- Post-promotion service remains healthy and ready; zero synthetic 0 V rows, two outage rows preserved, `quick_check=ok`.
- Live `0.4.8-hw` is now the hardware-accepted checkpoint.

## `0.4.9-hw` Telegram notification-policy acceptance

Requested notification behavior:

- ordinary Telegram messages: silent;
- automatic low-voltage alert below 180 V: normal notification outside quiet hours;
- 23:00-07:00 Europe/Kyiv: all Telegram messages forced silent;
- low-voltage recovery threshold: 185 V, recovery message silent;
- one alert per low-voltage episode, not every poll.

Implementation is configurable through `.env`; boundary/regression tests pass and the candidate was promoted to live CT110 with Telegram/Shelly doctor checks passing.

Acceptance evidence:

- 60/60 Python tests PASS;
- installer dry-run, reproducible release, rollback and support-bundle redaction PASS;
- live `.env` explicitly sets silent-by-default, 23:00-07:00 quiet hours, 180 V alert and 185 V recovery;
- Telegram and Shelly doctor checks PASS after promotion;
- persisted Telegram status `message_id` and update offset retained;
- no Telegram warnings after restart;
- pre-upgrade snapshot: `20261101-182218-0.4.8-hw`.

## `0.4.10-hw` admin Telegram settings acceptance

- `/settings` displays the live Telegram notification policy.
- Inline edit controls are shown only to IDs in `TELEGRAM_ADMIN_USER_IDS`.
- Callback writes are rejected for non-admin users.
- Settings are persisted in SQLite `meta` and loaded immediately, so no container restart is required.
- Controls cover silent default, quiet-hours enable/start/end, low-voltage alert enable/threshold/recovery and reset to `.env`.
- Promoted to live CT110 after all software gates passed. Admin configuration, health, Shelly/Telegram doctor, command registration source and existing Telegram state were verified. Interactive callback acceptance is available through `/settings`.

## `0.4.11-hw` separate Telegram status-channel acceptance

- Keep private control chat for commands and admin `/settings`.
- Publish/edit the stable voltage status in a dedicated Telegram channel.
- Persist channel status message independently as `telegram_status_message_id`.
- Doctor validates both destinations.
- Live target channel verified by Bot API before candidate promotion.
- Promoted to live CT110. Bot API validated the channel destination and the persisted channel status remained stable across subsequent update cycles, confirming in-place editing. Private control chat and admin `/settings` remain separate.

## `0.5.0-hw` three-phase + full feature candidate — SOFTWARE ACCEPTED / HARDWARE WAIT

Implementation completed in isolated candidate while production remains `0.4.11-hw` during the real mains outage.

Implemented:

- schema v3 with lossless v1/v2 → L1 migration;
- L1/L2/L3 runtime model; L2/L3 optional until hardware arrives;
- independent phase history/outage/polling/alerts;
- three-phase status channel, dashboard, health JSON and Prometheus metrics;
- warning/critical and <180 V alarm dwell/recovery hysteresis;
- recovery/outage notifications;
- daily summary and voltage-quality analytics;
- PNG `/chart`, CSV `/export`, outage history;
- admin maintenance mode and audit journal;
- backup freshness, watchdog counters and SQLite maintenance/rollups;
- read-only device firmware/uptime/time-skew health;
- optional channel status pinning.

Migration proof on a copy of the live database: 7543 measurements + 3 outages before and after migration, all assigned to L1, `PRAGMA quick_check=ok`.

Software/package gates are complete. Do not promote during the blackout.

Software acceptance checkpoint (final):

- 85/85 Python tests PASS;
- every function in `app.py` and `advanced_features.py` is <=40 lines;
- fresh/in-place installer dry-run PASS;
- reproducible release PASS; rollback PASS; support-bundle redaction PASS; Compose config PASS;
- final candidate image runs as UID 10001 with read-only root;
- isolated HTTP smoke PASS for `/health`, `/metrics`, `/dashboard`;
- schema-v3 migration on production DB copy PASS, row counts preserved, zero synthetic 0V rows, `quick_check=ok`;
- Telegram doctor PASS for private control chat + status channel; bot is channel administrator with post/edit rights;
- production `0.4.11-hw` remains unchanged and healthy during the real mains outage.

Promotion gate: wait for L1 mains to return, run real Shelly hardware acceptance, then promote `0.5.0-hw`. L2/L3 software slots are ready; their hardware acceptance naturally waits for the two future sensors.

## `0.5.1-hw` Pro 3EM preparation — SOFTWARE CANDIDATE

- Native Pro 3EM-3CT63 triphase polling through `EM.GetStatus?id=0`.
- A/B/C mapped to L1/L2/L3 with one polling timestamp.
- Missing/null phase values never become fake 0 V samples.
- Legacy Pro EM-50 and optional separate L2/L3 sensor mode retained.
- Confirmed power recovery rotates the stable channel status: delete old outage-era message → create/persist fresh online message.
- Production recovery behavior is already backported to live `0.4.12-hw` so the next mains restoration is covered before Pro 3EM hardware arrives.

Hardware validation for Pro 3EM naturally waits for the device purchase/installation.

## `0.5.3-hw` Telegram module selector — SOFTWARE CANDIDATE

- Admin `/settings` shows the active Shelly module.
- Admin may select `Pro EM-50` or `Pro 3EM / Pro 3EM-3CT63`.
- Selection is persisted in SQLite and applied immediately by rebuilding read-only clients/state.
- IP addresses and credentials remain in protected configuration, never editable through Telegram.
- Persisted runtime settings are now loaded automatically after every service restart.


## Next external-input stages

### 10. Telegram live acceptance — DONE

Live acceptance completed on CT110 with the configured private destination:

1. `doctor` validates `getMe` and `getChat` — PASS.
2. Stable status message exists and its `message_id` is persisted — PASS.
3. Subsequent status update edits the same message in place — PASS.
4. Telegram update offset persistence remains present — PASS.
5. Live command response and registered command set — PASS.
6. Deleting the status message causes exactly one replacement status message to be created and remembered — PASS.
7. Long-poll command loop runs without `ReadTimeout` or transport warnings after the timeout-headroom fix — PASS.

No bot token or private chat identifier is stored in public release artifacts or documentation.

### 11. GitHub publication — DONE

- Public repository: `iSentinel/shelly-voltage-telegram`.
- Multilingual README pages and the live dashboard image are published.
- Release `v0.5.4-hw` is published with source archive and `SHA256SUMS`.
- CI, container image and source-release workflows are active.
- Public release excludes `.env`, secrets, SQLite and local validation data.
- The one-line installer points to the real public repository.
- Public bootstrap dry-run was validated on a clean Debian 13 container.
## Optional hardening after acceptance

- Shelly Digest-auth software path is regression-tested. Live auth validation is
  deferred until the operator independently enables authentication on the physical
  Shelly; this monitor-only project does not change device authentication settings.
- Add a second Shelly device only if required later.
- Add Grafana/Prometheus integration only if useful; current metrics endpoint is already compatible with scraping.
- Signed container releases are implemented and independently verified in `0.5.5-hw` using keyless Sigstore Cosign signatures over immutable GHCR digests.


## `0.5.4-hw` maintainability audit — RELEASED / HARDWARE ACCEPTED

- Production is running hardware-accepted `0.5.4-hw` after the audited
  release was merged and promoted through the normal installer path.
- PEP 8/Ruff checks pass with no source lines over 79 characters.
- All Python functions, including tests, are limited to 40 lines.
- Production modules/classes/functions have clear docstrings; application logging
  remains on `loguru`; no standard-library `logging` imports remain.
- Exact duplicate function bodies are rejected by the audit gate.
- Legacy `SHELLY_METER_TYPE` migration is covered for missing and blank values.
- Multilingual GitHub documentation and a real dashboard image are maintained as
  part of the public release tree.
- Isolated candidate runtime against a physical Pro EM-50 passed with schema v3,
  valid live samples and no synthetic `0 V` measurements.


### Next stage after `0.5.4-hw`

- Keep the current Pro EM-50 deployment under normal observation.
- Perform physical three-phase acceptance when the planned Pro 3EM / Pro 3EM-3CT63 is installed.
- Validate L1/L2/L3 mapping, outage behavior, Telegram presentation, charts,
  exports and metrics with real three-phase data before enabling that backend in
  production.


## `0.5.5-hw` signed container release — RELEASED / SUPPLY-CHAIN ACCEPTED

- Keep production on hardware-accepted `0.5.4-hw`; `0.5.5-hw` changes the public release supply chain, not runtime monitoring behavior.
- Versioned GHCR images install Cosign through an official action pinned to a full commit SHA.
- Release images are signed keylessly through GitHub OIDC; no long-lived signing key is stored in repository secrets.
- The signature targets the immutable image digest returned by the multi-architecture build.
- Ordinary `main` images remain unsigned; only versioned release images receive the release signature.
- Regression tests enforce OIDC permission, pinned action SHA, digest-based signing and release-only conditions.
- Acceptance completed: GitHub Actions run `41373145505` built and keyless-signed RC2 digest `sha256:df534c7b9b30e2a8f3dfc617b07a0ff6837ecc15b285331dc13e211130dcca4d`; independent Gateway `cosign verify` passed with the expected GitHub Actions certificate identity and OIDC issuer.
- Final `v0.5.5-hw` publication completed: Source Release run `41373981210` and signed Container Image run `41373982691` succeeded from exact tag source `f669dbb628854ea6bdbedd61d3b505fe613c93bf`; final image digest is `sha256:c2115def9975dcfbb4a722d08713450403f53beaa01e6c54fec75962d82ce47a`, independently verified through Gateway.


## `0.5.6-hw` Pro EM-50 live acceptance + Pro 3EM tooling — HARDWARE ACCEPTED

Current hardware gates:

- **Current Pro EM-50 deployment:** recovery acceptance, read-only hardware probe, Telegram continuity and post-promotion soak all PASS; `0.5.6-hw` is live and hardware accepted.
- **Future Pro 3EM / 3CT63 backend:** keep disabled in production until the physical three-phase meter is installed and the dedicated acceptance CLI plus L1/L2/L3 UI/data checks pass.

- Separate candidate/source, public release and live hardware checkpoint with `VERSION=0.5.6-hw`, `RELEASE_VERSION=0.5.5-hw` and `LIVE_VERSION=0.5.6-hw`.
- Final release publication is fail-closed until `RELEASE_VERSION` is explicitly promoted to match `VERSION`.
- Add standalone read-only `scripts/pro3em-acceptance.py` for physical Pro 3EM / Pro 3EM-3CT63 acceptance.
- Reuse the existing Digest-auth `Pro3EMClient`; the acceptance path issues only `EM.GetStatus?id=0` measurement RPCs.
- Require complete L1/L2/L3 data from one atomic RPC cycle and reject missing phases, zero/implausible values and RPC failures.
- Provide repeated sampling, configurable bounds, text/JSON output and stable PASS/FAIL exit codes.
- Route low-voltage/recovery transitions for L1/L2/L3 into the stable Telegram status header, including phase (for L2/L3), event time, measured voltage and frequency; do not send duplicate private messages.
- Add read-only `scripts/recovery-acceptance.py` with `baseline` and `verify` modes for real blackout/recovery acceptance. It checks the same outage closes, the stable Telegram status rotates, `/ready` becomes healthy, valid samples increase and synthetic `0 V` remains absent.
- Recovery verification matches the exact captured outage by ID, phase and start time, and verification/promotion JSON redacts raw connection error text and Telegram status message IDs.
- New recovery baselines persist only a status-message fingerprint instead of the raw Telegram `message_id`; legacy baseline files remain compatible.
- Add read-only `scripts/promotion-check.py` as the single pre-promotion gate. It reuses recovery verification, requires the live service version to match `LIVE_VERSION`, and fails closed unless the active backend remains `pro_em50`.
- Physical three-phase acceptance remains pending until the Pro 3EM / 3CT63 is installed; this blocks only switching production to the `pro3em` backend, not promotion of the 0.5.6 software on the current Pro EM-50.


Software acceptance checkpoint:

- Ruff 0.14.2 formatting/lint and project quality rules PASS.
- Local and GitHub quality checks now use the same pinned `scripts/quality.sh` gate, preventing format/lint drift.
- 138/138 Python tests PASS with the ResourceWarning gate enabled.
- `py_compile`, shell syntax, installer dry-run, rollback, support-bundle
  redaction, pinned-release installer and Compose validation PASS.
- Reproducible source release PASS and excludes Python tool caches.
- Candidate container build/version smoke PASS.
- Prometheus current voltage/frequency fail stale-safe: `starting`/`offline` export `NaN`; `recovering`/`online` export only the newest valid sample.
- Local dashboard is stale-safe too: `starting`/`offline` phase cards hide voltage, while `recovering`/`online` show only the newest valid sample.
- Telegram stable status is stale-safe: offline phases expose no V/Hz values; `recovering` shows the newly received sample with an explicit recovery state.
- Low-voltage/recovery status-header persistence and destination tests PASS; no duplicate private message is sent for the primary phase.
- Read-only physical Pro EM-50 probe PASS on the current installation.
- Real blackout-soak acceptance PASS: the active outage remains a single record, no measurements are inserted while offline, stable Telegram status is not rotated and synthetic `0 V` remains absent.
- Pro 3EM acceptance CLI end-to-end HTTP smoke PASS for three atomic
  L1/L2/L3 cycles.
- Promotion on the current Pro EM-50 PASS: the captured outage closed, recovery baseline verified, read-only hardware probe passed, and Telegram/status continuity passed before and after promotion.
- Switching production to the `pro3em` backend remains blocked until the real Pro 3EM / 3CT63 is installed and L1/L2/L3 mapping, outage behavior, Telegram presentation, charts, exports and metrics pass on physical three-phase data.

### 2026-11-02 blackout operational checkpoint (historical pre-recovery)

- Shared quality gate PASS and 138/138 Python tests PASS.
- Installer, rollback, support-bundle and reproducible-release fixtures PASS.
- Live promotion gate fails closed while outage 4 remains open.
- Active recovery baseline now stores only a Telegram status-message fingerprint;
  no raw Telegram message ID is stored in the refreshed baseline.
- Compose service and backup timer are enabled and active; runtime container is
  healthy with zero restarts.
- Latest backup checksum and SQLite quick_check PASS; live SQLite quick_check PASS.
- Backup and live history contain zero synthetic 0 V measurements.
- Promotion remains blocked only by real Shelly recovery; the Pro 3EM backend
  remains separately blocked until physical three-phase hardware is installed.

## `0.5.6-hw` shared voltage-status notification cooldown

- Low/high warning and critical transitions no longer create separate Telegram
  alert messages; they replace the shared status message.
- Replacement is publish-first/delete-old so a publish failure keeps the
  previous status visible.
- First abnormal status may notify normally; subsequent abnormal replacements
  are silent for the configured cooldown. Recovery is always silent.
- Cooldown is measured from the last loud voltage status and silent updates do
  not extend it.
- Runtime choices: Off / 3 / 5 / 10 minutes; default 5 minutes.
- `/settings` persists the selection in SQLite and applies it immediately.
- Quiet hours remain authoritative and force silence.
- Software validation: quality gate PASS, 138/138 Python tests PASS.
- Hardware promotion PASS after outage #4 closed and recovery verification passed.

### 2026-11-02 `0.5.6-hw` live hardware acceptance

- Outage #4 closed on the physical Pro EM-50 and the captured recovery baseline
  verified PASS.
- `promotion-check.py --json` PASS with `pro_em50` and read-only guard active.
- Production promoted from `0.5.4-hw` to `0.5.6-hw`; rollback snapshot
  `20261102-212042-0.5.4-hw` source and SQLite checksums PASS.
- Post-promotion `doctor` PASS for Shelly, SQLite and Telegram.
- Container healthy/ready with physical voltage sampling resumed.
- SQLite continuity PASS: four closed outages, zero open outages, quick_check=ok,
  zero synthetic 0 V rows, measurement history increasing.
- Telegram shared status continuity PASS across promotion and restart; the persisted identifier remained stable without being exposed in release documentation.
- Runtime cooldown default is 5 minutes; `/settings` exposes Off/3/5/10.
- Restart continuity PASS with the same status ID and no duplicate outage.
- `LIVE_VERSION` promoted to `0.5.6-hw`; `RELEASE_VERSION` remains `0.5.5-hw`
  until public release publication completes.
- Pro 3EM production backend remains blocked until physical three-phase hardware
  acceptance is performed.


### 2026-11-02 post-promotion live checkpoint

- Outage 4 recovery verification PASS with the same captured outage identity.
- Read-only physical Pro EM-50 probe PASS before promotion.
- Safe installer promotion from `0.5.4-hw` to `0.5.6-hw` PASS with a verified
  pre-upgrade source + SQLite rollback snapshot.
- `VERSION=0.5.6-hw` and `LIVE_VERSION=0.5.6-hw`; public
  `RELEASE_VERSION=0.5.5-hw` remains intentionally separate.
- Level alerts are enabled on the live deployment and voltage notification
  cooldown defaults to 5 minutes.
- Post-promotion container health PASS with zero restarts, `/ready` PASS,
  Telegram failures zero and no warning/error logs in the acceptance window.
- SQLite `quick_check` PASS; outage count remains four with no open duplicate;
  synthetic `0 V` rows remain zero.
- Public `0.5.6-hw` release is the next release stage. Physical Pro 3EM /
  3CT63 acceptance remains blocked until that hardware is installed.

### 2026-11-02 `0.5.7-hw` Telegram status lifecycle

- PASS: old shared status is unpinned before deletion.
- PASS: status pinning defaults to OFF and remains configurable in `/settings`.
- PASS: public status inline buttons are removed, including startup cleanup for the current status.
- PASS: voltage alert/recovery transitions use one editable message for 3/5/10 minutes; default is 10 minutes.
- PASS: recovery keeps the original deadline; repeated transitions reuse the post.
- PASS: quality gate and 158/158 tests passed before deployment.
- PASS: corrected source deployed; container healthy, readiness true, status ID preserved, SQLite healthy and new measurements collected.

### 2026-11-02 operational continuation after PR #22

- Physical Pro EM-50 diagnostics, SQLite diagnostics and Telegram diagnostics PASS.
- Runtime container healthy and ready; monitor-only measurement polling continues.
- At the recorded checkpoint: 11,103 measurements, four closed outages,
  no open outages and no synthetic zero-voltage samples.
- Recent nine-minute observation: 53 samples, no diagnostic gaps above 30 seconds,
  maximum sample interval 10.259 seconds, voltage 212.6–219.1 V.
- Fresh online backup SHA256 and SQLite quick_check PASS.
- Runtime source matches the implementation merged in PR #22.
- Full hardware checkpoint remains 0.5.6-hw; no electrical transition was induced.
### 0.5.8-hw Telegram usability stage

- PASS: finalized event summary after the fixed edit window.
- PASS: persistent RU / UA / EN Telegram UI.
- PASS: administrator loud / silent / edit notification tests.
- PASS: restart-persistent unstable-grid indicator (4 transitions / 15 min).
- PASS: QUALITY GATE, 168/168 tests, installer/rollback/support fixtures, reproducible release archive, container build/import and live Pro EM-50 smoke.
- PASS: restart continuity; Telegram status message ID preserved; SQLite quick_check OK; no new outage and no synthetic `0 V`.
- PASS: live Telegram silent/edit diagnostics; real alert sound cooldown remained unchanged.
- SECURITY: protect `main` with required checks `test (3.11)`, `test (3.12)`, `test (3.13)` and `build`, block force-push/deletion, and require PRs. Application is pending GitHub repository Administration permission; the connected integration currently receives HTTP 403 on branch-protection APIs.
- NEXT: publish candidate PR and observe a natural threshold/recovery transition for hardware acceptance.
- BLOCKED separately: physical Pro 3EM / 3CT63 acceptance until hardware is installed.

