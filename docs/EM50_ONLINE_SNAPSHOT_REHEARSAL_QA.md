# EM-50 disposable online SQLite snapshot rehearsal (QA)

The 0.5.14-hw prerelease gate currently blocks because the newest
nightly backup does not match the live measurements, outage history,
or Telegram status metadata. Shelly is online and appends measurements
every few seconds.

This tool does not create a production rollback point. It safely
rehearses the exact full-history backup guard without stopping the bot.

    python3 scripts/em50-snapshot-rehearsal.py --help
    python3 scripts/em50-snapshot-rehearsal.py --dry-run
    python3 scripts/em50-snapshot-rehearsal.py --run --json

The dry-run does not read SQLite or create any files. The run opens
the source with a SQLite mode=ro URI and uses the official online
backup API into a unique 0700 temporary directory. The snapshot
database and gzip/sha256 manifest use mode 0600. The original source
never receives a write. The script invokes the same complete
row-hash parity guard as prerelease, then removes the temporary
directory on every exit.

Only nonsecret PASS/FAIL, reason codes and parity booleans are
reported. Telegram message IDs and individual voltage samples are
never printed. release_authorized is always false.

The rehearsal may PASS while the bot is writing regularly if no new
measurement arrives between the consistent snapshot and the final
parity check. It may also legitimately return BLOCKED if the live
database advances in that interval. This is an expected concurrent
change, not an authorization to pause, alter or discard measurements.

Tests prove that post-snapshot inserts, edits to an old reading even
with unchanged row count, outage-history edits, and changes to Telegram
status metadata each invalidate parity. They also cover symlink
rejection, protected temporary modes, and no-op dry-run.

A real release still requires a separate reviewed versioned,
persistent predeployment backup with its own SHA256 and rollback
procedure, followed by immediate parity under an approved cutover
protocol. Nothing in a temporary PASS replaces approval of
service-account installation, Docker mounts, merge, signing or rollback.

No Telegram sends/edits, device commands, production SQLite writes,
Docker restarts, user creation or systemd activation occur.
