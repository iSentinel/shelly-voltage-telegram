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

## Additional ancestor-path security regression (2026-11-09)

Three RED fixtures originally showed that an intermediate symlink,
a world-writable grandparent or a parent-traversal component could
bypass checks limited to the overlay file and immediate parent. All
three now PASS after validating every ancestor. The root-owned sticky
/tmp is recognized only for isolated fixtures, not a production
configuration location. Production requires a private /etc/shelly-voltage
directory and a root-owned, mode 0600 overlay.

Regression result: 14/14 Compose tests PASS, 1359/1359 Python tests
PASS, release and installer fixtures PASS, Ruff/quality PASS,
docker compose config -q PASS. Published release remains 0.5.14-hw;
candidate 0.5.15-hw is not authorized for publication.

## Forecast and measured-power contradiction (2026-11-09)

During the live group 10.1 YASNO scheduled blackout at 21:30
Kyiv, the installed 0.5.14-hw continued to receive positive Shelly
L1 measurements and reported online/healthy. The forecast label X
does not establish an actual blackout. Two additional regression
tests assert both contradiction directions through the actual
Telegram channel status builder: X cannot mark an online device
offline, and an all-clear schedule cannot bring an offline device
online or turn its last historical value into live voltage.
Both use verified local cache fixtures, with live network fetching
mocked out. Current QA: 1361/1361 Python and release/installer
fixture PASS, no production mutation.
