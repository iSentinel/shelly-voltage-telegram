# Issue #340 — installer Bash entry and child handoffs (QA ONLY)

**Draft only; no live deployment or release authority.**

The legacy installer used an unprivileged Bash shebang and explicitly
called both `source-tar.sh` and `start-compose.sh` with plain `bash`.
That overrides the protected child shebang and may process `BASH_ENV`
before the Compose owner gate. This patch changes only the installer:
- `#!/bin/bash -p` for direct kernel/shebang entry.
- `/bin/bash -p` for both snapshot/copy source archive calls.
- `/bin/bash -p` for signed and unsigned Compose child calls.

Tests run only with `--help` and temporary fake Compose/curl/systemctl.
No real Docker, Shelly, Telegram, systemd, SQLite or deployment is used.
A deliberately forced `/bin/bash scripts/install.sh --help` still
loads the hostile test `BASH_ENV`; direct manual Bash and piped Bash
entry remain outside this patch and require explicit owner hardening.

Do not treat this as a writer freeze. The installer's legacy
`--build` and `docker update --restart=unless-stopped` paths remain
release blockers; protecting interpreter startup does not make those
operations safe or authorized. Old autonomous writers and the global
G/B/W/A lock, two fresh independently restored WAL-complete backups,
current signed OCI/source, physical blackout QA, real Telegram teardown,
independent review, single-NVMe loss acceptance and explicit Owner
Approval remain mandatory. Pinned PHOTO stays NO_GO.
