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
