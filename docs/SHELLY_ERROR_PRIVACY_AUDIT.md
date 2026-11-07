# Read-only Shelly health error privacy audit

When a Shelly read fails, the original HTTP library exception can
contain a configured private host/IP or device credential. The
production `/health` endpoint may repeat that exception in
`last_error` and in each phase snapshot. Historical log bundles
may contain old versions of the same unsafe text.

`scripts/shelly-error-privacy-audit.py` checks that the *current*
local health snapshot uses only the fixed categories introduced by
the QA candidate in PR #194. It **never** prints the original
`last_error` fields, IPs or secret strings.

Usage, from an approved candidate source checkout:

```bash
python scripts/shelly-error-privacy-audit.py --help
python scripts/shelly-error-privacy-audit.py --dry-run --json
python scripts/shelly-error-privacy-audit.py --json
```

The URL is restricted to a local HTTP `/health` endpoint, never an
arbitrary target. The request is read-only and times out after a
bounded delay. Exit codes: 0 = PASS/dry run, 2 = FAIL, 3 =
INCONCLUSIVE when no error is currently present in the snapshot.
Do not interpret an inconclusive result as proof of privacy.

To check a live container without installing anything:

```bash
docker exec -i shelly-voltage-bot python - --json \
  < scripts/shelly-error-privacy-audit.py
```

**Important:** This audit can detect unsafe *current* health fields,
but it cannot prove that historical logs are sanitized. It should
not be used to justify publishing an old support bundle. Only an
approved deployment of the code change will protect future
production errors. No Telegram API messages, Shelly RPC operations,
SQLite writes or Docker restarts are performed by this check.
