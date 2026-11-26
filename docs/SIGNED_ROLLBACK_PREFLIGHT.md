# Signed rollback preflight

Use `check-rollback` to review an older published source archive and
container image against a running, verified supervisor. Supply the older
release version, commit SHA, image digest, archive SHA-256, archive,
Sigstore bundle, checksum file, and confirmed Telegram photo ID.

```bash
python3 scripts/signed-forward-cutover.py check-rollback --help
```

The preflight verifies both signatures, the installed release receipt,
the startup owner, the SQLite mount and integrity, and the recorded photo
identity. It compares the Python monitor files, Dockerfile, build context
rules and dependency declarations in both signed archives. A change in
those inputs requires separate compatibility testing with a disposable
database.

`PASS_ROLLBACK_PAIR_PREFLIGHT_ONLY` confirms those checks. The command
opens the live database read-only and leaves the current release running.

## Execute an approved rollback

Use the reviewed operator checkout for both rollback and recovery.
The `rollback` action accepts the same signed target inputs as
`check-rollback` and requires `--from-digest` for the approved running
image. The command checks the running pair again before stopping it.

```bash
python3 scripts/signed-forward-cutover.py rollback --help
python3 scripts/signed-forward-cutover.py recover --transaction <id>
```

After stopping the writer, the command saves the latest SQLite history
in a standalone rescue with a SHA-256 checksum. The older image opens
the existing database. If an interruption occurs before it may start,
recovery returns to the original supervisor. Once the older image may
have written, recovery resumes that same image with the current data.
Repeating recovery after completion verifies the running pair again.
An incompatible scheduled release checker stays paused after a completed
rollback. Recovery restores its prior timer state when it returns to
the original release.

## Legacy DynamicUser public receipt migration (issue #453)

The old release-check unit used
`StateDirectory=shelly-voltage-update-check` with `DynamicUser=yes`.
systemd can create a root-owned alias at
`/var/lib/shelly-voltage-update-check` pointing to
`private/shelly-voltage-update-check`. This is not an eligible public
receipt directory. The new unit uses a separate private state directory,
`shelly-voltage-release-check`, and reads the public receipt as a credential.

The operator must review this migration **before** an approved cutover.
The new preflight refuses both live and dangling symlinks, and preserves the
running writer. Do not weaken the protected path checks.

1. Confirm the current startup owner, signed release pair and healthy monitor.
   Review the old checker service and timer against the installed signed
   release. Refuse local overrides or unexpected paths.
2. Under the approved maintenance procedure, stop and disable only the old
   release-check timer; stop its oneshot service. Confirm the timer is
   disabled and both units are inactive. Do not stop the monitor.
   Prevent any concurrent operator from starting the old checker.
3. Inspect the alias with lstat/readlink. Accept only the expected root-owned
   systemd link whose literal target is
   `private/shelly-voltage-update-check`. Confirm the private parent is a
   root-owned 0700 directory and its target is a real directory. Refuse
   an unexpected target, ownership, link chain, or missing private state.
4. Record the old link target and preserve the original private directory and
   status files. Remove only the verified alias, never its target. Provision
   a new ordinary root-owned 0755 public directory. If interrupted between
   unlink and mkdir, inspect the preserved target and complete provisioning.
   Never copy untrusted writable private receipts into the public directory.
5. Verify that the resulting public path and any existing receipt pass the
   protected-path policy. Keep the legacy checker disabled. Repeat the signed
   preflight and proceed only under the separately authorized cutover.
   The new signed operator writes the receipt after readiness and installs
   the new checker/timer after the transaction is verified.

If a v0.5.28 transaction already reached `candidate-may-have-run`,
do not restore the earlier SQLite snapshot. After the reviewed directory
migration, use the original operator's `recover --transaction <id>`
to finish the installed pair, and verify history and timer state.

This procedure was rehearsed on LXC111 with a synthetic meter and genuine
signed v0.5.27/v0.5.28 images. It is not permission to modify production.
