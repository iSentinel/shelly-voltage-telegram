## Explicit approval before replacing live SQLite on rollback

Rollback to an older application snapshot can also restore an older
SQLite database, losing measurements and outage history added since
the snapshot. Inspect --dry-run output before taking action:

    sudo bash scripts/rollback.sh --dry-run --no-start       --snapshot /var/backups/shelly-voltage/upgrades/<snapshot>

If the current SQLite file exists, REAL rollback now fails closed
unless the operator explicitly adds --confirm-db-replacement.
This confirmation authorizes potential data loss; it does not
preserve current measurements. The rescue backup is still created
before service stop. Never run this option without reviewing the
snapshot and agreeing to its history/DB schema consequences.
See docs/ROLLBACK_HISTORY_GUARD_QA.md for QA evidence.

## Read-only source/image deployment parity before graphic cutover

Run this audit **before** allowing the existing status message to change
from text to photo or considering a source-only rollback:

    python3 scripts/deployment-state-audit.py --jsonl

The audit compares the locally installed `VERSION` and
`RELEASE_VERSION` with the protected updater `deployed.json` and
Compose image override, then uses read-only `docker inspect` to confirm
the exact pinned GHCR digest, Docker health and restart policy. It
returns exit code `2` and `SOURCE_IMAGE_VERSION_MISMATCH` if the
signed container is newer than the installed server scripts. The
default `0.5.18-hw` deployment may exhibit this mismatch after an
image-only upgrade while `/opt/shelly-voltage` still carries
`0.5.15-hw`.

`PASS` establishes only **local component parity**. This audit does
not verify the Cosign signature, authorize Telegram media conversion,
replace A/B snapshots, or perform a source update. A BLOCKED result
requires reconciliation of the verified release source with the
running signed image before a public graphic cutover. Do not edit
VERSION markers manually to silence the check.

# Secure GitHub update checks and manual container upgrades

This feature checks GitHub Releases every **24 hours**. Automatic installation
is **disabled**. The monitor never receives access to the Docker socket, host
systemd, SSH credentials, or root privileges.

## v0.5.19-hw protected signed-image source reconciliation

When an image-only update has already pinned an immutable GHCR image,
source installation must **not** rebuild a local container. The
installer first checks owner-private updater receipt/override, exact
signed release version, stopped and restart-fenced writer, and Docker
image identity. A mismatch fails before the source or SQLite snapshot
or any installation write. An existing signed image with missing
protected receipt also blocks local fallback.

After the protected old-source and full SQLite snapshot, deployment
uses the pinned override without `--build`, restores Docker
`unless-stopped` restart policy, waits for stable local `/ready`,
and checks read-only source/image parity. No media migration occurs
in this stage. For an existing signed deployment, install the new
signed image **first**, then reconcile the matching official signed
release source in a planned, watchdog-protected maintenance window.

A source marker alone is not signature verification or proof of
successful physical deployment. Do not manually edit VERSION to
suppress a parity mismatch. Public Telegram PHOTO cutover still
requires a fresh real stopped-writer A/B source+SQLite backup,
owner approval, rollback readiness and unrelated pinned-message audit.

## v0.5.18-hw startup readiness stabilization

The signed updater waits for **three consecutive** HTTP 200 probes at
local `/ready` before committing a deployment. A merely listening
HTTP server (`/health`=200) does not prove that Shelly has produced
fresh, safe voltage measurements. The bounded two-minute retry
window rejects persistent or flapping 503 responses and retains
automatic recovery to the previous verified image. Offline tests
simulate the observed 503-after-restart failure without editing Telegram.

## v0.5.17-hw source release and guarded production rollout

Version 0.5.17-hw aligns the protected graphic-cutover manifest and
includes channel-bound rollback checks, missing SQLite fail-closed
guard and compatible Cosign v3 selection. The sole owner explicitly
accepted proceeding without an independent source reviewer; this is
not represented as an independent code approval.

The VERSION marker alone is not a signed release. Tag only after
owner-controlled source QA, exact merged-main CI and reproducible
archive verification. Independently verify published GitHub source
SHA256 and GHCR Cosign signature. A signed source release does not
authorize live installation or public Telegram media conversion:
real stopped-writer A/B, rollback and recipient acceptance stay mandatory.

## Select a supported Cosign binary for signed updates

The root-only signed updater checks both the certificate identity and
the GHCR image digest with Cosign v3 or newer. It prefers the compatible
binary in `/usr/local/bin/cosign`, then `/usr/bin/cosign`. An older or
development build is not accepted merely because it is first in `PATH`.
To use another reviewed binary, set `SVT_COSIGN_BIN` to its absolute
path; an invalid explicit override fails closed.

On the production host, inspect the signature and release metadata
without changing Docker or Telegram:

```bash
sudo bash /opt/shelly-voltage/scripts/apply-update.sh \
  --version 0.5.16-hw --dry-run
```

Only after the owner-approved maintenance and rollback plan, physical
readiness, source QA, protected snapshots and Telegram media
acceptance have passed, run the authorized production installation:

```bash
sudo bash /opt/shelly-voltage/scripts/apply-update.sh \
  --version 0.5.16-hw
```

Do not replace a failed signature check with an unsigned image, disable
certificate checks or edit a public Telegram message as part of dry-run.
A successful dry-run does **not** approve production deployment.

## Protect credentials in pre-upgrade snapshots

When `scripts/install.sh` upgrades an existing deployment from a separate
source directory, it first creates a versioned source + online SQLite
backup. The source snapshot and the source-copy step share
`scripts/source-tar.sh`: it excludes local `.env` files and **all
`.env.*` backups except the public `.env.example` template**, plus
`secrets/`, Git metadata, and generated Python bytecode. This avoids
archiving older configuration backups that may still hold credentials.

The new snapshot directory is created with mode `0700`. The source
and SQLite archives retain their SHA256 checksums and can be checked
with the existing rollback dry run:

```bash
sudo bash scripts/rollback.sh --dry-run --no-start \
  --snapshot /var/backups/shelly-voltage/upgrades/<snapshot-directory>
```

Create or choose the snapshot on the host; never upload private
archives, credentials, or SQLite data to a public repository.
`bash scripts/source-tar.sh --help` describes the standalone archive
filter. These safety checks do not enable automatic installation.

## Validate SQLite restore content before interruption

Source archive safety and SHA256 verification are separate checks. The
installer now also inspects the freshly created SQLite backup **before**
copying any new release files, and `rollback.sh` checks the selected
backup **before** rescuing data or stopping Docker. The read-only
validation expands the gzip stream with a 2048 MiB default size limit
into a temporary directory, opens SQLite read-only, runs
`PRAGMA quick_check`, and confirms the `measurements` table exists.
It does not write to the production database or send Telegram messages.

```bash
python3 scripts/validate-sqlite-archive.py --help
python3 scripts/validate-sqlite-archive.py \
  --archive /var/backups/shelly-voltage/upgrades/<snapshot>/voltage-YYYYMMDD-HHMMSS.sqlite3.gz
sudo bash scripts/rollback.sh --dry-run --no-start \
  --snapshot /var/backups/shelly-voltage/upgrades/<snapshot>
```

The validator uses only the Python standard library, so it works on
the recovery host even without the bot's application dependencies.
A malformed gzip or SQLite image fails with exit code 2 before service
disruption. A successful temporary restore is **not** evidence that a
real production rollback or a candidate release has been approved.

## Fail closed when live SQLite is missing during a rollback

On an established deployment, the public Telegram status might already
have been converted from text to photo. The rollback guard must not
infer an unmigrated text message merely because the live SQLite file
is absent, even when all SQLite WAL and journal sidecars are absent.
Both the pre-stop and post-stop checks return BLOCKED until the missing
database and public media identity are reconciled through a separate
reviewed recovery procedure.

A complete existing SQLite database with verified text status remains
eligible for ordinary rollback checks. A new installation must use the
documented fresh-install path, not treat a missing database as rollback
approval. Do not fabricate an empty SQLite file or bypass the guard.

## Validate rollback archives before stopping the monitor

`scripts/rollback.sh` validates the source archive **before**
making a rescue backup, stopping Compose or replacing files. Alongside
the existing SHA256 checks, it now rejects path traversal, absolute
paths, symbolic/hard links, special file types, setuid/setgid modes,
more than 20,000 archive entries and uncompressed source archives
larger than 512 MiB.

During extraction, local `.env`, `.env.*`, `secrets/` and
`validation/` are excluded from older source snapshots. Even an
otherwise valid historical snapshot must not overwrite current
credentials or private QA evidence.

The isolated rollback fixture includes two deliberately unsafe,
checksum-consistent source archives (a traversal path and a symlink).
Both must fail before any test target changes. Use the read-only
`rollback.sh --snapshot DIR --dry-run --no-start` preflight before
considering a real version rollback.

## Preserve local evidence on rollback

`scripts/rollback.sh` preserves the current deployment `.env*`
configuration files (including local backups), `secrets/`, and
`validation/` while restoring the versioned application source and
SQLite database. Prior to this fix, a rollback could delete local
environment backups and private QA acceptance reports.

`scripts/source-tar.sh` excludes these private records from new source
snapshots and installer source-copy streams. It also skips local
`dist/`, `dist-audit/`, `dist-publish/`, tool caches, SQLite
databases, debug logs and symbolic links. The public
`.env.example` template is retained. Privately held evidence
remains at its original path instead of being embedded in a
versioned code archive.

The isolated rollback fixture checks that `.env`,
`.env.pre-upgrade`, `secrets/`, and `validation/` survive a
real test rollback against a temporary source/database directory.
This fixture never targets the installed monitor.

## Public source-release privacy

`scripts/release.sh` uses a separate public-release filter. It excludes
`.env`, every local `.env.*` backup and nested private environment
files, plus `secrets/`, private validation data, generated databases
and build outputs. The safe `.env.example` template is copied back
explicitly. A final stage check refuses any private `.env*` filename
other than `.env.example`.

The reproducible-release test includes a separate artificial source
directory with fake Telegram/Shelly credentials stored in multiple
environment backup names. It verifies that none enter the release.
Use `bash tests/test_release.sh` to check packaging without publishing
anything. Do not use a real production configuration as test data.

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

The separate host-side checker (`scripts/github_update.py`) also limits
GitHub and GHCR JSON responses to 128 KiB. It rejects unexpected structures,
malformed release assets, missing `SHA256SUMS`, invalid release tags, and
malformed OCI manifest metadata. Its release URL is constructed from the
validated project tag. Failures block updates; they do not fall back to
unsigned images or automatic installation.


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

## Candidate Docker QA without publishing a release

The `0.5.13-hw` source candidate was separately built and exercised
in LXC 111 on 2026-11-05 with a private mock Shelly EM1 endpoint.
The candidate responded healthy/ready, wrote 230 V / 50 Hz test samples,
opened one synthetic outage after the mock endpoint disconnected,
then closed it after reconnection, with zero synthetic 0 V measurements.
The disposable candidate image and test containers were removed;
the original QA monitor remained healthy.

This does **not** authorize a production update: candidate image
signing/publication, live hardware acceptance under that version and
independent Telegram delivery verification remain outstanding.
Production stays on published `0.5.12-hw` until those gates pass.

## Acceptance before production

Validate source archive and Cosign verification on a pinned digest, unit
tests, offline/low-voltage gates, backup/checksum/restore behavior, restart
with the pinned override, interrupted updates, and manual rollback on a
separate LXC. Only then enable the 24-hour check timer and consider a
manual production installation when the live Shelly has stabilized.

No real outage or low-voltage event should be induced for acceptance.
