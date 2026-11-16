# Issue #402 — release preflight host version parity

This QA change prevents an outdated host checkout from being accepted as
a new release source while the running signed-base OCI and updater have a
newer version. It **does not authorize deployment** and makes no production
database, Docker, Shelly or Telegram changes.

## Problem reproduced on 2026-11-16

Gateway read-only inspection of Proxmox LXC110 found:
- Host `/opt/shelly-voltage/VERSION` and `RELEASE_VERSION`: **0.5.15-hw**.
- Updater `deployed.json` and running container `/app/VERSION`: **0.5.21-hw**.
- GitHub `main` version markers: **0.5.21-hw**.
- Running emergency `/app/app.py` and host checkout `app.py` have
  different SHA256 fingerprints.
- The existing `source_candidate` check compared the two host version
  markers with each other, returning **PASS** even when both were old.

The new independent `source_vs_deployed` automatic preflight gate
reads the updater's `deployed.json` and rejects either host version
marker **older** than the currently deployed release. It parses
`x.y.z-hw` numerically and rejects missing, malformed or symlinked
updater metadata.

A candidate **newer** than the deployed release can be staged when the
host's stable marker is equal to or newer than the deployed version.
The existing `source_candidate` policy still controls candidate versus
stable marker. Version parity alone does **not** verify signatures,
image digests, backup restorability or Owner Approval.

## Safe operator invocation

```bash
python3 scripts/em50-release-preflight.py \
  --root /opt/shelly-voltage \
  --deployed-state /var/lib/shelly-voltage-updater/deployed.json \
  --db /var/lib/shelly-voltage/voltage.sqlite3 \
  --json
```

The command is read-only. Do not print full environment, bot tokens or
raw SQLite state in issue comments. Do not edit `VERSION`,
`RELEASE_VERSION`, updater metadata or active container image merely to
silence this failure.

## QA and acceptance

On the actual LXC110 checkout the expanded preflight returned:

| Automatic check | Result |
|---|---|
| `source_candidate` | PASS — two old markers agree with one another |
| `source_vs_deployed` | FAIL — source 0.5.15-hw is older than deployed 0.5.21-hw |
| `live_health` | PASS — Shelly fresh and /ready 200 |
| `sqlite_readonly` | PASS — database integrity OK |
| `predeploy_backup_parity` | FAIL — stale backup and live SQLite state differ |
| `release_authorized` | **false** |

Tests cover an older host checkout, a newer candidate with a current
stable marker, an outdated stable marker, missing/invalid/symlinked
updater metadata and integrated fail-closed release authorization.
The existing preflight tests must also remain green, including
protected Telegram receipt privacy and stale-backup safeguards.

## Next official release gates

See [the signed cutover checklist](https://github.com/iSentinel/shelly-voltage-telegram/blob/qa/issue402-runtime-release-drift-audit-20261116/docs/ISSUE402_OWNER_GATED_SIGNED_CUTOVER.md).
The emergency hotfix is operating correctly and must not be restarted
or replaced solely to make the checker green.

A correctly reviewed, owner-approved cutover requires a signed
immutable GHCR OCI, matching source/version evidence, coherent updater
and boot Compose configuration, a verified WAL-complete **off-host**
backup restored on an independent physical failure domain, a
single-writer fence, rollback rehearsal and explicit Owner Approval.
