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
Executing a rollback requires separate approval and a verified rescue of
the latest SQLite data after the writer stops. A historical database
snapshot alone is insufficient.
