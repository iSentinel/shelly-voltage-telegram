# Issue #340 — rollback Python import isolation (QA ONLY)

**Stacked Draft on #399; production remains NO_GO.**

Read-only caller review of immutable PR399 identified eight residual ordinary
`python3` interpreter calls in `scripts/rollback.sh`, despite the script
having a protected Bash shebang and the owner-freeze guard already using
isolated Python. They covered archived source/SQLite validators, pre-restore
SQLite quick check, signed-image rollback mode, installed-state parity audit,
and both Telegram graphic media checks.

This patch changes those calls to `/usr/bin/python3 -I` (or `-I -` for
embedded stdin Python); the earlier owner-freeze check remains unchanged.
It does not loosen or implement a writer release gate, restore archived data,
contact Telegram, or touch Docker.

QA tests verify all nine Python call sites, invoke five real CLI `--help`
paths under a forged `argparse.py` in `PYTHONPATH`, exercise isolated
interpreter semantics, and include an unsafe nonisolated negative control.
Only scratch resources are used; no real rollback or service start occurs.

Manual `bash scripts/rollback.sh` overrides the hardened shell shebang,
and unauthenticated mutable remote shell piping is still unsupported.
The rollout still needs the full old-owner G/B/W/A writer freeze,
approved pending boot, CURRENT signed OCI/source, two fresh WAL-complete
independently restored backups across adequate failure domains, real
Telegram long-poll teardown, physical power interruption QA, independent
security review and explicit Owner Approval recognizing total single-NVMe
loss risk. Public pinned PHOTO remains independently NO_GO.
