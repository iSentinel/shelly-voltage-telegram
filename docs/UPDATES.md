# Secure GitHub update checks and manual container upgrades

This feature checks GitHub Releases every **24 hours**. Automatic installation
is **disabled**. The monitor never receives access to the Docker socket, host
systemd, SSH credentials, or root privileges.

## Telegram interface

An authorized administrator sends `/updates` in the configured control chat.
The bot privately reports its installed version and the latest stable GitHub
Release. It cannot start a host update. Other users receive no release details.

## Host services

- `shelly-voltage-update-check.timer` checks every 24 hours after the previous
  activation, beginning after a short delay on boot.
- `shelly-voltage-update-check.service` runs an unprivileged, read-only
  release check and stores JSON in `/var/lib/shelly-voltage-update-check/status.json`.
- The daily checker directory contains only nonsensitive version status. Root\n  snapshots and secret backups remain in a separate restricted directory.\n- `shelly-voltage-updater-apply@.service` is a root-only **manual** oneshot
  service. It has no Install section, timer, or automatic trigger.
- `shelly-voltage-compose.service` uses `scripts/start-compose.sh` to
  preserve a pinned image choice after a host reboot.

Only published stable GitHub Releases are eligible. The updater rejects
prereleases, downgrades, the already deployed version, and missing release
assets. It checks the source archive SHA256 and verifies the pinned multiarch
GHCR image's Cosign certificate identity and GitHub Actions OIDC issuer.

## Before any installation

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

A power loss or host crash during an upgrade still requires operator review.
Do not claim a fully automatic crash-safe rollback until that scenario has
been independently accepted. The updater does not change electrical settings.

## Commands

Run these commands only on a test LXC with the service files installed and a
released, signed target version. The production installation requires a
separate hardware acceptance and explicit approval.

```bash
python3 scripts/github_update.py --help
bash scripts/apply-update.sh --help
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

## Acceptance before production

Validate source archive and Cosign verification on a pinned digest, unit
tests, offline/low-voltage gates, backup/checksum/restore behavior, restart
with the pinned override, interrupted updates, and manual rollback on a
separate LXC. Only then enable the 24-hour check timer and consider a
manual production installation when the live Shelly has stabilized.

No real outage or low-voltage event should be induced for acceptance.
