# gsa-checker — автоматизация и мониторинг GSA Search Engine Ranker

Зеркало подхода [`Aparser-checker`](../Aparser-checker), но для **GSA SER**. Цель та же:
создавать проекты, заполнять входными данными, менять настройки, собирать статистику,
видеть **остаток целей** (сколько ещё не обработано).

## Ключевое отличие от A-Parser: у GSA SER НЕТ HTTP API

GSA SER — нативное Windows-приложение без API. Поэтому интерфейс **гибридный**:

| Что делаем | Как |
|------------|-----|
| Остаток целей, статистика (submitted/verified) | **чтение файлов** проектов (папка `projects`) |
| Массовое создание проектов, заливка списков целей | **запись файлов** `.prj` / импорт |
| Живые правки настроек запущенного GSA | **UI-автоматизация** (pywinauto/AutoIt) — `lib/ui.py`, в работе |

Место в конвейере: keygen → A-Parser парсит футпринты → списки URL падают в
`\\share\for_gsa_ser\<батч>\*.txt` → **сюда** их импортирует gsa-checker как цели проектов.

## Структура

```
gsa_checker.py          # точка входа: --remaining / --check (+ далее --stats, --create, --settings)
config.example.json     # шаблон конфига → скопировать в data/gsa_checker.config.json
lib/                    # (появятся) prj.py — парсер/генератор .prj; ui.py — UI-автоматизация; stats.py; telegram
data/                   # рабочие данные (конфиг/состояние/БД/логи) — не в git
docs/
```

## Что уже готово

### Остаток целей (`--remaining`)
Считает непереработанные цели по проектам = число строк в файлах кэша целей
(шаблон `target_cache_glob`, по умолчанию `*.new`, `*.targets`). Быстрый побайтовый
подсчёт (файлы бывают на сотни МБ), игнор пустых строк, файлы одного проекта
суммируются, имя проекта берётся из `.prj` если удаётся распарсить.

```
cp config.example.json data/gsa_checker.config.json   # впишите gsa_projects_dir
python gsa_checker.py --check       # диагностика: путь, расширения файлов в папке проектов
python gsa_checker.py --remaining   # таблица остатка + итог
python gsa_checker.py --remaining --json
```

> **`--check` запускать первым** на реальном сервере: он покажет, какие расширения
> лежат в папке проектов, — по ним уточняется `target_cache_glob` под вашу версию GSA.

### Автопилот (`--autopilot`)
Кормит **активные** проекты равномерно из общего пула. Исключает проекты, чьё имя
содержит строки из `autopilot_exclude_names` (по умолч. `CC`/`TEST`/`Common`). Когда у
любого проекта остаток ниже `autopilot_min_targets`, берёт новейшие неиспользованные
батчи из `autopilot_pool_dir` (до `autopilot_batch_limit_mb` МБ) и делит их цели
**поровну** между проектами (каждому свой кусок, дописывает в `.new_targets`, данные не
стирает). Использованные батчи переносит в `autopilot_used_dir`. При `--apply` в конце
делает один `--ui-refresh`. Раз в `email_reminder_days` шлёт напоминание обновить почты.
```
python gsa_checker.py --autopilot            # превью (сухой прогон)
python gsa_checker.py --autopilot --apply     # раздать + перенести батчи + рефреш
```
> Ставить в планировщик раз в час (GSA запущен). Новые проекты НЕ создаёт.

### UI-рефреш GSA (`--ui-check` / `--ui-refresh`)
После файловой дозаливки (`--autopilot`) GSA нужно «толкнуть», чтобы подхватил новые
цели. `lib/ui.py` на **pywinauto** (только Windows; на Linux команды дают понятную
ошибку, остальной gsa-checker не задет — импорт ленивый).
```
pip install pywinauto                    # на Windows-сервере с запущенным GSA
python gsa_checker.py --ui-check          # выгрузит структуру окна в data/ui_controls.txt
python gsa_checker.py --ui-refresh         # рефреш (шаги через ui_* в конфиге)
```
Селекторы под конкретный билд — в конфиге: `ui_window_title`, `ui_backend`
(`uia`/`win32`), `ui_select_all`, `ui_context_item` (пункт правого клика, напр. `Active`),
`ui_refresh_keys` (напр. `{F5}`). **Порядок ввода в строй:** сначала `--ui-check`, по
дампу настроить `ui_*`, затем `--ui-refresh`.

### Создание проекта (`--create`)
Собирает готовый к импорту проект: `.prj` из шаблона (`gsa_template_prj`) с проставленными
`URL`/`Keywords` + `.targets` из батча целей (файл или папка `for_gsa_ser`, дедуп, `--limit`).
Пишет в `create_out_dir`, живой GSA не трогает.
```
python gsa_checker.py --create --name Brave-0001 --url https://site/ \
  --keywords "kw1, kw2" --targets "\\share\for_gsa_ser\09-07" --limit 8000
# --dry-run — превью; --force — перезапись; --template/--out — переопределить пути
```
Импорт: скопировать созданные файлы в `gsa_projects_dir` (при закрытом GSA) или
импортировать через GSA. Заливка emails/статей — как и раньше через `fill_gsa_emails`/Spin-generator.

### Обновление почт (`--emails`)
Перегенерирует секцию `[email accounts]` в `.prj` свежими почтами (уникальный набор на
проект, `emails_per_project` штук, провайдер `email_provider_ini`). Формат нативный для
GSA — разделитель один байт `0xFF` (в отличие от `fill_gsa_emails`, где символ `ÿ` под
UTF-8 давал два байта). Остальное в `.prj` не трогает. Сухой прогон по умолчанию,
`--apply` пишет с бэкапом `.prj.bak`; `--only`/`--count` — фильтр/переопределение.
```
python gsa_checker.py --emails --count 20                # превью по всем проектам
python gsa_checker.py --emails --only fr --apply          # обновить французские
```
> ⚠ Делать при **закрытом GSA** (как `--settings`). Провайдер (`*.email.ini`) должен
> существовать в настройках GSA.

### Массовая правка настроек (`--settings`)
Меняет `[Options]`/`[engines]` в пачке `.prj` (`lib/prj.py` — построчный редактор,
round-trip байт-в-байт, сохраняет спин-синтаксис и разделитель `0xFF` в аккаунтах).
Сухой прогон по умолчанию; `--apply` пишет с бэкапом `.prj.bak`; `--only` — фильтр.

```
# показать, что изменится (без записи):
python gsa_checker.py --settings --set "engines:Askbot=0" --set "Options:use random url=1"
# записать только во французские проекты (GSA закрыт!):
python gsa_checker.py --settings --set-file changes.txt --only fr --apply
```

> ⚠ Делать при **закрытом GSA**: он держит проекты в памяти и перезапишет `.prj`
> при выходе, затерев файловые правки.

### Статистика (`--stats`)
Снимок по каждому проекту — считает строки в реальных data-файлах GSA:

| Метрика | Файл |
|---------|------|
| остаток целей | `.targets` |
| verified (размещено) | `.success` |
| на проверку | `.verify` |
| обработано URL | `.urls_done` |

```
python gsa_checker.py --stats          # таблица по проектам + итог
python gsa_checker.py --stats --json    # для централизованного сбора
```

Каждый прогон `--stats`/`--notify` пишет снимок в SQLite (`data/gsa_stats.db`,
`lib/statsdb.py`). Как накопится история (≥2 снимка за `eta_window_min`), в `--stats`
появляются колонки **ЦЕЛЬ/Ч** (скорость расхода) и **ETA** (прогноз до исчерпания
`.targets`; `СТОП` — если проект встал). Ретенция — `stats_retention_days`.

### Выгрузка результатов (`--export`)
Выгружает verified-ссылки (`.success`) в **CSV со страной** (страна по ccTLD домена:
`.pl`→Poland, `.ru`→Russia, `.com`→gTLD) в папку `export_dir` на шаре. **Инкрементально:**
по офсету в `data/gsa_checker.state.json` берёт только новые ссылки с прошлого прогона.
Колонки: `project, country, url, date, engine, type, anchor, target`.
```
python gsa_checker.py --export           # выгрузить новое → CSV + сводка по странам
python gsa_checker.py --export --dry-run  # превью без записи (офсеты не двигаются)
python gsa_checker.py --export --full      # весь .success, а не только новое
```
Ставить в планировщик (напр. раз в день). CSV в `utf-8-sig` (открывается в Excel).

### Уведомления в Telegram (`--notify`)
`lib/telegram.py` (прямая отправка, `telegram_proxy` или сервер-релей `telegram_relay_url`).
Сообщения: остаток < `low_targets_threshold` → «⏳ мало целей», `0` → «🛑 цели кончились»,
рост выше порога → «✅ пополнились»; heartbeat «🟢 всё ок» раз в `heartbeat_hours`.
Кулдаун `cooldown_hours` и дедуп — в `data/gsa_checker.state.json`.

```
python gsa_checker.py --test-telegram      # проверить канал
python gsa_checker.py --notify --dry-run    # превью сообщений без отправки
python gsa_checker.py --notify              # рабочий прогон (для планировщика)
```

Планировщик (раз в N минут) — как в Aparser-checker:
```
# Windows:  schtasks /Create /SC MINUTE /MO 30 /TN "gsa-notify" ^
#             /TR "python C:\gsa-checker\gsa_checker.py --notify"
# Linux cron:  */30 * * * * cd /path/gsa-checker && python3 gsa_checker.py --notify
```

## Что дальше (нужны образцы с сервера)

Чтобы писать парсер настроек и генератор проектов, нужен реальный формат `.prj`.
**Положите на шару образец:** один `.prj` + его data-файлы (пароли аккаунтов можно
затереть) + версию GSA SER и путь к папке `projects`.

- [ ] `lib/prj.py` — разбор `.prj` в структуру и обратно (настройки проекта).
- [ ] `--stats` — submitted/verified в SQLite (переиспользовать схему из Aparser-checker).
- [ ] `--create` — создание проекта из шаблона + заливка списка целей из `for_gsa_ser`.
- [ ] `--settings` — массовая правка настроек (файлово или через `lib/ui.py`).
- [ ] Telegram-уведомления (остаток < порога, проект встал) + heartbeat — переиспользовать
      `relay.py`/`telegram` из Aparser-checker.
```
