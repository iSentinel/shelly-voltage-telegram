# YASNO systemd service-account prerequisite (QA)

Live Gateway acceptance, November 8 2026, LXC 110.

- A transient systemd job with User=10002 exited 217/USER before Python
  executed: no NSS/passwd record exists for numeric UID10002.
  This was not detected by earlier setpriv tests.
- A second isolated transient producer ran under the existing nobody
  account (UID65534) and passed with ProtectSystem=strict.
- Another isolated transient exporter ran as root with bounded
  CAP_CHOWN and CAP_DAC_OVERRIDE and wrote to a protected recipient
  directory. A read-only Docker UID10001 container consumed its cache.
- No permanent users, unit files, timers, Docker mounts, credentials,
  SQLite data or Telegram messages were changed.

The optional scripts/yasno-systemd-account-preflight.py is strictly
read-only. It validates that a resolvable shelly-yasno account exists,
uses a noninteractive shell and is neither root nor bot UID10001.
It also rejects producer primary or supplemental membership in the
Telegram reader group (default GID 10001). NSS group-lookup failure
blocks release instead of assuming safe isolation.
It requires producer owner:group mode 0700, producer file mode 0600,
recipient root:10001 mode 0750 and recipient file root:10001 mode 0640.
Symlinks, hardlinks, missing files and mismatched permissions fail.

Commands for QA:

    python3 scripts/yasno-systemd-account-preflight.py --help
    python3 scripts/yasno-systemd-account-preflight.py --dry-run
    python3 scripts/yasno-systemd-account-preflight.py --check

Current LXC --check is EXPECTED FAIL (KeyError) because no
shelly-yasno account is installed. Only an explicitly authorized
future installation should create the service account, protected
paths and activate the uninstalled example units.

Regression:

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_yasno_systemd_account_preflight.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh

Private Telegram recipient-visible send/edit approval, release
signing and rollback authorization remain independent blockers.
Pro3EM hardware arrival does not block the EM-50-only update.
