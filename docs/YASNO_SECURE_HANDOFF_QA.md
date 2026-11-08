# Secure YASNO producer-to-Telegram reader handoff (QA only)

The active Shelly bot uses UID:GID 10001:10001 and has a read-only root
filesystem. Its measured voltage and SQLite data must not change.

The YASNO source cache remains in a producer-only 0700 directory, with a
sealed JSON file 0600. An independent handoff validates pinned URL,
SHA-256 corruption checksum, capture time, Kyiv day and complete
applicable 48-slot forecast before making the recipient-readable copy.

Recipient: exporter-owned directory group 10001 mode 0750 and
exporter-owned file group 10001 mode 0640. The bot can read but cannot
modify either file or directory. Docker will mount this directory
read-only after explicit release approval. Do not put the source cache
inside the bot.

## QA commands, not production installation

Prepare an isolated test output:

    sudo install -d -m 0750 -o root -g 10001 /tmp/shelly-yasno-handoff-20261108

Dry-run, strictly without writes or network:

    python3 yasno_handoff.py --dry-run --source /tmp/shelly-yasno-qa-private/yasno.json --output /tmp/shelly-yasno-handoff-20261108/yasno.json --recipient-gid 10001 --group 10.1

Explicitly export only verified content:

    sudo python3 yasno_handoff.py --install --source /tmp/shelly-yasno-qa-private/yasno.json --output /tmp/shelly-yasno-handoff-20261108/yasno.json --recipient-gid 10001 --group 10.1

Read-only status check:

    python3 yasno_handoff.py --status --source /tmp/shelly-yasno-qa-private/yasno.json --output /tmp/shelly-yasno-handoff-20261108/yasno.json --recipient-gid 10001 --group 10.1

The exporter checks recipient owner, mode, link count and symlink paths,
takes a nonblocking exclusive lock, fsyncs a temporary 0640 file and
atomically replaces the destination. Identical data is a no-op.
Original retrieval timestamps are preserved; an older source cannot
replace a newer destination. Failure preserves the prior recipient.

There are no HTTP calls, Telegram sends, Shelly controls, container
restarts or production SQLite operations in this helper.

## Explicit future Docker Compose review

The uninstalled deploy/compose.yasno-reader.example.yml shows a
read-only bind into /run/yasno and keeps
TELEGRAM_YASNO_STATUS_ENABLED=0 by default.

Before deployment, create a secure host directory outside /tmp,
review its UID/GID mapping inside Proxmox LXC and Docker, install the
reader bind, and obtain permission to restart. A root-owned exporter
must be able to read the original producer file, which may be owned
by a separate shelly-yasno non-login account. Do not make the
source file world-readable or run the bot as root.

## QA tests

    PYTHONPATH=.:tests python3 -m unittest tests/test_yasno_handoff.py -v
    scripts/quality.sh
    PYTHONPATH=.:tests python3 -m unittest discover -s tests -q

An approved real private-recipient Telegram send/edit acceptance is
still mandatory. Unit and filesystem tests do not prove delivery.
