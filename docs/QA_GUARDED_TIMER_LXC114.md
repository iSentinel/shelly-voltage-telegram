# Networkless LXC114 guarded systemd timer acceptance

**QA ONLY — depends on PR #348. Never install these units on the
production LXC110 or a host with a network interface.**

This QA kit ships a bounded, deliberately fast timer and a oneshot
service. It reuses the exact fixed-target, inert-by-default guarded
restart helper from scripts/disposable-guarded-writer-restart-qa.py
(PR #348). Its only possible restart target is the synthetic
svt-qa-compose.service inside unprivileged, loopback-only LXC114.

The timer has deliberately short 4/6-second intervals for QA. These
timings are NOT appropriate for production. There is no installer,
enabled service, or background work in this PR.

## Required disposable state

- LXC114 hostname shelly-docker-reboot-qa with only lo, no
  IPv4 route, no other network interface, no external bind mounts.
- Image already imported offline and pinned by its exact image ID
  sha256:59d8b7c6d7c672958893392dcf09c40da96d664e1b26d6364551e69bcf3a883b.
- QA Docker container svt-qa-signed-writer-114, image CMD overridden
  with invented app.Storage samples, network=none, restart=no.
- QA Compose manifest at /var/lib/svt-qa-docker114/compose.yaml
  also sets restart=no, only one private writable synthetic SQLite bind,
  no Telegram token, no ports, no network and no added secrets.
- svt-qa-compose.service includes synthetic pending-update
  ExecStartPre and is the sole owner permitted to start the writer.
- QA helper copied from PR #348 exactly to
  /var/lib/svt-qa-docker114/guarded-restart.py, root-owned, mode 0700.
- The Docker writer is stopped and the QA units disabled before setup.

## Explicit setup inside disposable LXC114 only

Inspect the files before using them. Copy these two QA files to:

    /etc/systemd/system/svt-qa-guarded-watchdog.service
    /etc/systemd/system/svt-qa-guarded-watchdog.timer

Then in LXC114:

    systemctl daemon-reload
    systemd-analyze verify /etc/systemd/system/svt-qa-guarded-watchdog.service /etc/systemd/system/svt-qa-guarded-watchdog.timer

For a fake pending fixture, create ONLY this test marker:

    printf 'QA_ONLY_PENDING\n' > /var/lib/svt-qa-docker114/state/pending-update
    systemctl enable svt-qa-compose.service
    systemctl enable --now svt-qa-guarded-watchdog.timer

Observe the QA service journals with
journalctl -u svt-qa-guarded-watchdog.service. While pending exists
and writer is stopped, every timer tick must return SKIP_PENDING.
It must never start the writer.

Only after archiving the fake pending marker, the next timer tick
may delegate one start via synthetic systemd Compose. It must return
QA_SYSTEMD_RESTARTED once, then SKIP_ALREADY_RUNNING on subsequent
ticks, without reopening live WAL via SQLite.

With pending created while writer is running, the guard explicitly
returns BLOCKED PENDING_BUT_QA_WRITER_RUNNING, not a false success.
With Docker restart=no, SIGKILL of a provenance-checked QA-only writer
PID must leave it stopped. The timer must then return SKIP_PENDING
without restarting. This is a test, not a real power-loss event.

For a true disposable cold boot, use pct stop 114, then pct start 114
from Proxmox, with fake pending already created. Compose prestart must
fail; the timer must remain active but skip writer start. Compare the
complete SQLite logical history and app start count before/after boot.

## Actual Gateway acceptance, 2026-11-14

The above was run only inside LXC114 against real systemd, Docker,
Compose and the signed image's synthetic app.Storage writer.

- First sandbox used AF_UNIX alone, which blocked interface inspection.
  AF_NETLINK was added to permit reading the current namespace.
- Before updating PR #348, a healthy running writer triggered a
  read-only SQLite/WAL sidecar error under ProtectSystem=strict.
  The revised helper checks pending/running state first. Unit tests
  increased from 32 to 36/36 PASS and whole-project Ruff PASS.
- With the revised script, repeated pending timer ticks returned
  SKIP_PENDING. After the synthetic marker was archived, the timer
  started one writer (app starts 11->12); next tick returned
  SKIP_ALREADY_RUNNING, with service status SUCCESS.
- New fake pending while writer ran returned explicit BLOCKED.
  After SIGKILL of the proven LXC114 QA process, Docker policy=no
  and subsequent timer ticks returned SKIP_PENDING.
- Real guest cold boot with both synthetic Compose and QA timer
  enabled left Docker active, Compose failed on pending, writer
  stopped, and 845/845 synthetic SQLite rows plus full logical
  SHA-256 identical, app starts 12/12, quick_check=ok.
- Cleanup: both QA timer and QA Compose disabled after acceptance;
  QA writer stopped, Docker restart=no, only loopback, no routes.
  Production Shelly/Telegram/SQLite/Compose untouched.

## Tear down — mandatory after acceptance

In LXC114:

    systemctl disable --now svt-qa-guarded-watchdog.timer
    systemctl stop svt-qa-compose.service
    systemctl disable svt-qa-compose.service

Verify stopped QA writer and Docker restart=no. Preserve private
synthetic QA history and archived fake markers for regression review.
Never substitute production source paths, container names or secrets.

Passing a disposable test does not authorize installing these units
in production. The real updater apply path, watchdog start owners,
true power-loss storage durability, exact signed host-source parity
and fresh owner-approved fenced source+full-current SQLite A/B
are separate blocked gates. Telegram TEXT-to-PHOTO is still NO_GO.

## Additional QA-only maintenance lock limitation

A later isolated LXC114 timer race proved that pending-only
checks could relaunch the synthetic writer during a pre-pending
exclusive maintenance lock. PR #350 adds a separate private
root-owned 0600 read-only shared-lock guard to this QA-only
supervisor. It keeps a shared lock across the delegated start;
exclusive simulated updater ownership returns SKIP_MAINTENANCE_LOCKED.
This requires precreating the private QA lock file in LXC114.

The actual production updater lock and real boot recovery must
NOT be changed based on this isolated test. Using the **same**
lock inode for a shared-lock supervisor held through systemctl
restart and an exclusive-lock ExecStartPre can DEADLOCK.
The production lock topology needs independent owner/security
review before ANY production watchdog, source install or PHOTO.
