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

### Durable isolated migration rehearsal (2026-11-27)

PR #484 CI 8/8 PASS. Added qa-legacy-receipt-migration.py, restricted
to a dedicated root-owned offline LXC114 fixture. This is not an
operator for production. It requires an existing protected operator
lock and real inactive/disabled dedicated test timer and inactive
static service. Prepare verifies the exact literal root-owned alias,
private target ownership/mode and private receipt identity/hash, then
creates an empty 0755 replacement directory and persists its identity
along with the legacy alias and private receipt in a private journal.
Recover removes only the reviewed alias and durably renames the
prepared directory into place. It verifies the original private state
again. Repeating recovery verifies the same empty replacement;
unexpected public content, changed private evidence, foreign alias
or orphan preparation fail closed. No private receipt is copied.

181 focused tests and project quality gate PASS (seven new migration
tests, also PASS as nobody). Actual isolated fixture
/var/lib/svt-legacy-receipt-qa.U3pJxK:
- Real enabled active QA systemd timer refused preparation; old alias
  remained present and no journal was written.
- After disabling/stopping dedicated units, prepare succeeded.
- SIGKILL after durable unlink and before rename returned 137;
  private receipt SHA256 unchanged and staged directory remained.
- Recovery and repeated recovery passed, same staged inode published
  as a root-owned ordinary 0755 empty public directory, private
  receipt SHA256 unchanged. Result
  PASS_REAL_SIGKILL_LEGACY_ALIAS_RECOVERY.
- Test unit/timer and enable link removed. No Docker writer started.

The alias/receipt and systemd units were synthetic; the test did not
ask DynamicUser to create its own StateDirectory. The journal is
not an authenticated approval, the lock coordinates cooperating
operators only, and a partial prepare journal or orphan stage
requires manual review. No production service or receipt was
modified. Production legacy alias and active old checker timer
remain. Before any real migration, obtain separate exact operator
and owner approval, reverify unit bytes and pair, and use the
approved maintenance fence. The QA utility must not be repointed
at production. Signed release publication and activation remain
distinct gates.

### Actual DynamicUser-created private state (2026-11-27)

Exact #485 CI 8/8 PASS. On isolated LXC114 a uniquely named
DynamicUser=yes service with StateDirectory and StateDirectoryMode=0750
ran successfully. systemd created the root-owned public symlink and
the private directory owned by dynamic UID 65412, mode 0750, with
a synthetic receipt mode 0644 owned by that UID. After the test
service was stopped, its private directory was moved into a dedicated
QA fixture and the equivalent literal legacy alias was created there.
The existing migration prepare/recover code passed with the actual
systemd-created private inode and receipt bytes preserved. New public
directory empty, root-owned 0755. Evidence:
 /var/lib/svt-legacy-receipt-qa.4q5xB5.
Result PASS_MIGRATION_OF_REAL_DYNAMICUSER_PRIVATE_STATE.
This is not a migration at the live systemd StateDirectory path:
the private state was relocated into the isolated fixture before
rehearsal, and the retired-unit checks used dedicated test units.
No production state or units were modified.
