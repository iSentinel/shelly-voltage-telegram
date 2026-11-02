<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/🇬🇧_English-0A84FF?style=for-the-badge" alt="English"></a>
  <a href="README_RU.md"><img src="https://img.shields.io/badge/🇷🇺_Русский-3B82F6?style=for-the-badge" alt="Русский"></a>
  <a href="README_UA.md"><img src="https://img.shields.io/badge/🇺🇦_Українська-FFD700?style=for-the-badge" alt="Українська"></a>
</p>

<h1 align="center">⚡ Shelly Voltage Telegram</h1>
<p align="center"><strong>Локальный мониторинг напряжения, отключений и качества сети для Shelly с Telegram-статусом и уведомлениями.</strong></p>

<p align="center">
  <a href="https://github.com/iSentinel/shelly-voltage-telegram/releases/latest"><img src="https://img.shields.io/github/v/release/iSentinel/shelly-voltage-telegram?style=for-the-badge&color=7C3AED" alt="Последний релиз"></a>
  <a href="https://github.com/iSentinel/shelly-voltage-telegram/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/iSentinel/shelly-voltage-telegram/ci.yml?branch=main&style=for-the-badge&label=CI" alt="CI"></a>
  <img src="https://img.shields.io/badge/GHCR-amd64%20%7C%20arm64-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="GHCR multi-arch">
  <img src="https://img.shields.io/badge/Python-3.11--3.13-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/iSentinel/shelly-voltage-telegram?style=for-the-badge&color=22C55E" alt="Лицензия"></a>
</p>

<p align="center">
  <img src="docs/assets/dashboard.jpg" alt="Живой dashboard Shelly Voltage Telegram" width="900">
</p>
<p align="center"><em>Реальный dashboard с рабочего стенда: L1 онлайн, L2/L3 готовы к будущим датчикам.</em></p>

---

## Что делает проект

**Shelly Voltage Telegram** регулярно считывает напряжение и частоту с поддерживаемых Shelly, сохраняет корректные измерения в SQLite, фиксирует подтверждённые отключения и поддерживает актуальное статус-сообщение в Telegram.

Проект работает только как **монитор**. Он не переключает реле, не меняет конфигурацию Shelly и не управляет электрической нагрузкой.

| | |
| --- | --- |
| 🔒 **Только чтение** | Используются только RPC получения состояния Shelly. |
| ⚡ **Напряжение и частота** | Текущие значения, min/max/avg, история и статистика качества. |
| 🔌 **Отключения** | Гистерезис, журнал отключений, длительность и восстановление. |
| 📱 **Telegram** | Стабильный статус, уведомления, графики, CSV и настройки администратора. |
| 📊 **Наблюдаемость** | `/health`, `/ready`, `/status.json`, `/metrics`, локальный dashboard. |
| 🗄️ **SQLite WAL** | История, runtime-настройки, аудит и outage-журнал. |
| 🐳 **Docker** | Непривилегированный контейнер, Compose, systemd и резервные копии. |
| 3️⃣ **Готовность к 3 фазам** | Нативный Pro 3EM/3CT63 и отдельные L2/L3 при необходимости. |

## Статус проекта

- Текущий публичный релиз: **`0.5.3-hw`**.
- Рабочий production уже использует **`0.5.3-hw`** и прошёл аппаратную проверку на Shelly Pro EM-50 после реального отключения и восстановления питания.
- В validation metadata этого релиза всё ещё сохранён более ранний публичный checkpoint `0.4.11-hw`; он будет приведён к текущему production-состоянию в следующем аудированном релизе.
- Поддержка Shelly Pro 3EM / Pro 3EM-3CT63 уже реализована и протестирована программно. Полная трёхфазная аппаратная проверка будет выполнена после установки устройства.

## Поддерживаемые Shelly

Модель можно выбрать через административное меню `/settings`. IP-адреса и учётные данные не редактируются через Telegram и остаются в `.env`/secret-файлах.

| Модель | Фазы | RPC только для чтения |
| --- | ---: | --- |
| Shelly Pro EM-50 | 1 | `EM1.GetStatus` |
| Shelly EM Gen3 | 1 | `EM1.GetStatus` |
| Shelly PM Mini Gen3 | 1 | `PM1.GetStatus` |
| Shelly Plus PM Mini | 1 | `PM1.GetStatus` |
| Shelly Plus 1PM | 1 | `Switch.GetStatus` |
| Shelly 1PM Gen3 | 1 | `Switch.GetStatus` |
| Shelly 1PM Gen4 | 1 | `Switch.GetStatus` |
| Shelly 1PM Mini Gen3 | 1 | `Switch.GetStatus` |
| Shelly 1PM Mini Gen4 | 1 | `Switch.GetStatus` |
| Shelly Pro 3EM / Pro 3EM-3CT63 | 3 | `EM.GetStatus` |

> **Важно:** PM Mini и 1PM включаются непосредственно в измеряемую цепь и должны использоваться только в пределах их электрических характеристик. Для ввода дома или трёх фаз обычно лучше CT-модель.

## Архитектура

```mermaid
flowchart LR
    S1[Shelly / L1] -->|read-only RPC| BOT[Shelly Voltage Telegram]
    S2[Опциональный L2] -->|read-only RPC| BOT
    S3[Опциональный L3] -->|read-only RPC| BOT
    P3[Pro 3EM / 3CT63] -->|A/B/C одним EM.GetStatus| BOT
    BOT --> DB[(SQLite WAL)]
    BOT --> TG[Telegram Bot API]
    BOT --> HTTP[/health · /ready · /metrics · /dashboard]
    BOT --> BK[Backup + support bundle]
```

Для Pro 3EM в режиме `triphase` один цикл опроса раскладывает **A → L1**, **B → L2**, **C → L3** с общей временной меткой.

## Быстрая установка

### Установка одной командой

```bash
curl -fsSL https://raw.githubusercontent.com/iSentinel/shelly-voltage-telegram/main/scripts/bootstrap.sh | sudo bash
```

### Безопасная проверка без установки

```bash
curl -fsSL https://raw.githubusercontent.com/iSentinel/shelly-voltage-telegram/main/scripts/bootstrap.sh \
  | sudo bash -s -- --dry-run --non-interactive
```

### Ручная установка

```bash
git clone https://github.com/iSentinel/shelly-voltage-telegram.git
cd shelly-voltage-telegram
sudo ./scripts/install.sh
```

При обновлении installer сохраняет существующие `.env`, secrets, SQLite и резервные копии.

## Первичная настройка

Запустите безопасный конфигуратор:

```bash
sudo ./scripts/configure.sh
```

Он настраивает модель и адрес Shelly, приватный Telegram control chat, отдельный status channel и список администраторов. Токен бота и пароли сохраняются в отдельных secret-файлах.

Пример:

```text
SHELLY_METER_TYPE=pro_em50
SHELLY_HOST=<адрес-shelly>
TELEGRAM_CHAT_ID=<приватный-control-chat>
TELEGRAM_STATUS_CHAT_ID=<status-channel>
TELEGRAM_ADMIN_USER_IDS=<telegram-user-id>
```

Для Pro 3EM / 3CT63:

```text
SHELLY_METER_TYPE=pro3em
SHELLY_HOST=<адрес-pro-3em>
```

## Telegram

Приватный чат управления и канал статуса разделены. Команды и административные настройки остаются в control chat, а постоянный статус можно публиковать в отдельный канал.

После подтверждённого восстановления питания бот удаляет старое outage-status сообщение и создаёт новое online-status сообщение. Новый `message_id` сохраняется в SQLite и используется для последующих обновлений.

Основные команды:

```text
/start       /help         /voltage      /status
/today       /minmax       /history      /outages
/quality     /chart        /export       /summary
/device      /health       /settings     /audit
/maintenance
```

Примеры:

```text
/chart L1 24
/export L1 24
/quality L1 24
/maintenance 60
/maintenance off
```

## Правила мониторинга

- Опрос по умолчанию: **каждые 10 секунд**.
- Offline подтверждается после **3 неудачных опросов**.
- Recovery подтверждается после **2 успешных опросов**.
- Недоступный Shelly не создаёт искусственные записи `0 V`.
- Срок хранения raw-данных настраивается; перед удалением создаются hourly aggregates.
- Quiet hours, low-voltage и warning/critical уровни настраиваются пользователем.
- Значения warning/critical являются настройками проекта, а не заявленными нормативными пределами.

## HTTP и Prometheus

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/ready
curl http://127.0.0.1:8080/status.json
curl http://127.0.0.1:8080/metrics
```

Dashboard:

```text
http://127.0.0.1:8080/dashboard
```

## Диагностика и резервные копии

```bash
# Проверка Shelly + SQLite + Telegram
docker exec shelly-voltage-bot python /app/app.py doctor

# Отчёт за 24 часа
./scripts/soak-report.py --hours 24 --json

# Online backup SQLite
./scripts/backup.sh

# Обезличенный support bundle
./scripts/support-bundle.sh

# Проверка rollback
sudo ./scripts/rollback.sh --dry-run
```

Все пользовательские скрипты поддерживают `-h` / `--help`.

## Безопасность

- Контейнер запускается от UID `10001`, не от root.
- Root filesystem контейнера read-only.
- Linux capabilities удалены.
- Включён `no-new-privileges`.
- Заданы PID/memory limits.
- HTTP-порт по умолчанию доступен только через localhost.
- Secrets имеют ограниченные права доступа.
- `.env`, `secrets/`, SQLite и локальные validation-файлы не попадают в публичные релизы.
- Support bundle удаляет секретные значения.
- Поддерживается HTTP Digest authentication Shelly.
- Shelly Cloud, MQTT, Home Assistant, Zigbee и ESP32 не требуются.

> Не публикуйте Shelly RPC напрямую в Интернет.

## Docker image

```bash
docker pull ghcr.io/isentinel/shelly-voltage-telegram:v0.5.3-hw
```

Образ выпускается для `linux/amd64` и `linux/arm64` с SBOM и provenance metadata.


## Roadmap

- аппаратная проверка **Shelly Pro 3EM-3CT63**;
- более информативный трёхфазный dashboard и графики;
- дальнейшее усиление installer/upgrade path;
- продолжение maintainability-аудита и модульного разделения без изменения monitor-only поведения.

Подробности: [`ROADMAP.md`](ROADMAP.md) и [`HARDWARE_VALIDATION.md`](HARDWARE_VALIDATION.md).

## Лицензия

См. [`LICENSE`](LICENSE).

---

<p align="center"><strong>Контролируем состояние сети. Храним историю. Уведомляем. Никогда не управляем нагрузкой.</strong></p>
