# Secure GitHub update checks and manual container upgrades

This feature checks GitHub Releases every **24 hours**. Automatic installation
is **disabled**. The monitor never receives access to the Docker socket, host
systemd, SSH credentials, or root privileges.

## Telegram interface

An authorized administrator can send `/updates` directly to the bot in a
private chat, or use the configured control chat. The bot replies privately
with its installed version and the latest stable GitHub Release. It cannot
start a host update. Other users receive no release details.

The administrator's private `/updates` reply is localized in Russian,
Ukrainian or English. Only a published stable release containing both the
source archive and `SHA256SUMS` is advertised; the message links to the
canonical project release page. If the GitHub check fails, a short localized
error is sent privately without exposing request details.

GitHub API responses are bounded to 128 KiB and validated before showing
availability in Telegram. Malformed JSON, missing release assets, unsupported
tags or unexpected metadata types fail closed with a private localized error.
This check never sends Docker commands.

## Host services

- `shelly-voltage-update-check.timer` checks every 24 hours after the previous
  activation, beginning after a short delay on boot.
- `shelly-voltage-update-check.service` runs an unprivileged, read-only
  release check and stores JSON in `/var/lib/shelly-voltage-update-check/status.json`.
- The daily checker directory contains only nonsensitive version status. Root
  snapshots and secret backups remain in a separate restricted directory.
- `shelly-voltage-updater-apply@.service` is a root-only **manual** oneshot
  service. It has no Install section, timer, or automatic trigger.
- `shelly-voltage-compose.service` uses `scripts/start-compose.sh` to
  preserve a pinned image choice after a host reboot.
- On every Compose service start, `ExecStartPre` calls the root-only
  `apply-update.sh --recover` mode. A private pending-update marker triggers
  restoration of the previous image selection and SQLite snapshot *before*
  allowing the service to start.

**Cosign v3 or newer is required.** Debian Trixie's Cosign 2.5.0 reports
`no signatures found` for the project's published OCI signatures, while
Cosign 3.0.6 successfully verifies the same digest. The manual updater
refuses unknown or older Cosign versions before any container changes.
Install a trusted, verified v3+ binary in the test LXC for acceptance.

Only published stable GitHub Releases are eligible. The updater rejects
prereleases, downgrades, the already deployed version, and missing release
assets. It checks the source archive SHA256 and verifies the pinned multiarch
GHCR image's Cosign certificate identity and GitHub Actions OIDC issuer.

## Release promotion policy

A new signed stable image tag `vX.Y.Z-hw` is created only by a push of the
corresponding Git tag. GitHub Actions must verify that the checkout points
to the exact tagged commit, the tag matches `VERSION`, and
`RELEASE_VERSION` matches `VERSION`. Ordinary `main` builds remain
unversioned development images. Manual `workflow_dispatch` builds can
publish only `manual-*` tags and cannot sign an arbitrary commit as a
stable release. The same source version checks remain in the GitHub Source
Release workflow.

The policy is enforced by `scripts/image_release_policy.py`, including
unit tests for mismatched tags, candidate versions, commit identities and
invalid manual tags. Use `python3 scripts/image_release_policy.py --help`
to see validation parameters. Do not create the actual release tag until
the software candidate and live hardware acceptance gates are satisfied.

## Before any installation

The installer checks available space on the Docker data filesystem, the
backup directory and the temporary-files filesystem **before pulling** an
image and again **before the SQLite backup**. It requires at least 768 MiB
free on each, or more for a large database. It does not automatically prune
old images: they may be needed for a safe rollback.

The updater verifies that SQLite is intact, Shelly is online, its latest
reading is at most 120 seconds old, voltage is in the conservative
207–253 V upgrade band, and there is no open outage, active voltage episode,
or outstanding stable-recovery timer. Every unsafe or unknown state denies
installation.

Before changing the Compose image, the updater takes a restricted snapshot of
the SQLite database, environment file, secrets, and previous image choice.
The image is pinned by immutable digest. After replacement, it requires
`/health`, fresh `/ready` status and a second electrical preflight. A failed
update restores the previous image choice and the backed-up database, keeping
a separate rescue copy of the post-failure database. Inspect the rescue
copy before discarding any measurements created during the upgrade window.

The updater writes a durable pending-update marker before selecting the
new image. The marker is cleared only after the replacement passes health,
readiness and electrical checks. If the host restarts mid-upgrade, systemd
restores the original image and SQLite before launching the Compose stack.
An invalid snapshot or marker fails closed and keeps the pending state for
manual recovery. The earliest post-failure rescue database is preserved on
repeated recovery attempts.

Boot recovery was additionally tested through three real, controlled
reboots of disposable LXC 111: an interrupted update was restored, a malformed
pending marker blocked startup, and a corrupted backup checksum blocked
restoration until corrected. These tests do **not** simulate uncontrolled
host power loss or storage corruption. Operator review remains necessary
after a real host failure. The updater does not change electrical settings.

## Commands

Run these commands only on a test LXC with the service files installed and a
released, signed target version. The production installation requires a
separate hardware acceptance and explicit approval.

```bash
python3 scripts/github_update.py --help
bash scripts/apply-update.sh --help
# Recovery is invoked automatically by systemd before starting Compose.
# On the disposable test LXC only:
sudo bash scripts/apply-update.sh --recover
python3 scripts/github_update.py check --root /opt/shelly-voltage
python3 scripts/github_update.py preflight
sudo bash scripts/apply-update.sh --version 0.5.13-hw --dry-run
sudo systemctl start shelly-voltage-updater-apply@0.5.13-hw.service
sudo journalctl -u shelly-voltage-updater-apply@0.5.13-hw.service -n 80
sudo systemctl list-timers shelly-voltage-update-check.timer
```

The example version must already exist as a published signed Release; a
candidate branch alone is not sufficient. All commands fail closed when
the active device or voltage is unsafe.

## Isolated positive acceptance (2026-11-05)

On disposable LXC 111, the updater installed the published, Cosign-verified
`v0.5.12-hw` image over a synthetic `0.5.11-hw` test fixture, completed
the backup and post-install checks, and returned `UPDATE PASS`. The new
application read a local synthetic `EM1.GetStatus` response of 230 V /
50 Hz; `/health`, `/ready`, Docker health and SQLite writes passed.
Telegram was disabled, and no live Shelly or production credentials were
involved.

After a real reboot, the pinned digest and deployed version were retained;
the updated application was healthy again. The fixture's initial Compose
configuration had omitted `env_file` and its unversioned SQLite lacked
`phase`. Those test-only setup issues were corrected before successful
acceptance. The QA stand was returned to its original image and database.
Evidence remains privately stored on LXC 111 under
`/root/qa-positive-acceptance-20261105/`.

This establishes a *working signed update with a simulated sensor*. It
does not establish Telegram notification delivery during a real outage,
recovery of an uncontrolled power loss, or hardware acceptance of the
unreleased candidate version. Production remains unchanged until the
active Shelly outage is resolved and separately accepted.

## Acceptance before production

Validate source archive and Cosign verification on a pinned digest, unit
tests, offline/low-voltage gates, backup/checksum/restore behavior, restart
with the pinned override, interrupted updates, and manual rollback on a
separate LXC. Only then enable the 24-hour check timer and consider a
manual production installation when the live Shelly has stabilized.

No real outage or low-voltage event should be induced for acceptance.
