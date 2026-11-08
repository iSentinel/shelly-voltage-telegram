# YASNO bootstrap/live systemd preflight — QA only

This follow-up fixes a first-install deadlock: a service-account check
that requires YASNO cache files before any service has ever run would
prevent provisioning a clean installation.

There are now two explicit, read-only stages:

1. bootstrap: require a systemd-resolvable non-login producer identity,
   its 0700 directory, and the root:10001 reader directory mode 0750.
   Cache files may be absent until the first successful refresh, but
   any file already present MUST meet strict ownership and modes.
2. live: require everything from bootstrap PLUS a protected producer
   cache (0600) and root:10001 recipient cache (0640). Missing caches
   always FAIL. Default phase remains live for backward safety.

Commands (only checks; no user, directory or file is created):

    python3 scripts/yasno-systemd-account-preflight.py --help
    python3 scripts/yasno-systemd-account-preflight.py --dry-run
    python3 scripts/yasno-systemd-account-preflight.py --check --phase bootstrap
    python3 scripts/yasno-systemd-account-preflight.py --check --phase live

Actual Gateway acceptance used an EXISTING non-login account nobody
and separate /run QA directories: the first bootstrap check PASSed
with no files, the same live check failed as expected; synthetic,
current-day YASNO cache was then created by the private producer
and exported using yasno_handoff.py. The subsequent live check PASSed.
No systemd unit was installed or activated during this specific
bootstrap test.

Prior OnSuccess live integration QA also passed in LXC110 using
temporary /run/systemd/system units: successful producer service
automatically launched the protected exporter, a deliberate failing
producer did NOT launch an exporter, and UID10001 Docker read the
result without network or write access. Temporary units were removed.

Before an approved deployment, the actual dedicated shelly-yasno
account must be explicitly provisioned; using nobody is only a
temporary QA stand-in. Initial setup passes bootstrap, then performs
one independently reviewed successful refresh+handoff. Only then
should the live check and approved full release promotion proceed.

This script does not check Telegram delivery, start services, alter
SQLite, send messages or provide release authorization. The required
human-visible private Telegram check and signed release/rollback
approval remain separate gates.

Regression:

    PYTHONPATH=.:tests python3 -W default -m unittest tests/test_yasno_systemd_account_preflight.py -v
    PYTHONPATH=.:tests python3 -W default -m unittest discover -s tests -q
    scripts/quality.sh
