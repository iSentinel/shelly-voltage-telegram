# Issue #412 — read-only update-checker with systemd credentials

## Reproducible field finding

On LXC110 at 2026-11-18 02:00 Europe/Kyiv, the
shelly-voltage-update-check.timer was enabled but inactive.
The corresponding service used DynamicUser=yes with no
supplementary groups, WorkingDirectory=/opt/shelly-voltage
and a direct Python source path under the protected root.

An owner-approved installation protects project source with
0750 root:shelly-yasno and scripts with 0710 root:shelly-yasno.
An unrelated nonroot user cannot traverse this project or
read the checker. Separately, .env remains 0600 and secrets
0700: expanding project read permissions or running the checker
as root is not acceptable.

The release checker is not an update installer:
github_update.py check reads the published GitHub release
metadata and writes status.json with auto_install=false.
It must never invoke apply-update.sh, Docker or Telegram.

## Proposed least-privilege design

LoadCredential asks the systemd service manager to copy only
the public github_update.py code and RELEASE_VERSION marker
to its private, read-only credential directory for each start.

    LoadCredential=github_update.py:/opt/shelly-voltage/scripts/github_update.py
    LoadCredential=RELEASE_VERSION:/opt/shelly-voltage/RELEASE_VERSION
    WorkingDirectory=/
    ExecStart=/usr/bin/python3 %d/github_update.py check --root %d

The service retains DynamicUser, private StateDirectory,
NoNewPrivileges, ProtectHome, ProtectSystem=strict and the
restricted capabilities/address families. The checker only
reads credential copies, not the original protected project.
This works even when the optional YASNO group is absent.

No private credentials (.env, secrets, chat ID, bot token,
SQLite database) are passed as LoadCredential data. No
project chmod, group membership or root elevation is added.

## Test evidence and remaining acceptance

- The source-controlled unit passes systemd-analyze verify.
- Synthetic isolated checker tests verify no source-directory
  requirement; only two loaded public credentials; current and
  newer releases; auto_install=false; JSON status confined to
  StateDirectory; missing credentials fail; Python CLI help.
- The tests run as both root and nobody with no network and
  no capabilities; actual external GitHub responses are mocked.
- Production LXC110 remains untouched: disabled/inactive checker
  is not enabled by source-only QA.

LXC114 is a minimal QA container without systemd. We cannot
claim actual service-manager LoadCredential/ DynamicUser
end-to-end proof there. A future authorized deploy window
must verify the updated service using the real protected
source permissions, successful status.json update, active
timer with a next fire, no automatic installation and no
access to .env or secrets. Release/signing and cutover
remain separate owner decisions.

Issue #412 tracks this independent future stage. Owner
accepted catastrophic loss of a single NVMe, not proof of
off-host backups. Public Telegram PHOTO behavior is managed
by its separate owner-approved feature/release workflow.
