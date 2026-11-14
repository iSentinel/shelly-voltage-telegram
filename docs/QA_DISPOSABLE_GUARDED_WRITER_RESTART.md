# Disposable guarded Storage writer restart QA

**QA-only. Issue #340. Do not use on production systems.**

## Why a guarded systemd restart is being tested

A real isolated Debian 13 LXC114 was created with no network
interface, bridge, route, shared production mount, secret or
Telegram API access. Its Docker/Compose stack runs the exact
previously verified v0.5.21-hw immutable OCI image but replaces
the normal app command with a simple synthetic app.Storage writer
writing only invented voltage samples.

Actual guest cold-boot acceptance revealed that failing the
Compose systemd ExecStartPre during a pending update does **not**
prevent Docker from restarting a container independently.

- Docker and Compose restart=unless-stopped: pending prestart
  refused, but Docker launched the writer and synthetic SQLite
  grew 147->231 rows; app starts 2->3.
- Docker and Compose restart=on-failure:3: a genuine SIGKILL of
  the test writer process recovered via Docker at runtime (starts
  5->6), but another pending guest cold boot still launched
  the writer despite failed systemd prestart, 562->601 rows
  and app starts 6->7. On this tested environment, on-failure
  is **not** a substitute for boot ownership.
- Docker and Compose restart=no: a pending guest cold boot left
  the writer stopped; all 234 synthetic rows, all 3 starts and
  the full SQLite logical hash were unchanged. Docker daemon
  remained active while systemd prestart failed as expected.

Therefore a reliable restart after a process crash requires
a separate *guarded* mechanism under the single systemd boot
owner, rather than re-enabling Docker independent auto-restart.

## QA helper safety restrictions

The script is intentionally tied to the disposable guest
hostname shelly-docker-reboot-qa and the single fixed container
svt-qa-signed-writer-114. It cannot accept a custom container,
Compose file or service name.

Before any restart it verifies:

1. Exact guest hostname and only the loopback interface in the
   current network namespace.
2. Exact local signed image ID
   sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b.
3. Docker restart=no and network=none.
4. Exact enabled systemd QA service with its expected
   ExecStartPre and fixed Compose manifest path.
5. Single Compose service with restart=no, local immutable
   QA-only image tag, pull_policy=never, read_only=true,
   non-root UID 10001, capabilities dropped and
   no-new-privileges.
6. Exactly one writable private bind mount to the synthetic
   data directory and no extra volumes, devices, secrets,
   published ports or unapproved environment variables.
7. The synthetic app.Storage-only Python command, not the
   normal Telegram bot command.
8. Existing synthetic SQLite quick_check=ok and
   meta.schema_version=3, opened read-only.
9. No active pending-update marker. A second pending check
   occurs immediately before delegating restart to systemd,
   and the systemd ExecStartPre checks again.
10. If writer is already running without pending, it returns
    SKIP_ALREADY_RUNNING without opening the actively written SQLite
    (a sandboxed read may fail on WAL sidecars). If pending exists
    while the writer is running, it returns an explicit BLOCKED
    PENDING_BUT_QA_WRITER_RUNNING error. A stopped writer with pending
    returns SKIP_PENDING without opening SQLite. Only an otherwise
    eligible stopped writer has its SQLite integrity checked.
11. The only permitted restart action is a bounded systemctl
    restart of the fixed synthetic QA unit.

No user input can select a production container or service.
The tool does not call Docker start, stop, update, pull or build,
run migrations, change public Telegram messages or read the
real production database.

## Usage and exit codes

Inert default:

    python3 scripts/disposable-guarded-writer-restart-qa.py --dry-run

Explicit disposable QA-only action:

    python3 scripts/disposable-guarded-writer-restart-qa.py \
      --run-disposable \
      --confirm DISPOSABLE_QA114_GUARDED_RESTART_ONLY

This requires the exact disposable LXC114 and its verified
synthetic configuration. Use -h/--help for usage.

Exit 0 indicates a QA-only observation or successful delegated
QA action; 2 means confirmation missing and 3 means a blocked
or failed QA action. Every successful JSON report explicitly
states production_modified=false, boot_start_authorized=false,
source_install_authorized=false, telegram_photo_authorized=false
and all_restart_owners_tested=false.

## Real acceptance, 2026-11-14

- Synthetic pending active, writer stopped: guarded helper
  returned SKIP_PENDING and did not start anything.
- After only the fake pending was archived, an explicit
  guarded call delegated restart to systemd; writer start
  count became 8. Repeating the helper returned
  SKIP_ALREADY_RUNNING with no duplicate writer.
- Actual SIGKILL of the verified synthetic writer PID
  while Docker restart=no: Docker left the process stopped.
  Guarded helper then used the fixed systemd unit to
  restore the writer, start count 8->9, old SQLite history
  and quick_check preserved.
- Artificial pending created while running; the verified
  writer was SIGKILLed; Docker did not restart it, the
  helper returned SKIP_PENDING and a full actual LXC114
  cold boot also left the writer stopped. **716/716**
  synthetic rows and complete logical SQLite SHA256
  77239a6f79dcbdb86f90cb9ed122c870839803c46fe9df16f99418e8f4e891dd
  remained identical, starts stayed 9 and quick_check=ok.
- Hardened security checks validated again inside LXC114:
  SKIP_PENDING, then after archiving only the synthetic
  marker QA_SYSTEMD_RESTARTED (starts->10), followed by
  SKIP_ALREADY_RUNNING.
- Final QA cleanup: writer was stopped, synthetic Compose
  unit disabled, Docker restart=no, LXC114 remains offline
  with loopback only. Final synthetic SQLite **727 rows**,
  quick_check=ok. All pending diagnostic QA markers retained
  under private filenames.
- 32/32 focused mocked unit tests PASS; full project Ruff
  quality, source archive check and functions <=40 lines PASS.

## Not a production recovery implementation

This is an explicit, manually invoked QA helper, **not** a
scheduled watchdog/timer. No production updater timer, network
service, Telegram credential, message pin or actual long-running
bot behavior has been exercised. No physical host power-loss
storage durability has been demonstrated.

The live host source remains v0.5.15-hw while its signed
runtime image is v0.5.21-hw. Production Docker and Compose
still have independent unless-stopped restart policies and
a real all-owner writer fence is blocked. The historical
03:44 Kyiv backup is stale. Production cannot be changed
until independent design review, fresh consistent whole-current
SQLite+source A/B backups, explicit owner approval and a
controlled maintenance/rollback plan. Boot continuation and
Telegram TEXT-to-PHOTO remain NO-GO.

## Real recurring systemd timer follow-up

A separate only-in-LXC114 QA systemd timer/service repeatedly invoked
the explicit guarded script. Its first service sandbox required
AF_NETLINK in RestrictAddressFamilies because the guard inspects
the current network namespace. After adding it, pending produced
SKIP_PENDING across repeated timer ticks, and the timer restarted the
stopped writer once after removing only the fake pending marker.
A subsequent tick against a healthy, running WAL writer failed with
a read-only SQLite sidecar error under ProtectSystem=strict. This
revision now short-circuits healthy running writers and stopped
pending writers before opening SQLite, while explicitly detecting
the illegal running+pending state. Repeatable timer acceptance must
pass before this is accepted as a usable recurring QA supervisor.
No timer is installed or enabled by this PR.
