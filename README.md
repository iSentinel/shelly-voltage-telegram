<p align="center">
  <a href="README_EN.md"><img src="https://img.shields.io/badge/🇬🇧_English-0A84FF?style=for-the-badge" alt="English"></a>
  <a href="README_RU.md"><img src="https://img.shields.io/badge/🇷🇺_Русский-3B82F6?style=for-the-badge" alt="Русский"></a>
  <a href="README_UA.md"><img src="https://img.shields.io/badge/🇺🇦_Українська-FFD700?style=for-the-badge" alt="Українська"></a>
</p>

<h1 align="center">⚡ Shelly Voltage Telegram</h1>
<p align="center"><strong>Local-first voltage, outage and power-quality monitoring for Shelly → Telegram.</strong></p>
<p align="center">Read-only by design · SQLite history · Prometheus metrics · Docker · Single-phase and three-phase ready</p>

<p align="center">
  <a href="https://github.com/iSentinel/shelly-voltage-telegram/releases/latest"><img src="https://img.shields.io/github/v/release/iSentinel/shelly-voltage-telegram?style=for-the-badge&color=7C3AED" alt="Latest release"></a>
  <a href="https://github.com/iSentinel/shelly-voltage-telegram/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/iSentinel/shelly-voltage-telegram/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/GHCR-amd64%20%7C%20arm64-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="GHCR multi-arch">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/iSentinel/shelly-voltage-telegram?style=for-the-badge&color=22C55E" alt="License"></a>
</p>

<p align="center">
  <img src="docs/assets/telegram-status.png" alt="Telegram voltage status message" width="430">
</p>
<p align="center"><em>Telegram voltage status message, cropped to the message boundaries.</em></p>

---

## Overview

**Shelly Voltage Telegram** reads voltage and frequency from supported Shelly devices, stores valid measurements, detects confirmed outages and keeps a Telegram status message current.

The project is intentionally **monitor-only**: it does not switch relays, change Shelly configuration or control electrical loads.

<table>
<tr><td>🔒 <b>Read-only Shelly RPC</b></td><td>⚡ Voltage + frequency + quality</td></tr>
<tr><td>🔌 <b>Outage / recovery journal</b></td><td>📱 Telegram status, alerts, charts, CSV</td></tr>
<tr><td>🗄️ <b>SQLite WAL history</b></td><td>📊 Health, readiness, metrics, dashboard</td></tr>
<tr><td>🐳 <b>Hardened Docker runtime</b></td><td>3️⃣ Pro 3EM / 3CT63 ready</td></tr>
</table>

## Status

- Current software candidate: **0.5.10-hw**
- Current release: **0.5.9-hw**
- Production deployment: **0.5.9-hw**, live on the physical Shelly Pro EM-50; the last fully hardware-accepted electrical-transition checkpoint remains `0.5.6-hw`.
- Hardware-accepted live checkpoint: **0.5.6-hw**
- Release `0.5.9-hw` adds the 15-minute stable-recovery status rotation on top of the event summaries, RU/UA/EN UI, diagnostics, and unstable-grid indication from `0.5.8-hw`.
- Candidate `0.5.10-hw` extends the read-only recovery acceptance CLI so a natural 15-minute stable-recovery rotation can be verified without controlling Shelly or writing to the live database.
- It also migrates a persisted pre-`0.5.9-hw` recovery event into the stability timer so an old recovery banner cannot remain indefinitely after an upgrade.
- Shelly Pro 3EM / Pro 3EM-3CT63 support is implemented and software-tested; physical three-phase validation follows after device installation.

## Supported devices

`Pro EM-50` · `EM Gen3` · `PM Mini Gen3` · `Plus PM Mini` · `Plus 1PM` · `1PM Gen3` · `1PM Gen4` · `1PM Mini Gen3` · `1PM Mini Gen4` · `Pro 3EM / Pro 3EM-3CT63`

The Shelly model is selected from the administrator `/settings` menu. Network addresses and credentials stay in deployment configuration and secret files.

## Architecture

```mermaid
flowchart LR
    S["Shelly meter"] -->|read-only RPC| B["Shelly Voltage Telegram"]
    P["Pro 3EM A/B/C"] -->|one EM.GetStatus| B
    B --> D[("SQLite WAL")]
    B --> T["Telegram"]
    B --> H["health / ready / metrics / dashboard"]
    B --> R["Backups + support bundle"]
```

## Voltage alert status notifications

Low- and high-voltage transitions replace the shared Telegram status message
instead of creating separate alert messages. The new status is published first;
the previous status is deleted only after the replacement succeeds.

The first abnormal status may notify normally. Further abnormal replacements
inside the notification cooldown are silent. Recovery is always silent, and
quiet hours still force every message to be silent. The cooldown is counted
from the last loud voltage status and is not extended by silent replacements.

Default cooldown is 5 minutes. Administrators can select `Off`, `3`, `5`, or
`10` minutes from `/settings`. The value is persisted in SQLite and applies
without a restart. Set `VOLTAGE_NOTIFICATION_COOLDOWN_MIN` to change the
deployment default.


## 0.5.8 candidate features

- Final voltage-event summary after the fixed edit window: start/end, duration,
  min/max/average voltage from SQLite, transition count, and time outside normal.
- Telegram UI language can be switched at runtime between **RU / UA / EN** and
  is persisted in SQLite.
- `/settings` includes explicit **Test loud**, **Test silent**, and **Test edit**
  checks that do not alter real voltage-alert cooldown state.
- An optional instability indicator appears after four confirmed low/high/normal
  transitions within 15 minutes; transition history survives a restart.

## Install in one command

```bash
curl -fsSL https://raw.githubusercontent.com/iSentinel/shelly-voltage-telegram/main/scripts/bootstrap.sh | sudo bash
```

Safe dry run:

```bash
curl -fsSL https://raw.githubusercontent.com/iSentinel/shelly-voltage-telegram/main/scripts/bootstrap.sh \
  | sudo bash -s -- --dry-run --non-interactive
```

Then configure the deployment:

```bash
sudo ./scripts/configure.sh
```

## Telegram commands

```text
/start       /help         /voltage      /status
/today       /minmax       /history      /outages
/quality     /chart        /export       /summary
/device      /health       /settings     /audit
/maintenance
```

## Container image

```bash
docker pull ghcr.io/isentinel/shelly-voltage-telegram:v0.5.9-hw
```

Published for `linux/amd64` and `linux/arm64` with SBOM/provenance metadata.

## Documentation

- 🇬🇧 [English documentation](README_EN.md)
- 🇷🇺 [Русская документация](README_RU.md)
- 🇺🇦 [Українська документація](README_UA.md)
- 🧭 [Roadmap](ROADMAP.md)
- 🧪 [Hardware validation](HARDWARE_VALIDATION.md)
- 📝 [Changelog](CHANGELOG.md)


---

<p align="center"><strong>Monitor power. Keep history. Get notified. Never control the load.</strong></p>

### Telegram status message

The edit window is configured in administrator `/settings`: 3, 5 or 10 minutes, with 10 minutes as the default (`VOLTAGE_STATUS_EDIT_WINDOW_MIN`). Its deadline starts with the first published voltage event. Low voltage, high voltage, recovery and repeated transitions within that deadline edit the same message, including after recovery. Edits do not extend the deadline. After expiry, the next transition publishes a new post, then unpins and deletes the previous post.

The audible notification cooldown is a separate setting. Editing a message is silent and does not change the last audible notification time. Quiet hours still apply to new posts. SQLite metadata preserves the setting, message ID and original deadline across restarts. Older databases use the configured default and reuse a recent stored voltage event when available; existing history is retained.
