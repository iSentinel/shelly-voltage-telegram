# Issue #402 — independent off-host SQLite recovery acceptance

**Status: BLOCKED / NO-GO.** This is an implementation and acceptance
plan, not proof that an independent backup already exists.

## Grounded current infrastructure

As of 2026-11-16 19:45 Kyiv, Gateway SSH exposes one authorized server,
`proxmox`, with **one physical 1.8 TB NVMe** (`nvme0n1`). The only
active Proxmox storage IDs are `local` (directory `/var/lib/vz`) and
`local-lvm` (local thin LVM). No NFS, CIFS, Ceph or SSHFS data mounts
were configured; no PBS storage was configured in `/etc/pve/storage.cfg`.
`proxmox-backup-client` is installed, but its presence does not prove
a working authenticated remote PBS repository. No remote connection,
secret or off-host restoration has been verified.

SQLite snapshots previously copied from LXC110 into a root-only path on
the same Proxmox host and verified with SHA256 protect against **some
container-local failures**. They do **not** protect against full NVMe,
host or disaster-domain loss.

## Destination requirements

1. Owner supplies a second physically separate destination: ideally
   authenticated PBS on another host/storage system, or an encrypted
   backup vault with independent disks. Confirm real host, storage
   identity, retention policy, network access and failure domain.
2. Transport is authenticated, encrypted in transit, scoped to backup
   traffic, and has no public unauthenticated endpoint. Credentials
   remain solely in a root-owned secret store (never GitHub, Telegram,
   a QA log or a ChatGPT response).
3. The backup operator may **write** the designated backup namespace
   and **read** only required source paths. Separate restore credentials
   should have no write access to production.
4. A second copy under a different directory, volume, Proxmox LVM
   dataset or partition of the same `nvme0n1` is **not** off-host
   evidence. A Proxmox backup tool installed locally is likewise not
   evidence until an independent remote restore succeeds.

## Prepare a WAL-complete, application-consistent snapshot

All production-changing operations require explicit Owner Approval
and a documented single-writer fence. Do not activate a second Telegram
bot or change the public channel merely to take a backup.

For a read-only inspection before cutover:

```bash
python3 scripts/em50-release-preflight.py --json
python3 scripts/em50-runtime-release-drift-audit.py \
  --expected-app-sha256 <reviewed-approved-source-sha256> --jsonl
```

The existing predeploy parity guard is intentionally strict: historical
backups diverge from the live database as new meter samples, outages and
Telegram status IDs are recorded. **Never edit the live SQLite database
or its `deployed.json` to force parity.**

At an approved cutover:
- Establish and independently verify one global writer owner for
  all SQLite and Telegram paths: running Docker bot, systemd/updater,
  backup workers and rollback paths. Avoid duplicate live instances.
- Record current production image ID, source SHA256, updater metadata,
  status message ID/media type and live database integrity.
- Produce the SQLite snapshot using the SQLite backup API, with
  explicit WAL-consistent semantics. **Do not** simply copy the
  `.sqlite3` file while an active WAL may contain uncheckpointed
  transactions. Preserve ownership, modes and metadata.
- Verify `PRAGMA quick_check=ok`, expected table counts, correct
  outage state and an internally consistent Telegram status ID/media
  marker, without publishing or editing messages.
- Generate an authenticated manifest with immutable source identity,
  snapshot checksum and capture timestamp. Move it and the backup over
  the encrypted channel to the independent destination.
- Check exact remote content checksum and retained backup object ID;
  enforce verified retention and ensure the backup cannot be casually
  overwritten by the next rollout.

## Independent restore acceptance

Before removing the writer fence or authorizing a new official image:

1. Restore on a **different physical host or storage failure domain**,
   with a separate throwaway workspace and no production Telegram
   bot token, no internet publishing and no access to the live channel.
2. Open the restored DB in read-only mode, run `PRAGMA quick_check`,
   count meter rows, and compare last sample timestamp, outage #32
   closure, Telegram status ID and matching text/photo media marker.
   Check parity to the captured snapshot, not a live database that
   has since resumed taking measurements.
3. Verify hashes of source snapshot, transferred object and restored
   SQLite file (accounting for any documented SQLite backup format).
4. Destroy only the disposable restore workspace **after** immutable
   evidence is retained. Do not silently promote restored old metadata
   over newer live Telegram messages.

The accepted evidence should state remote storage identity and
independent failure domain, remote object identifier, transport and
storage security checks, capture time, source and destination SHA256,
`quick_check`, parity verdict, and the identities of the owner/reviewer.
Redact addresses or credentials in public Issue comments when needed.

## Explicit NO-GO conditions

- Only one physical host/disk, no provisioned remote backup target.
- Off-host object uploaded but never independently restored.
- A stale snapshot that does not match the approved cutover point.
- Uncertain Telegram send results or nonmatching status ID/media marker.
- Docker/systemd updater can start a second writer while backups are
  captured or restored.
- Missing verified signed OCI/source identity, unclear rollback source,
  pending rollout state, or absent owner approval.

In all such cases, retain the known-good working emergency bot and
record `NO-GO`. The correct objective is **data recoverability with
Telegram identity continuity**, not a cosmetic green preflight.

## QA reference

Natural outage #32 is already accepted for live monitoring:
[public Telegram status #1130](https://t.me/svitlobot_yordanska_7/1130),
two independent 15-minute checks PASS, and read-only recovery evidence
checksums in [the field report](https://github.com/iSentinel/shelly-voltage-telegram/blob/qa/issue402-readonly-identity-audit-20261116/docs/EM50_OUTAGE32_FIELD_ACCEPTANCE_20261116.md).
Those existing host/LXC copies share one NVMe and do not satisfy this
off-host recovery gate.
