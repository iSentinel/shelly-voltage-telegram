# DTEK published outage schedule (opt-in candidate)

This draft feature is separate from authoritative Shelly voltage readings.
It does not update outage records, notify subscribers, or alter live status.

## Configuration and access

- DTEK_REGION: k Kyiv, kr Kyiv oblast, dn Dnipro oblast,
  o Odesa oblast, d Donetsk oblast
- DTEK_GROUP: published queue number such as 1.2, without GPV
- DTEK_CACHE_FILE: default /data/dtek_schedule.json

The Telegram /schedule command works **only in the administrator's
private chat**, and only after the optional configuration is supplied.
It never sends its result into a public Telegram channel. No address is
sent to DTEK: only regional public pages are accessed.

The production Python app **does not perform any browser or HTTP fetch**.
It reads an independently collected JSON cache, verifying region, schema
version, correct local Kyiv calendar date, completeness of all 24 hours
for the requested group, and maximum age of two hours. It rejects
symlinks and oversized snapshots. Missing, old or malformed data is
shown as **unknown**, never as "there are no outages".

## Optional isolated one-shot collector

The collector is scripts/dtek-collect.py. It uses Python Playwright
only when run independently; it is not part of production requirements.
It needs Chromium installed in its *own* runtime and outbound access to
a DTEK regional /ua/shutdowns page. Do **not** install Playwright into
the monitoring bot or use the live bot's Telegram secrets.

Usage from a disposable collector environment:

    python scripts/dtek-collect.py --help
    python scripts/dtek-collect.py --region k --output /data/dtek_schedule.json --dry-run
    python scripts/dtek-collect.py --region k --output /data/dtek_schedule.json

Use /var/lib/shelly-voltage/dtek_schedule.json on the LXC host for
the /data/... Docker bind mount. The collector writes owner-only
permissions (0600) and should run with the same numeric UID that
the main app uses (**10001**); otherwise the app cannot read the
cache. Run the collector as a separate short-lived process with limited
permissions and host-specific scheduling, not on the voltage polling
thread. Automatic scheduling and live-source acceptance remain
explicit future work; no recurring collection job is deployed yet.

**No real schedule is accepted until the browser flow is tested on the
current DTEK page.** A plain HTTP probe returned only 212 bytes and no
fact payload for Kyiv and Kyiv oblast on 2026-11-06. Browser
collection may also fail and must fail closed without replacing a
previously valid cache.

Source inspiration (reviewed, not bundled as a separate bot):

- https://github.com/mr-devboy/dtek-schedule
- https://github.com/mr-devboy/dtek-monitor
- https://github.com/mr-devboy/power-monitor
- https://github.com/antondziuin/DtekSchedulePowerOutSenderBotHelper
