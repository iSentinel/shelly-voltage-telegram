"""Small Telegram UI translation helpers."""

from __future__ import annotations

LANGUAGES = ("ru", "uk", "en")

_TEXT = {
    "ru": {
        "cmd_start": "Открыть меню",
        "menu_title": "⚡ Главное меню\nВыберите нужный раздел:",
        "menu_status": "⚡ Статус",
        "menu_settings": "⚙️ Настройки",
        "menu_today": "📊 Сегодня",
        "menu_outages": "🔌 Отключения",
        "menu_chart": "📉 График",
        "menu_help": "ℹ️ Помощь",
        "menu_apartments": "🏢 Квартиры и фазы",
        "menu_updates": "🔄 Обновления",
        "cmd_help": "Показать команды",
        "cmd_voltage": "Текущее напряжение",
        "cmd_outages": "Отключения за сегодня",
        "cmd_status": "Полный статус",
        "cmd_today": "Статистика за сегодня",
        "cmd_minmax": "Минимум и максимум",
        "cmd_history": "Последние измерения",
        "cmd_health": "Состояние сервиса",
        "cmd_settings": "Настройки уведомлений",
        "cmd_chart": "График напряжения",
        "cmd_export": "Экспорт CSV",
        "cmd_quality": "Качество сети",
        "cmd_events": "События за 24 часа",
        "cmd_summary": "Сводка за сутки",
        "cmd_maintenance": "Режим обслуживания",
        "cmd_audit": "Журнал настроек",
        "cmd_device": "Состояние Shelly",
        "cmd_updates": "Проверить GitHub Releases",
        "updates_installed": "Установленная версия: {version}",
        "updates_latest": "Последний стабильный релиз GitHub: {version}",
        "updates_available": "Обновление доступно: {answer}",
        "updates_yes": "да",
        "updates_no": "нет",
        "updates_auto_disabled": "Автоматическая установка: выключена",
        "updates_manual_required": (
            "Для установки требуется подтверждение администратора на сервере."
        ),
        "updates_check_failed": (
            "Не удалось проверить опубликованный релиз GitHub."
        ),
        "phase_unconfigured": "⚪ {phase}: не настроена",
        "phase_offline": "🔴 {phase}: недоступна",
        "phase_starting": "запуск",
        "phase_no_data": "нет данных",
        "phase_recovering": "🟡 {phase}: {voltage:.1f} V (восстанавливается)",
        "network_title": "⚡ Напряжение сети · {phases}",
        "no_measurements": "{phase}: нет корректных измерений.",
        "l1_today": "{phase} сегодня: {minimum}–{maximum} В, ср. {average} В",
        "power_recovered": (
            "✅ Питание восстановлено · перерыв связи {duration}"
        ),
        "last_l1": "🕐 Последние данные {phase}: {stamp}",
        "event_low": "🚨 Низкое напряжение сети",
        "event_warning_low": "⚠️ Пониженное напряжение",
        "event_critical_low": "🚨 Критически пониженное напряжение",
        "event_warning_high": "⚠️ Повышенное напряжение",
        "event_critical_high": "🚨 Критически повышенное напряжение",
        "event_recovered": "✅ Напряжение восстановилось",
        "unstable": "⚠️ Нестабильное напряжение: {count} переходов за 15 мин",
        "episode_title": "📋 Итог события",
        "episode_period": "🕐 {start}–{end} · {duration}",
        "episode_stats": "⚡ {minimum}–{maximum} В · ср. {average} В",
        "episode_transitions": "🔄 Переходов: {count}",
        "episode_abnormal": "⚠️ Вне нормы: {duration}",
        "settings_title": "⚙️ Настройки бота",
        "settings_choose": "Выберите раздел:",
        "settings_section_device": "📟 Устройство",
        "settings_section_voltage": "⚡ Напряжение",
        "settings_section_notifications": "🔔 Уведомления",
        "settings_section_schedule": "🕒 Расписание",
        "settings_section_service": "🧰 Сервис",
        "settings_section_meter": "📟 Модель Shelly",
        "settings_section_language": "🌐 Язык",
        "settings_section_quiet": "🌙 Ночной режим",
        "settings_section_cooldown": "🔔 Пауза между уведомлениями",
        "settings_section_edit": "✏️ Окно редактирования",
        "settings_section_tests": "🧪 Тесты уведомлений",
        "settings_section_low": "⬇️ Нижние пороги",
        "settings_section_high": "⬆️ Верхние пороги",
        "settings_section_legacy_low": "🚨 Оповещение о низком напряжении",
        "settings_section_dwell": "⏳ Задержки",
        "settings_section_summary_time": "🕒 Время сводки",
        "settings_back": "⬅️ Назад",
        "settings_change_meter": "📟 Изменить модель",
        "settings_reset_confirm": (
            "⚠️ Сбросить runtime-настройки к значениям .env?"
        ),
        "settings_reset_yes": "✅ Сбросить",
        "settings_cancel": "❌ Отмена",
        "settings_module": "📟 Модуль: {value}",
        "settings_language": "🌐 Язык: {value}",
        "settings_normal": "🔕 Обычные сообщения: {value}",
        "settings_quiet": "🌙 Ночной режим: {state} {hours}",
        "settings_cooldown": "🔔 Пауза между предупреждениями: {value}",
        "settings_edit": "✏️ Окно редактирования: {value}",
        "settings_low": "🚨 Низкое напряжение: {state} < {voltage:g} V",
        "settings_recovery": "✅ Сброс тревоги: ≥ {voltage:g} V",
        "settings_levels": "⚠️ Уровни: {state} {range}",
        "settings_maintenance": "🛠 Обслуживание: {state}",
        "settings_lines": "🔌 {phases}",
        "settings_phases": "🔌 Фазы: L1 вкл. · L2 {l2} · L3 {l3}",
        "settings_outage": "🔌 Уведомления о потере связи: {state}",
        "settings_summary": "📅 Сводка: {state} {time}",
        "settings_pin": "📌 Закрепление статуса: {state}",
        "settings_dwell": (
            "⏳ Подтверждение: {dwell:g} с / восстановление {recovery:g} с"
        ),
        "settings_instability": (
            "⚡ Индикатор нестабильности: {state} · 4/15 мин"
        ),
        "silent": "тихие",
        "audible": "со звуком",
        "minutes": "{value} мин",
        "button_silent": "🔕 Тихие ON/OFF",
        "button_quiet": "🌙 Ночь ON/OFF",
        "button_edit": "✏️ Окно {value} мин",
        "button_start_dec": "◀ Начало -1ч",
        "button_start_inc": "Начало +1ч ▶",
        "button_end_dec": "◀ Конец -1ч",
        "button_end_inc": "Конец +1ч ▶",
        "button_low": "🚨 Alert ON/OFF",
        "button_threshold": "Порог {delta}",
        "button_recovery": "Recovery {delta}",
        "button_levels": "⚠️ Уровни ON/OFF",
        "button_outage": "🔌 Потеря связи: вкл./выкл.",
        "button_summary": "📅 Сводка: вкл./выкл.",
        "button_pin": "📌 Закрепление: вкл./выкл.",
        "button_instability": "⚡ Нестабильность ON/OFF",
        "button_reset": "↩️ Сбросить к .env",
        "button_test_loud": "🔔 Проверить звук",
        "button_test_silent": "🔕 Проверить тихое сообщение",
        "button_test_edit": "✏️ Проверить редактирование",
        "only_admin": "Только администратор",
        "saved": "Сохранено",
        "settings_error": "Ошибка настройки",
        "phase_sensor_offline": "⚠️ {phase}: датчик недоступен",
        "phase_sensor_recovered": (
            "✅ {phase}: питание/датчик восстановлены\n⏱ {duration}"
        ),
        "availability_offline": (
            "⚠️ Shelly недоступен. Последнее корректное измерение сохранено."
        ),
        "availability_recovered": (
            "✅ Shelly восстановлен\n⏱ Длительность: {duration}"
        ),
        "callback_today": "Сегодня: {value}",
        "callback_outages": "Отключения: {value}",
        "callback_updated": "Статус обновлён",
        "device_title": "🔧 Состояние Shelly",
        "device_unconfigured": "{phase}: не настроена",
        "phase_invalid": "Фаза должна быть L1, L2 или L3",
        "phase_not_configured": "{phase}: датчик ещё не настроен",
        "chart_caption": "{phase} · {hours}ч · {count} точек",
        "export_caption": "{phase} · {hours}ч · {count} строк",
        "command_error": "Ошибка: {error}",
        "maintenance_admin_only": (
            "Только администратор может менять режим обслуживания."
        ),
        "maintenance_status": "🛠 Обслуживание: {value}",
        "maintenance_usage": (
            "Использование: /maintenance 60 или /maintenance off"
        ),
        "summary_phase": (
            "{phase}: {minimum}–{maximum} V, ср. {average} V; "
            "потерь связи: {count}, {duration}"
        ),
        "test_loud": "🔔 Тест громкого уведомления",
        "test_silent": "🔕 Тест тихого уведомления",
        "test_edit_start": "✏️ Проверка редактирования · шаг 1/2",
        "test_edit_done": "✅ Редактирование Telegram работает · шаг 2/2",
        "help": (
            "Команды: /voltage /status /today /minmax /history /outages "
            "/health /settings /chart /export /quality /summary /events /week "
            "/maintenance /audit /device /updates"
        ),
        "service_ok": "✅ Сервис работает; {phases}\n{backup}",
        "no_current": "Нет актуальных данных измерений.",
        "phases_title": "⚡ Фазы",
        "history_title": "🗂 Последние измерения {phase}",
        "outages_title": "🔌 Последние отключения {phase}",
        "outage_now": "идёт сейчас",
        "no_outages": "{phase}: отключений нет",
        "outage_count_today": "🔌 Подтверждённых отключений сегодня: {count}",
        "outage_unavailable": "⏱ Подтверждённая недоступность: {duration}",
        "outage_boundaries": (
            "ℹ️ Границы: {failures} ошибок / {successes} успешных опросов"
        ),
        "outage_interpretation": (
            "ℹ️ Зафиксирована недоступность Shelly. Это не всегда означает "
            "отключение электричества (возможна проблема сети или IP)."
        ),
        "summary_title": "📅 Суточная сводка",
        "summary_unconfigured": "⚪ {phase}: не настроена",
        "quality_title": "📊 Качество {phase} за {hours}ч",
        "quality_samples": "Измерений: {value}",
        "quality_below": "Ниже {voltage:g} В: {value}",
        "quality_above": "Выше {voltage:g} В: {value}",
        "quality_bad": "Вне нормы: {value:.2f}%",
        "audit_title": "🧾 Последние изменения настроек",
        "status_online": "🟢 Связь с Shelly установлена",
        "status_recovering": "🟡 Shelly восстанавливает соединение",
        "status_offline": (
            "🔴 Нет связи с Shelly · напряжение сейчас неизвестно"
        ),
        "status_starting": "🟡 Ожидаем измерений Shelly",
        "status_frequency": "〰️ Частота: {value:.2f} Гц",
        "unit_voltage": "V",
        "unit_frequency": "Гц",
        "state_on": "вкл.",
        "state_off": "выкл.",
        "backup_ok": (
            "💾 Резервная копия: {stamp}, {size} КиБ · SQLite: {integrity}"
        ),
        "device_details": (
            "{phase}: {model} · прошивка {firmware} · "
            "работает {uptime} с · часы {skew}"
        ),
        "device_offline": "{phase}: нет связи ({error})",
        "menu_events": "🕘 События",
        "events_title": "🕘 События за 24 часа",
        "events_empty": "Подтверждённых событий за 24 часа нет",
        "events_warning": "⚠️ {phase}: {state} · {time}",
        "events_connection": "🔌 {phase}: потеря связи · {time}",
        "menu_summary": "📅 Сводка",
        "events_notice": (
            "ℹ️ Перерывы связи — за 24 ч; переходы напряжения "
            "— только из сохранённой истории (обычно 1 ч)."
        ),
        "events_state_low": "пониженное напряжение",
        "events_state_high": "повышенное напряжение",
        "events_state_normal": "напряжение восстановилось",
        "device_unknown": "нет данных",
        "events_reconnected": "✅ {phase}: связь восстановлена · {time}",
        "unit_seconds": "с",
        "button_cooldown_off": "🔔 Пауза: выкл.",
        "button_cooldown_min": "{value} мин",
        "cmd_week": "Статистика за 7 дней",
        "weekly_title": "📅 Качество электросети · 7 дней",
        "weekly_phase": (
            "{phase}: {minimum}–{maximum} V, ср. "
            "{average} V · измерений {count}"
        ),
        "weekly_coverage": (
            "✅ Измеренные интервалы: {observed} · без данных: {unknown}"
        ),
        "weekly_threshold": (
            "⚠️ Оценка времени ниже порога: {below}; выше: {above}"
        ),
        "weekly_connection": (
            "🔌 Потерь связи: {count} · длительность {duration}"
        ),
        "weekly_notice": (
            "ℹ️ Только интервалы между измерениями до "
            "30 с учитываются как наблюдаемые. "
            "Остальное время неизвестно. Нет связи не "
            "означает отсутствия питания."
        ),
        "compact_preview_title": "⚡ Краткий статус · {phases}",
        "compact_preview_note": (
            "ℹ️ Предпросмотр только для администратора. "
            "Сообщение канала не изменено."
        ),
        "phase_cached": (
            "🟡 {phase}: {voltage:.1f} {unit} · сохранено (ожидаем опрос)"
        ),
        "backup_missing": "💾 Резервная копия: отсутствует",
    },
    "uk": {
        "cmd_start": "Відкрити меню",
        "menu_title": "⚡ Головне меню\nВиберіть потрібний розділ:",
        "menu_status": "⚡ Статус",
        "menu_settings": "⚙️ Налаштування",
        "menu_today": "📊 Сьогодні",
        "menu_outages": "🔌 Відключення",
        "menu_chart": "📉 Графік",
        "menu_help": "ℹ️ Довідка",
        "menu_apartments": "🏢 Квартири та фази",
        "menu_updates": "🔄 Оновлення",
        "cmd_help": "Показати команди",
        "cmd_voltage": "Поточна напруга",
        "cmd_outages": "Відключення за сьогодні",
        "cmd_status": "Повний статус",
        "cmd_today": "Статистика за сьогодні",
        "cmd_minmax": "Мінімум і максимум",
        "cmd_history": "Останні вимірювання",
        "cmd_health": "Стан сервісу",
        "cmd_settings": "Налаштування сповіщень",
        "cmd_chart": "Графік напруги",
        "cmd_export": "Експорт CSV",
        "cmd_quality": "Якість мережі",
        "cmd_events": "Події за 24 години",
        "cmd_summary": "Добове зведення",
        "cmd_maintenance": "Режим обслуговування",
        "cmd_audit": "Журнал налаштувань",
        "cmd_device": "Стан Shelly",
        "cmd_updates": "Перевірити GitHub Releases",
        "updates_installed": "Встановлена версія: {version}",
        "updates_latest": "Останній стабільний реліз GitHub: {version}",
        "updates_available": "Оновлення доступне: {answer}",
        "updates_yes": "так",
        "updates_no": "ні",
        "updates_auto_disabled": "Автоматичне встановлення: вимкнено",
        "updates_manual_required": (
            "Для встановлення потрібне підтвердження "
            "адміністратора на сервері."
        ),
        "updates_check_failed": (
            "Не вдалося перевірити опублікований реліз GitHub."
        ),
        "phase_unconfigured": "⚪ {phase}: не налаштована",
        "phase_offline": "🔴 {phase}: недоступна",
        "phase_starting": "запуск",
        "phase_no_data": "немає даних",
        "phase_recovering": "🟡 {phase}: {voltage:.1f} В (відновлюється)",
        "network_title": "⚡ Напруга мережі · {phases}",
        "no_measurements": "{phase}: немає коректних вимірювань.",
        "l1_today": (
            "{phase} сьогодні: {minimum}–{maximum} В, сер. {average} В"
        ),
        "power_recovered": (
            "✅ Живлення відновлено · перерва зв'язку {duration}"
        ),
        "last_l1": "🕐 Останні дані {phase}: {stamp}",
        "event_low": "🚨 Низька напруга мережі",
        "event_warning_low": "⚠️ Знижена напруга",
        "event_critical_low": "🚨 Критично знижена напруга",
        "event_warning_high": "⚠️ Підвищена напруга",
        "event_critical_high": "🚨 Критично підвищена напруга",
        "event_recovered": "✅ Напруга відновилася",
        "unstable": "⚠️ Нестабільна напруга: {count} переходів за 15 хв",
        "episode_title": "📋 Підсумок події",
        "episode_period": "🕐 {start}–{end} · {duration}",
        "episode_stats": "⚡ {minimum}–{maximum} В · сер. {average} В",
        "episode_transitions": "🔄 Переходів: {count}",
        "episode_abnormal": "⚠️ Поза нормою: {duration}",
        "settings_title": "⚙️ Налаштування бота",
        "settings_choose": "Оберіть розділ:",
        "settings_section_device": "📟 Пристрій",
        "settings_section_voltage": "⚡ Напруга",
        "settings_section_notifications": "🔔 Сповіщення",
        "settings_section_schedule": "🕒 Розклад",
        "settings_section_service": "🧰 Сервіс",
        "settings_section_meter": "📟 Модель Shelly",
        "settings_section_language": "🌐 Мова",
        "settings_section_quiet": "🌙 Нічний режим",
        "settings_section_cooldown": "🔔 Пауза між сповіщеннями",
        "settings_section_edit": "✏️ Вікно редагування",
        "settings_section_tests": "🧪 Тести сповіщень",
        "settings_section_low": "⬇️ Нижні пороги",
        "settings_section_high": "⬆️ Верхні пороги",
        "settings_section_legacy_low": "🚨 Попередження про низьку напругу",
        "settings_section_dwell": "⏳ Затримки",
        "settings_section_summary_time": "🕒 Час зведення",
        "settings_back": "⬅️ Назад",
        "settings_change_meter": "📟 Змінити модель",
        "settings_reset_confirm": (
            "⚠️ Скинути поточні налаштування до значень .env?"
        ),
        "settings_reset_yes": "✅ Скинути",
        "settings_cancel": "❌ Скасувати",
        "settings_module": "📟 Модуль: {value}",
        "settings_language": "🌐 Мова: {value}",
        "settings_normal": "🔕 Звичайні повідомлення: {value}",
        "settings_quiet": "🌙 Нічний режим: {state} {hours}",
        "settings_cooldown": "🔔 Пауза між попередженнями: {value}",
        "settings_edit": "✏️ Вікно редагування: {value}",
        "settings_low": "🚨 Низька напруга: {state} < {voltage:g} В",
        "settings_recovery": "✅ Поріг відновлення: ≥ {voltage:g} В",
        "settings_levels": "⚠️ Рівні: {state} {range}",
        "settings_maintenance": "🛠 Обслуговування: {state}",
        "settings_lines": "🔌 {phases}",
        "settings_phases": "🔌 Фази: L1 увімкн. · L2 {l2} · L3 {l3}",
        "settings_outage": "🔌 Сповіщення про втрату зв'язку: {state}",
        "settings_summary": "📅 Зведення: {state} {time}",
        "settings_pin": "📌 Закріплення статусу: {state}",
        "settings_dwell": (
            "⏳ Підтвердження: {dwell:g} с / відновлення {recovery:g} с"
        ),
        "settings_instability": (
            "⚡ Індикатор нестабільності: {state} · 4/15 хв"
        ),
        "silent": "тихі",
        "audible": "зі звуком",
        "minutes": "{value} хв",
        "button_silent": "🔕 Тихі сповіщення: змінити",
        "button_quiet": "🌙 Нічний режим: змінити",
        "button_edit": "✏️ Вікно {value} хв",
        "button_start_dec": "◀ Початок -1г",
        "button_start_inc": "Початок +1г ▶",
        "button_end_dec": "◀ Кінець -1г",
        "button_end_inc": "Кінець +1г ▶",
        "button_low": "🚨 Попередження: змінити",
        "button_threshold": "Поріг {delta}",
        "button_recovery": "Поріг відновлення {delta}",
        "button_levels": "⚠️ Пороги: змінити",
        "button_outage": "🔌 Втрата зв'язку: змінити",
        "button_summary": "📅 Зведення: змінити",
        "button_pin": "📌 Закріплення: змінити",
        "button_instability": "⚡ Нестабільність: змінити",
        "button_reset": "↩️ Скинути до .env",
        "button_test_loud": "🔔 Перевірити звук",
        "button_test_silent": "🔕 Перевірити тихе сповіщення",
        "button_test_edit": "✏️ Перевірити редагування",
        "only_admin": "Лише адміністратор",
        "saved": "Збережено",
        "settings_error": "Помилка налаштування",
        "test_loud": "🔔 Тест гучного сповіщення",
        "test_silent": "🔕 Тест тихого сповіщення",
        "test_edit_start": "✏️ Перевірка редагування · крок 1/2",
        "test_edit_done": "✅ Редагування Telegram працює · крок 2/2",
        "help": (
            "Команди: /voltage /status /today /minmax /history /outages "
            "/health /settings /chart /export /quality /summary /events /week "
            "/maintenance /audit /device /updates"
        ),
        "service_ok": "✅ Сервіс працює; {phases}\n{backup}",
        "no_current": "Немає актуальних даних вимірювань.",
        "phases_title": "⚡ Фази",
        "history_title": "🗂 Останні вимірювання {phase}",
        "outages_title": "🔌 Останні відключення {phase}",
        "outage_now": "триває зараз",
        "no_outages": "{phase}: відключень немає",
        "outage_count_today": "🔌 Підтверджених відключень сьогодні: {count}",
        "outage_unavailable": "⏱ Підтверджена недоступність: {duration}",
        "outage_boundaries": (
            "ℹ️ Межі: {failures} помилок / {successes} успішних опитувань"
        ),
        "outage_interpretation": (
            "ℹ️ Зафіксовано недоступність Shelly. Це не завжди означає "
            "зникнення електрики (можлива проблема мережі або IP)."
        ),
        "summary_title": "📅 Добове зведення",
        "summary_unconfigured": "⚪ {phase}: не налаштована",
        "quality_title": "📊 Якість {phase} за {hours}г",
        "quality_samples": "Вимірювань: {value}",
        "quality_below": "Нижче {voltage:g} В: {value}",
        "quality_above": "Вище {voltage:g} В: {value}",
        "quality_bad": "Поза нормою: {value:.2f}%",
        "audit_title": "🧾 Останні зміни налаштувань",
        "phase_sensor_offline": "⚠️ {phase}: датчик недоступний",
        "phase_sensor_recovered": (
            "✅ {phase}: зв'язок із датчиком відновлено · {duration}"
        ),
        "availability_offline": (
            "⚠️ Немає зв'язку із Shelly. Останнє вимірювання збережено."
        ),
        "availability_recovered": (
            "✅ Зв'язок із Shelly відновлено · {duration}"
        ),
        "callback_today": "Сьогодні: {value}",
        "callback_outages": "Перерви зв'язку: {value}",
        "callback_updated": "Стан оновлено",
        "device_title": "🔧 Стан Shelly",
        "device_unconfigured": "{phase}: не налаштована",
        "phase_invalid": "Фаза має бути L1, L2 або L3",
        "phase_not_configured": "{phase}: датчик ще не налаштовано",
        "chart_caption": "{phase} · {hours} год · {count} точок",
        "export_caption": "{phase} · {hours} год · {count} рядків",
        "command_error": "Помилка: {error}",
        "maintenance_admin_only": (
            "Лише адміністратор може змінювати режим обслуговування"
        ),
        "maintenance_status": "🛠 Обслуговування: {value}",
        "maintenance_usage": (
            "Використання: /maintenance 60 або /maintenance off"
        ),
        "summary_phase": (
            "{phase}: {minimum}–{maximum} В, сер. {average} "
            "В; перерв зв'язку: {count}, {duration}"
        ),
        "status_online": "🟢 Зв'язок із Shelly є",
        "status_recovering": "🟡 Shelly відновлює з'єднання",
        "status_offline": (
            "🔴 Немає зв'язку із Shelly · поточна напруга невідома"
        ),
        "status_starting": "🟡 Очікуємо вимірювань Shelly",
        "status_frequency": "〰️ Частота: {value:.2f} Гц",
        "unit_voltage": "В",
        "unit_frequency": "Гц",
        "state_on": "увімкн.",
        "state_off": "вимкн.",
        "backup_ok": (
            "💾 Резервна копія: {stamp}, {size} КіБ · SQLite: {integrity}"
        ),
        "device_details": (
            "{phase}: {model} · прошивка {firmware} · працює "
            "{uptime} с · годинник {skew}"
        ),
        "device_offline": "{phase}: немає зв'язку ({error})",
        "menu_events": "🕘 Події",
        "events_title": "🕘 Події за 24 години",
        "events_empty": "Підтверджених подій за 24 години немає",
        "events_warning": "⚠️ {phase}: {state} · {time}",
        "events_connection": "🔌 {phase}: втрата зв'язку · {time}",
        "menu_summary": "📅 Зведення",
        "events_notice": (
            "ℹ️ Перерви зв'язку — за 24 год; зміни напруги — "
            "лише зі збереженої історії (зазвичай 1 год)."
        ),
        "events_state_low": "знижена напруга",
        "events_state_high": "підвищена напруга",
        "events_state_normal": "напруга відновилася",
        "device_unknown": "немає даних",
        "events_reconnected": "✅ {phase}: зв'язок відновлено · {time}",
        "unit_seconds": "с",
        "button_cooldown_off": "🔔 Пауза: вимкн.",
        "button_cooldown_min": "{value} хв",
        "cmd_week": "Статистика за 7 днів",
        "weekly_title": "📅 Якість електромережі · 7 днів",
        "weekly_phase": (
            "{phase}: {minimum}–{maximum} В, сер. "
            "{average} В · вимірювань {count}"
        ),
        "weekly_coverage": (
            "✅ Виміряні інтервали: {observed} · без даних: {unknown}"
        ),
        "weekly_threshold": (
            "⚠️ Оцінка часу нижче порога: {below}; вище: {above}"
        ),
        "weekly_connection": (
            "🔌 Втрат зв'язку: {count} · тривалість {duration}"
        ),
        "weekly_notice": (
            "ℹ️ Лише проміжки між вимірами до 30 с "
            "враховуються як спостережені. Решта часу "
            "невідома. Втрата зв'язку не доводить "
            "відсутність живлення."
        ),
        "compact_preview_title": "⚡ Короткий статус · {phases}",
        "compact_preview_note": (
            "ℹ️ Попередній перегляд лише для адміністратора. "
            "Повідомлення в каналі не змінено."
        ),
        "phase_cached": (
            "🟡 {phase}: {voltage:.1f} {unit} · збережено (очікуємо вимір)"
        ),
        "backup_missing": "💾 Резервна копія: відсутня",
    },
    "en": {
        "cmd_start": "Open menu",
        "menu_title": "⚡ Main menu\nChoose a section:",
        "menu_status": "⚡ Status",
        "menu_settings": "⚙️ Settings",
        "menu_today": "📊 Today",
        "menu_outages": "🔌 Outages",
        "menu_chart": "📉 Chart",
        "menu_help": "ℹ️ Help",
        "menu_apartments": "🏢 Apartments and phases",
        "menu_updates": "🔄 Updates",
        "cmd_help": "Show commands",
        "cmd_voltage": "Current voltage",
        "cmd_outages": "Today's outages",
        "cmd_status": "Full status",
        "cmd_today": "Today's statistics",
        "cmd_minmax": "Minimum and maximum",
        "cmd_history": "Recent measurements",
        "cmd_health": "Service health",
        "cmd_settings": "Notification settings",
        "cmd_chart": "Voltage chart",
        "cmd_export": "Export CSV",
        "cmd_quality": "Power quality",
        "cmd_events": "Last 24 hours of events",
        "cmd_summary": "Daily summary",
        "cmd_maintenance": "Maintenance mode",
        "cmd_audit": "Settings audit",
        "cmd_device": "Shelly health",
        "cmd_updates": "Check GitHub releases",
        "updates_installed": "Installed: {version}",
        "updates_latest": "Latest stable GitHub release: {version}",
        "updates_available": "Update available: {answer}",
        "updates_yes": "yes",
        "updates_no": "no",
        "updates_auto_disabled": "Automatic installation: disabled",
        "updates_manual_required": (
            "Installation requires explicit administrator "
            "approval on the host."
        ),
        "updates_check_failed": "GitHub release check unavailable.",
        "phase_unconfigured": "⚪ {phase}: not configured",
        "phase_offline": "🔴 {phase}: unavailable",
        "phase_starting": "starting",
        "phase_no_data": "no data",
        "phase_recovering": "🟡 {phase}: {voltage:.1f} V (recovering)",
        "network_title": "⚡ Grid voltage · {phases}",
        "no_measurements": "{phase}: no valid measurements.",
        "l1_today": "{phase} today: {minimum}–{maximum} V, avg {average} V",
        "power_recovered": "✅ Power restored · connection loss {duration}",
        "last_l1": "🕐 Last {phase} data: {stamp}",
        "event_low": "🚨 Low grid voltage",
        "event_warning_low": "⚠️ Low voltage",
        "event_critical_low": "🚨 Critically low voltage",
        "event_warning_high": "⚠️ High voltage",
        "event_critical_high": "🚨 Critically high voltage",
        "event_recovered": "✅ Voltage recovered",
        "unstable": "⚠️ Unstable voltage: {count} transitions in 15 min",
        "episode_title": "📋 Event summary",
        "episode_period": "🕐 {start}–{end} · {duration}",
        "episode_stats": "⚡ {minimum}–{maximum} V · avg {average} V",
        "episode_transitions": "🔄 Transitions: {count}",
        "episode_abnormal": "⚠️ Outside normal range: {duration}",
        "settings_title": "⚙️ Bot settings",
        "settings_choose": "Choose a section:",
        "settings_section_device": "📟 Device",
        "settings_section_voltage": "⚡ Voltage",
        "settings_section_notifications": "🔔 Notifications",
        "settings_section_schedule": "🕒 Schedule",
        "settings_section_service": "🧰 Service",
        "settings_section_meter": "📟 Shelly model",
        "settings_section_language": "🌐 Language",
        "settings_section_quiet": "🌙 Quiet hours",
        "settings_section_cooldown": "🔔 Cooldown",
        "settings_section_edit": "✏️ Edit window",
        "settings_section_tests": "🧪 Notification tests",
        "settings_section_low": "⬇️ Low thresholds",
        "settings_section_high": "⬆️ High thresholds",
        "settings_section_legacy_low": "🚨 Low-voltage alert",
        "settings_section_dwell": "⏳ Dwell timers",
        "settings_section_summary_time": "🕒 Summary time",
        "settings_back": "⬅️ Back",
        "settings_change_meter": "📟 Change model",
        "settings_reset_confirm": (
            "⚠️ Reset runtime settings to .env defaults?"
        ),
        "settings_reset_yes": "✅ Reset",
        "settings_cancel": "❌ Cancel",
        "settings_module": "📟 Meter: {value}",
        "settings_language": "🌐 Language: {value}",
        "settings_normal": "🔕 Normal messages: {value}",
        "settings_quiet": "🌙 Quiet hours: {state} {hours}",
        "settings_cooldown": "🔔 Voltage alert cooldown: {value}",
        "settings_edit": "✏️ Edit window: {value}",
        "settings_low": "🚨 Low voltage: {state} < {voltage:g} V",
        "settings_recovery": "✅ Alert recovery: ≥ {voltage:g} V",
        "settings_levels": "⚠️ Levels: {state} {range}",
        "settings_maintenance": "🛠 Maintenance: {state}",
        "settings_lines": "🔌 {phases}",
        "settings_phases": "🔌 Phases: L1 ON · L2 {l2} · L3 {l3}",
        "settings_outage": "🔌 Outage alerts: {state}",
        "settings_summary": "📅 Summary: {state} {time}",
        "settings_pin": "📌 Pin status: {state}",
        "settings_dwell": "⏳ Dwell: {dwell:g}s / recovery {recovery:g}s",
        "settings_instability": "⚡ Instability indicator: {state} · 4/15 min",
        "silent": "silent",
        "audible": "audible",
        "minutes": "{value} min",
        "button_silent": "🔕 Silent ON/OFF",
        "button_quiet": "🌙 Quiet ON/OFF",
        "button_edit": "✏️ Window {value} min",
        "button_start_dec": "◀ Start -1h",
        "button_start_inc": "Start +1h ▶",
        "button_end_dec": "◀ End -1h",
        "button_end_inc": "End +1h ▶",
        "button_low": "🚨 Alert ON/OFF",
        "button_threshold": "Threshold {delta}",
        "button_recovery": "Recovery {delta}",
        "button_levels": "⚠️ Levels ON/OFF",
        "button_outage": "🔌 Outage alerts ON/OFF",
        "button_summary": "📅 Summary ON/OFF",
        "button_pin": "📌 Pin ON/OFF",
        "button_instability": "⚡ Instability ON/OFF",
        "button_reset": "↩️ Reset to .env",
        "button_test_loud": "🔔 Test loud",
        "button_test_silent": "🔕 Test silent",
        "button_test_edit": "✏️ Test edit",
        "only_admin": "Administrator only",
        "saved": "Saved",
        "settings_error": "Settings error",
        "test_loud": "🔔 Loud notification test",
        "test_silent": "🔕 Silent notification test",
        "test_edit_start": "✏️ Edit test · step 1/2",
        "test_edit_done": "✅ Telegram editing works · step 2/2",
        "help": (
            "Commands: /voltage /status /today /minmax /history /outages "
            "/health /settings /chart /export /quality /summary /events /week "
            "/maintenance /audit /device /updates"
        ),
        "service_ok": "✅ Service is running; {phases}\n{backup}",
        "no_current": "No current measurement data.",
        "phases_title": "⚡ Phases",
        "history_title": "🗂 Recent measurements {phase}",
        "outages_title": "🔌 Recent outages {phase}",
        "outage_now": "ongoing now",
        "no_outages": "{phase}: no outages",
        "outage_count_today": "🔌 Confirmed outages today: {count}",
        "outage_unavailable": "⏱ Confirmed unavailability: {duration}",
        "outage_boundaries": (
            "ℹ️ Boundaries: {failures} failures / {successes} successful polls"
        ),
        "outage_interpretation": (
            "ℹ️ These are Shelly connectivity gaps, not confirmed power "
            "cuts. Network or IP changes can cause them."
        ),
        "summary_title": "📅 Daily summary",
        "summary_unconfigured": "⚪ {phase}: not configured",
        "quality_title": "📊 Quality {phase} for {hours}h",
        "quality_samples": "Measurements: {value}",
        "quality_below": "Below {voltage:g} V: {value}",
        "quality_above": "Above {voltage:g} V: {value}",
        "quality_bad": "Outside normal range: {value:.2f}%",
        "audit_title": "🧾 Recent settings changes",
        "phase_sensor_offline": "⚠️ {phase}: sensor unavailable",
        "phase_sensor_recovered": (
            "✅ {phase}: sensor connection restored · {duration}"
        ),
        "availability_offline": (
            "⚠️ Shelly is unreachable. Last valid reading is preserved."
        ),
        "availability_recovered": "✅ Shelly connection restored · {duration}",
        "callback_today": "Today: {value}",
        "callback_outages": "Connection losses: {value}",
        "callback_updated": "Status updated",
        "device_title": "🔧 Shelly device status",
        "device_unconfigured": "{phase}: not configured",
        "phase_invalid": "Phase must be L1, L2 or L3",
        "phase_not_configured": "{phase}: sensor is not configured",
        "chart_caption": "{phase} · {hours} h · {count} points",
        "export_caption": "{phase} · {hours} h · {count} rows",
        "command_error": "Error: {error}",
        "maintenance_admin_only": (
            "Only an administrator can change maintenance mode"
        ),
        "maintenance_status": "🛠 Maintenance: {value}",
        "maintenance_usage": "Usage: /maintenance 60 or /maintenance off",
        "summary_phase": (
            "{phase}: {minimum}–{maximum} V, avg {average} V; "
            "connection losses: {count}, {duration}"
        ),
        "status_online": "🟢 Shelly is connected",
        "status_recovering": "🟡 Shelly connection recovering",
        "status_offline": "🔴 Shelly is offline · current voltage unknown",
        "status_starting": "🟡 Awaiting Shelly readings",
        "status_frequency": "〰️ Frequency: {value:.2f} Hz",
        "unit_voltage": "V",
        "unit_frequency": "Hz",
        "state_on": "on",
        "state_off": "off",
        "backup_ok": "💾 Backup: {stamp}, {size} KiB · SQLite: {integrity}",
        "device_details": (
            "{phase}: {model} · firmware {firmware} · uptime "
            "{uptime} s · clock {skew}"
        ),
        "device_offline": "{phase}: unreachable ({error})",
        "menu_events": "🕘 Events",
        "events_title": "🕘 Events in last 24 hours",
        "events_empty": "No confirmed events in the last 24 hours",
        "events_warning": "⚠️ {phase}: {state} · {time}",
        "events_connection": "🔌 {phase}: connection lost · {time}",
        "menu_summary": "📅 Summary",
        "events_notice": (
            "ℹ️ Connection losses: last 24 h; voltage "
            "changes: retained history only (usually 1 h)."
        ),
        "events_state_low": "low voltage",
        "events_state_high": "high voltage",
        "events_state_normal": "voltage recovered",
        "device_unknown": "n/a",
        "events_reconnected": "✅ {phase}: connection restored · {time}",
        "unit_seconds": "s",
        "button_cooldown_off": "🔔 Cooldown: off",
        "button_cooldown_min": "{value} min",
        "cmd_week": "Seven-day quality report",
        "weekly_title": "📅 Grid quality · past 7 days",
        "weekly_phase": (
            "{phase}: {minimum}–{maximum} V, avg {average} V · samples {count}"
        ),
        "weekly_coverage": (
            "✅ Measured intervals: {observed} · unknown: {unknown}"
        ),
        "weekly_threshold": (
            "⚠️ Estimated time below threshold: {below}; above: {above}"
        ),
        "weekly_connection": (
            "🔌 Connection losses: {count} · duration {duration}"
        ),
        "weekly_notice": (
            "ℹ️ Only consecutive samples up to 30 s "
            "apart count as observed. Remaining time is "
            "unknown. Lost connection does not prove a "
            "power outage."
        ),
        "compact_preview_title": "⚡ Compact status · {phases}",
        "compact_preview_note": (
            "ℹ️ Admin preview only. The channel message has not changed."
        ),
        "phase_cached": (
            "🟡 {phase}: {voltage:.1f} {unit} · "
            "cached (awaiting fresh reading)"
        ),
        "backup_missing": "💾 Backup: missing",
    },
}


def tr(language: str, key: str, **values: object) -> str:
    """Translate only from the selected language; missing keys are errors."""

    lang = language if language in LANGUAGES else "ru"
    template = _TEXT[lang][key]
    return template.format(**values)


def command_descriptions(language: str) -> dict[str, str]:
    """Return localized Telegram command descriptions."""

    names = (
        "start",
        "help",
        "voltage",
        "outages",
        "status",
        "today",
        "minmax",
        "history",
        "health",
        "settings",
        "chart",
        "export",
        "quality",
        "summary",
        "events",
        "week",
        "maintenance",
        "audit",
        "device",
        "updates",
    )
    return {name: tr(language, f"cmd_{name}") for name in names}


def duration_text(seconds: float | None, language: str) -> str:
    """Format a compact duration for the selected Telegram language."""

    total = max(0, int(seconds or 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    units = {
        "ru": ("ч", "м", "с"),
        "uk": ("г", "хв", "с"),
        "en": ("h", "m", "s"),
    }
    h_unit, m_unit, s_unit = units.get(language, units["ru"])
    if hours:
        return f"{hours}{h_unit} {minutes}{m_unit}"
    if minutes:
        return f"{minutes}{m_unit} {secs}{s_unit}"
    return f"{secs}{s_unit}"
