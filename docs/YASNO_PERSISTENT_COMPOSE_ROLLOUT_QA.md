# Secure YASNO opt-in preserved across restarts (0.5.15 candidate)

This is source-only QA; released production is still 0.5.14-hw.

The old start-compose.sh uses explicit Compose -f flags. Therefore
setting COMPOSE_FILE in .env does not preserve a separate YASNO overlay
during restart, pinned image updates or a new source installation.

The candidate reads ONLY an optional owner-controlled configuration at
/etc/shelly-voltage/yasno-reader.yaml. It must be a regular private file
with a protected parent, no symlinks or hardlinks. In production the
service runs as root and only a root-owned overlay is accepted.

- Without the overlay: original physical voltage status is unchanged.
- Normal service start: base + optional updater + YASNO last.
- Installer build: base + YASNO, build source; old updater is excluded.
- The feature is NOT enabled automatically by this code change.
- Safe example: deploy/compose.yasno-reader.enabled.example.yml.
- The example is never installed by the upgrade script.

Future owner-approved activation requires a separate maintenance window,
a fresh source+SQLite backup, a verified UID10001 read-only mount, and
a post-start check with yasno-docker-reader-preflight.py --phase active.
The current systemd producer and handoff timer do not require restarting
the bot or touching its SQLite history.

Run offline acceptance:

    PYTHONPATH=.:tests python3 -m unittest tests/test_start_compose_yasno.py -v
    python3 -m unittest discover -s tests -p 'test_*.py' -q
    bash scripts/quality.sh
    bash tests/test_release.sh
    bash tests/test_install_release.sh

Never interpret forecast schedules as live power or generate a 0-V
measurement during a physical outage. On error remove the opt-in file
in an owner-approved maintenance window; preserve the protected cache.
