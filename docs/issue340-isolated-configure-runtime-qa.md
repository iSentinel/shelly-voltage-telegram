# Issue #340 — protected configuration shell and Python (QA ONLY)

**Draft on #397, not deployed. Production remains NO_GO.**

The legacy installer directly launches `configure.sh`. Its former
environment-based Bash shebang and inline `python3 -` could import
untrusted `BASH_ENV` and `PYTHONPATH` before serializing settings
or configuring optional Telegram secrets.

The candidate uses `#!/bin/bash -p` and
`/usr/bin/python3 -I -` in `scripts/configure.sh`.
No config keys, token behavior or live service files are changed.

Isolated LXC114 QA uses only a temporary `SVT_ROOT`, example
`.env.example`, a fake documentation-only Shelly host
`192.0.2.42`, no Telegram token and an inert chown executable.
A fake `pathlib.py` and Bash startup poison must not run.
Negative control demonstrates that manually forcing ordinary
Bash still bypasses a protected shebang.

This is not an approved deployment mode. Manual/piped Bash and
emergency callers require a separate audit. Complete old owner
G/B/W/A fencing, approved pending boot, current signed OCI/source,
independently restored twin WAL-complete SQLite backups across
adequate failure domains, real Telegram teardown, physical
power-loss QA, external security review and explicit Owner
Approval of potential single-NVMe complete loss remain required.
Public pinned PHOTO is independently NO_GO.
