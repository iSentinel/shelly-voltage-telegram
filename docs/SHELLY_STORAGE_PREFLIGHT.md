# Storage and backup headroom (read-only)

This check reports the available filesystem space of the monitoring
SQLite volume and the age of the latest matching compressed backup.
It does not open backup archives, change monitoring configuration,
restart Docker, call Shelly or Telegram, or delete files. Paths are
never included in JSON output.

The intended safety thresholds are **WARN below 1024 MiB free** and
**FAIL below 256 MiB free**. Missing backups are FAIL, while a backup
older than 36 hours is WARN. The thresholds are adjustable by CLI.

```bash
python scripts/shelly-storage-preflight.py --help
python scripts/shelly-storage-preflight.py --dry-run --json
python scripts/shelly-storage-preflight.py --json
```

Exit codes: 0 PASS or dry run; 2 FAIL; 3 WARN. In automation, WARN
must not be silently converted into PASS. The tool is snapshot-only
and does not schedule continuous monitoring on its own.

A high Docker footprint is not a reason to delete Docker volumes,
images, logs or test worktrees without analyzing ownership and
dependents. Do not vacuum or migrate live SQLite merely to gain
space; obtain an authorized maintenance window before doing so.

The test suite uses disposable filesystems and mocked disk usage.
Production is not modified by QA testing.
