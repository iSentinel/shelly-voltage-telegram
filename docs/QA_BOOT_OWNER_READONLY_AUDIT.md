# Read-only Docker/Compose/systemd boot-owner audit

**Issue #340, QA only. No production restart policy changes or
authorization to start, stop, repair, restore or upgrade anything.**

## Problem demonstrated by actual disposable guest reboot

On November 14, 2026 a dedicated unprivileged Debian 13 LXC114
with only the loopback network interface ran the exact local signed
v0.5.21-hw OCI image, offline Docker 26.1.5 and Compose 2.26.1.
The image's normal Telegram command was replaced by a synthetic
app.Storage loop with a private test-only SQLite.

Original synthetic Compose configuration used restart:
unless-stopped. A test-only pending-update marker correctly made
the systemd Compose ExecStartPre fail. Despite this, the Docker
daemon auto-started its container during real LXC114 cold boot.
The synthetic database grew from 147 to 231 rows while systemd
Compose reported FAILED, and the writer start count rose from
2 to 3. This is direct evidence that ExecStartPre failure alone
does not fence Docker's independent restart owner.

In the disposable replica ONLY, applying Docker restart=no and
Compose restart=no, then stopping the test writer, fixed this
specific two-owner race: another LXC114 cold boot with the fake
pending marker left the writer stopped; rows 234->234, starts
3->3 and complete logical SQLite SHA-256 unchanged.

The synthetic pending diagnostic remained intact. After archiving
ONLY that fake marker and an explicit systemd start, exactly one
test writer resumed, starts 3->4; no history was lost. The final
QA service was stopped with 245 synthetic rows, quick_check=ok.
No networking or Telegram activity was possible in this guest.

This does NOT validate updater timers, other owners, physical
power loss or the entire real application recovery path.

## What the observer does

The standalone CLI is inert by default. With explicit --observe
and supplied paths/identifiers, it only:

- uses Docker inspect to read whether one allowlisted container is
  running and its restart policy;
- parses a rendered, single-service Docker Compose configuration
  and reads only restart/network/port metadata, **never printing**
  expanded environment or credential values;
- reads a single allowlisted systemd unit's ActiveState,
  UnitFileState, recognized recovery ExecStartPre and Compose start
  command through systemctl show;
- tests existence (not contents) of an explicit pending marker.

It detects independent Docker restart owners, a Compose policy
that may re-enable auto-restart, a writer running despite pending,
missing/replaced startup guards and disabled systemd ownership.
Targets and source files are strictly validated, symlinks rejected,
and arbitrary container/unit names refused.

A result of SINGLE_OWNER_POLICY_OBSERVED or
PENDING_WRITER_FENCED_OBSERVED means **only these exact observed
owners agree**. It explicitly does not prove that every updater,
watchdog, boot recovery, systemd dependency or Telegram process
is fenced, and grants no boot or maintenance authorization.

Every evaluated result has these fields false:

- all_other_restart_owners_fenced
- offline_signature_verified_here
- schema_write_compatibility_proven
- boot_start_authorized
- source_install_authorized
- telegram_photo_authorized
- production_modified

## Commands

Inert default:

    python3 scripts/boot-owner-readonly-audit.py --dry-run

Observe one disposable networkless QA writer:

    python3 scripts/boot-owner-readonly-audit.py --observe \
      --container svt-qa-signed-writer-114 \
      --compose /var/lib/svt-qa-docker114/compose.yaml \
      --unit svt-qa-compose.service \
      --pending /var/lib/svt-qa-docker114/state/pending-update \
      --jsonl

Read-only observation of the already running production owner
configuration (does not invoke installation or recovery):

    python3 scripts/boot-owner-readonly-audit.py --observe \
      --container shelly-voltage-bot \
      --compose /opt/shelly-voltage/compose.yaml \
      --unit shelly-voltage-compose.service \
      --pending /var/lib/shelly-voltage-updater/pending-update \
      --jsonl

See -h or --help. Exit code 0 is an observed owner-policy
configuration, not release success; code 2 means unsafe/unverified
or blocked observation. Nothing ever sets production_modified=true.

## Acceptance

- 28/28 dedicated mocked unit tests PASS, strict ResourceWarning,
  full project Ruff quality PASS; functions <=40 lines.
- Actual networkless LXC114 after isolated no/no fix returned
  SINGLE_OWNER_POLICY_OBSERVED with no pending and stopped writer.
  With a fake pending marker and still-stopped writer it returned
  PENDING_WRITER_FENCED_OBSERVED, without writing or restarting.
- Actual production LXC110, observed strictly read-only, returned
  BOOT_OWNER_UNFENCED for **both**
  DOCKER_INDEPENDENT_RESTART_OWNER and
  COMPOSE_REENABLES_DOCKER_RESTART (unless-stopped in both places).
  No pending exists, production writer is Healthy and operating.
- Exact signed OCI content and staged v0.5.21-hw Python release
  files were previously compared byte-for-byte (24/24). Separate
  Cosign trust verification passed earlier, but this observer
  does not verify a cryptographic signature.

## Release and privacy gates

Changing production Docker restart policy alone is NOT enough:
a subsequent Compose up can re-enable the old policy. A safe
single-writer design needs a reviewed coordinated change to both
Docker and Compose, plus every other restart owner and maintenance
fencing policy, preserving availability when recoverable.

All real owner-fencing is still blocked. Fresh owner-approved,
writer-consistent source plus full current-history SQLite A/B
backups and independent restores remain required before live
changes; the historic 2026-11-14 03:44 archive lacks newer samples.

No production systemd, Docker, Compose, SQLite, source, Telegram
pinned message or TEXT-to-PHOTO change is part of this PR. Do not
apply a QA countermeasure to production without explicit owner
approval and a controlled maintenance window.
