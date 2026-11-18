# Issue #402 — YASNO source permissions after signed source alignment

## Field failure (2026-11-18, 04:00–04:09 Europe/Kyiv)

Signed production 0.5.25-hw stayed online, healthy and ready, but
shelly-yasno-refresh.service failed with Python PermissionError at
03:30, 03:45 and 04:00. Its previous 03:16 execution had passed.

Actual installed rights: project directory 0750 root:shelly-yasno,
scripts directory 0710 root:shelly-yasno, but the Python source
script and its two imports were 0600 root:root:

- scripts/yasno-refresh.py
- yasno_cache.py
- yasno_schedule.py

In the published Git tree, all three are regular public source
files with mode 100644. The protected source copy can be produced
under umask 077 during an installer transaction; the existing
prepare_yasno_reader() restored group *directory traversal* but
did not restore file readability. A group-readable directory
alone cannot make a mode-0600 file readable to an unprivileged
account. This is a permission integration bug, not an electrical
failure or an error in the YASNO public schedule API.

Field recovery required only root-owned group-read 0640 on these
three files (group shelly-yasno). The .env file remained 0600
root:root, secrets directory 0700 root:root. Manual
runuser -u shelly-yasno -- python3 ... --status returned PASS;
the real systemd refresh service updated the schedule at 04:08:56
and exited successfully, with its timer still active.
No bot restart, new image or live SQLite modification.

## Source-only permanent correction

The installer now validates every required YASNO public file as a
regular owner-owned, single-link, non-symlink file **before** any
group permissions are granted. It then grants the dedicated YASNO
group read-only mode 0640 on those three files, preserving 0750
project traversal, 0710 script-directory traversal, protected
0600 .env and 0700 secrets. It does not chown to a nonroot user,
grant write access, or make the project globally readable.

When YASNO is not installed, the helper returns without modifying
permissions. A dry-run remains non-mutating. A missing file,
untrusted owner, symlink or hardlink fails closed rather than
accidentally exposing a private file or creating partial rights.

## Acceptance

- Two synthetic UID configurations root/nobody in network-disabled
  read-only Docker. Include source tar copy with mode-0600 files.
- Real Linux fixture with genuine user shelly-yasno and group-read
  access to only the three public files; .env/token denied.
- Validate source tar exclusion of private credentials and exact
  source/backup guard behavior as in the existing CI suite.
- Ruff and complete project quality gate for the **exact commit**.
- Source-only draft PR first, reviewed separately before a
  future stable image/release. Running 0.5.25-hw is not
  automatically upgraded by this PR.

Public graphical Telegram status is owned by its separate
feature/release workflow; permission QA does not publish or
modify Telegram PHOTO and does not infer owner opt-in.
