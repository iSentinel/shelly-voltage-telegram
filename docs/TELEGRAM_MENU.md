# Telegram menu and voltage phases

`/start` opens six buttons: Status, Settings, Today, Outages, Chart and Help.
The existing command names continue to work. Navigation is restricted to the
configured control chat and allowed users. Only administrators receive settings
write controls.

Status and `/status` show voltage, measurement time and the active bot settings.
The public channel status continues to contain voltage information without
navigation buttons. Settings are available even before the first measurement.

Choose the model in `/settings` → Device. The bot rebuilds its read-only clients
and active voltage lines immediately and persists the model in SQLite:

| Model | Independent voltage phases | Display |
| --- | --- | --- |
| Pro EM-50, EM Gen3 | 1 | L |
| PM Mini and 1PM models | 1 | L |
| Pro 3EM / Pro 3EM-3CT63 | 3 | L1, L2, L3 |

Two current clamps on Pro EM-50 do not represent two independent voltage phases.
Legacy `PHASE_L2_*` / `PHASE_L3_*` values are preserved but no longer activate
additional lines for a single-phase model. Use Pro 3EM for three-phase monitoring.

Historical primary measurements retain the SQLite identifier `L1`; no migration
or deletion is necessary. Existing L2/L3 history is preserved when selecting a
single-phase model and becomes available again when selecting Pro 3EM.
Commands accept `L` as an alias for primary history; `L1` remains compatible.
Charts and exports reject lines inactive for the selected model. Prometheus and
legacy health phase keys retain L1/L2/L3 for existing integrations; health also
includes `line_labels` with the active display names.

Model switching and polling share a lock so a measurement cycle cannot mix two
model configurations. No configuration is written to the physical Shelly.

## Проверка пользователем

1. Отправьте `/start`: появится меню из шести кнопок.
2. Нажмите «Статус»: проверьте напряжение и актуальные настройки.
3. В разделе устройства выберите Pro EM-50: должна остаться одна строка `L`.
4. Для установленного Pro 3EM выберите его модель: появятся `L1`, `L2`, `L3`.
5. Перезапустите бот: выбранная модель должна сохраниться.

Не выбирайте неподключённую модель для проверки работающего прибора.
Аппаратная проверка Pro 3EM требует реального устройства.
