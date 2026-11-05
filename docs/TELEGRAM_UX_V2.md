# Telegram UX v2: compact power-status dashboard

Status: **P1 source implemented, not deployed**. PR #96 introduced the
localized compact status and read-only `/events` timeline. The final
RU/UK language audit is being validated separately. P2/P3 items remain
proposals pending testing and explicit release approval.
This document is independent of the firmware's physical release gate.

## Goals

Make the most important information visible within two seconds on a phone:
power state, current voltage and frequency, measurement freshness,
recent minimum/maximum, and the last confirmed event. Keep the public
channel quiet: one status message, edited or safely replaced according
to the existing policy, with no inline navigation buttons in the channel.

Do not show 0 V when the meter is unreachable. An offline Shelly is
evidence of lost device connectivity, **not** proof that mains is absent.
Do not show any measurement that is older than the displayed timestamp
as if it were current.

## Compact status (RU, fictional example)

```text
⚡ ЭЛЕКТРОСЕТЬ · ДОМ
🟢 Питание наблюдается

🔌 228.4 В    〰️ 50.00 Гц

📊 Сегодня    204.0 — 235.0 В
🕘 Измерено   22:25
✅ Связь со Shelly: есть
```

These numbers are examples only, not physical readings. For a real
outage, voltage should be shown as unavailable until a valid meter
sample returns. Do not infer an outage from a missing RPC response.

## Voltage warning and offline examples

```text
⚠️ ЭЛЕКТРОСЕТЬ · ПОНИЖЕННОЕ НАПРЯЖЕНИЕ
🟡 199.8 В · ниже настроенного порога
🕘 Измерено 22:15
📊 История: /history
```

```text
🔴 ЭЛЕКТРОСЕТЬ · НЕТ СВЯЗИ СО SHELLY
Текущее напряжение: неизвестно
Последнее корректное: 226.2 В · 21:48
Причина отключения связи пока не установлена
```

## Three phases after physical Pro 3EM / 3CT63 acceptance

```text
⚡ ЭЛЕКТРОСЕТЬ · 3 ФАЗЫ
🟢 Счётчик доступен
L1  229.8 В
L2  231.1 В
L3  228.7 В
Разница между фазами: 2.4 В
Обновлено: 22:25
```

The difference is max(phase voltages) - min(phase voltages); it is
**descriptive**, not a diagnosis of unsafe imbalance. Do not enable the
Pro 3EM backend until the installed meter and phase mapping are
physically validated. Treat any `EM.GetStatus.errors` or per-phase
error array as a meter fault rather than a valid reading.

## Private admin menu

Keep the channel status free of action buttons. In the private control
chat, show a small two-column keyboard:

- ⚡ Статус | 📈 График
- 🕘 События | 📊 Итоги
- ⚙️ Настройки | ❓ Помощь

Open secondary actions only when selected; keep the same
back-navigation pattern across submenus. Reuse existing slash commands
and remain compatible with RU/UA/EN.

## Implementation order

1. **P1** — A safe text renderer with RU/UA/EN and tests for online,
   warning, offline, recovering, missing data and fresh timestamps.
   Make compact mode optional at first; keep the existing default until
   explicit operator approval. Check Telegram HTML escaping if using
   rich text. Never emit secrets.
2. **P1** — Read-only 24-hour event timeline combining confirmed voltage
   transitions and outages. Show real timestamps, duration, phase and
   validity. Do not invent unavailable events or fabricate 0 V readings.
3. **P2 (weekly on-demand source implemented)** — The read-only
   `/week` command reports seven-day min/max/average, connection loss
   and observed versus unknown sample coverage. Opt-in weekly
   scheduled delivery is still pending explicit approval.
4. **P2** — Display preferences: compact/detailed, local timezone,
   opt-in schedules; do not change the existing 10-minute edit window
   or default pin=false.
5. **P2** — After hardware arrival, optional three-phase comparison with
   explicit `EM.GetStatus` health diagnostics, without power control.
6. **P3** — Telegram Mini App with responsive charts and optional
   per-phase views. Requires HTTPS, origin/authorization protection,
   server-side `initData` verification, and authorization tests;
   do not make it a production requirement.

## Release acceptance checklist

- All previously existing commands, RU/UA/EN text and control-chat
  authorization continue to work.
- Status delivery remains a single current message; no duplicate
  notification flood, no unwanted pin, and no stale alarm.
- Offline periods display "unknown" voltage, never 0 V.
- Monitors must not control Shelly, relays, or physical power.
- Private chat admin actions cannot appear in the public channel.
- Rendering must be bounded in length and escape untrusted input.
- Python 3.11/3.12/3.13 CI, Ruff/PEP8, existing unit tests, isolated
  candidate and a real Pro EM-50 hardware test all pass.
- The operator confirms an actual message in Telegram visually,
  separately from Bot API access and persisted message IDs.

Telegram Bot API reference: https://core.telegram.org/bots/api
Telegram Mini Apps reference: https://core.telegram.org/bots/webapps
