# Issue #340 — isolated backup Bash/Python execution (QA ONLY)

**Stacked Draft; production NO_GO.** The old backup helper had a plain
Bash shebang. The updater's snapshot invoked it with explicit
`/bin/bash`. The helper's `python3 -` inline SQLite import also
accepted `PYTHONPATH` module overrides before SQLite verification.

This QA patch:
- Protects the backup helper direct shebang with `#!/bin/bash -p`.
- Protects the updater snapshot handoff using `/bin/bash -p`.
- Uses `/usr/bin/python3 -I -` for the inline SQLite backup.

`tests/test_privileged_backup_runtime_qa.py` operates on an
entirely disposable SQLite fixture with its own scratch backup
directory, verifies the checksum, decompresses a separate restored
SQLite file, and tests the recovered row and SQLite quick_check.
A forged `sqlite3.py` in `PYTHONPATH` and a forged Bash startup
are checked. Deliberately unprotected manual Python and Bash entries
remain negative controls; neither is a supported deployment method.

Actual LXC110 SQLite, Docker, systemd timers, Telegram and live
Shelly are not modified. This patch alone does not establish any
global owner lock, fresh independent backup on separate failure
domain, or runtime go/no-go decision. Physical power-loss test,
current signed OCI/source, owner G/B/W/A freeze, pending-boot
continuity, long-poll teardown, external security review and
explicit Owner Approval with single-NVMe loss risk remain mandatory.
Public pinned PHOTO is still NO_GO.
