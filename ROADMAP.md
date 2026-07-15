# Роадмап gsa-checker

Автоматизация и мониторинг **GSA Search Engine Ranker** — зеркало подхода Aparser-checker.
Цель: создавать проекты, заполнять входными данными, менять настройки, собирать
статистику, видеть остаток целей.

Ключевое ограничение: **у GSA SER нет HTTP API**. Интерфейс гибридный —
файлы проектов для чтения/массовых операций, UI-автоматизация для живых правок.

---

## ✅ Готово

### Остаток целей (`--remaining`)
Считает непереработанные цели по проектам = строки в файлах кэша целей
(`target_cache_glob`, по умолчанию `*.new`/`*.targets`). Побайтовый подсчёт (файлы на
сотни МБ), игнор пустых строк, суммирование файлов проекта, имя из `.prj`.
Режимы: `--remaining`, `--remaining --json`, `--check` (диагностика путей/расширений).

---

## 🔜 Ближайшее

### 1. `lib/prj.py` — разбор и редактирование `.prj`  ✅ ГОТОВО
Формат разобран по реальному `template.prj`: INI, 4 секции (`[data_value]`,
`[Options]`, `[engines]`, `[email accounts]`), UTF-8, но `[email accounts]` содержит
сырой разделитель `0xFF` → читаем/пишем с `surrogateescape` (round-trip байт-в-байт,
проверено на 168 КБ). Класс `Prj`: `load`/`get_value`/`set_value`/`save`, построчный
редактор сохраняет порядок/пробелы/спин-синтаксис.

### 1b. `--settings` — массовая правка настроек  ✅ ГОТОВО
Правит `[Options]`/`[engines]` в пачке `.prj` по `gsa_projects_dir`. Сухой прогон по
умолчанию, `--apply` пишет с бэкапом `.prj.bak`, фильтр `--only`. Предупреждает, что
GSA должен быть закрыт (иначе затрёт правки при выходе).
```
python gsa_checker.py --settings --set "engines:Askbot=0" --set "Options:use random url=1"
python gsa_checker.py --settings --set-file changes.txt --only fr --apply
```

### 2. `--stats` — снимок статистики  ✅ ГОТОВО (снимок), 🔜 time-series
Форматы подтверждены на живом проекте SERVerified. Счётчиков в `.prj` НЕТ —
статистика = число строк в data-файлах:

| Метрика | Файл | Смысл |
|---------|------|-------|
| остаток | `.targets` | цели, куда ещё постить |
| verified | `.success` | подтверждённые размещения |
| на проверку | `.verify` | очередь верификации |
| обработано | `.urls_done` | уже пройденные URL |

`python gsa_checker.py --stats` (+ `--json`) — таблица по проектам + итог.

### 2b. Time-series + ETA + детект «встал»  ✅ ГОТОВО
`lib/statsdb.py` (SQLite, WAL, индекс, ретенция `stats_retention_days`). Каждый
`--stats`/`--notify` пишет снимок; по истории за `eta_window_min` считаются скорость
расхода целей, **ETA до исчерпания `.targets`** (колонки в `--stats`) и признак
«проект встал» (остаток не убывает и нет новых verified). Проверено на модельной
истории и на тестовом проекте.

### 3. `--create` — создание проекта из шаблона + заливка целей  ✅ ГОТОВО (базовое)
Собирает готовый к импорту проект: `<name>.prj` из шаблона (`gsa_template_prj`) с
проставленными `URL`/`Keywords` + `<name>.targets` из батча (`--targets` файл/папка,
дедуп, `--limit`). Пишет в `create_out_dir`, живой GSA не трогает. `--dry-run`, `--force`.
```
python gsa_checker.py --create --name Brave-0001 --url https://site/ \
  --keywords "kw1, kw2" --targets "\\share\for_gsa_ser\09-07" --limit 8000
```
Дальше:
- [ ] заливка emails/статей (сейчас — их `fill_gsa_emails`/Spin-generator);
- [ ] идемпотентность по батчу (журнал, как `aparser_sent.jsonl`) — для автопилота;
- [ ] импорт в живой GSA: копирование в `gsa_projects_dir` при закрытом GSA / через UI.

### 4. `lib/ui.py` — UI-автоматизация (pywinauto)  ⏳ КАРКАС ГОТОВ, нужна обкатка
Технология — **pywinauto** (нативно из Python). Ленивый импорт: на Linux не мешает.
Цель — «толкнуть» GSA подхватить дозалитые цели (рефреш/активация проектов).
- [x] `--ui-check` — диагностика: перечисляет окна GSA и выгружает дерево контролов в
      `data/ui_controls.txt` (по нему настраиваем селекторы);
- [x] `--ui-refresh` — каркас рефреша, шаги через конфиг (`ui_select_all`,
      `ui_context_item`, `ui_refresh_keys`, `ui_backend`, `ui_window_title`);
- [ ] **обкатка на Windows-сервере с GSA**: прогнать `--ui-check`, по дампу довести
      грид проектов и точный пункт меню/клавиши рефреша; затем связать с `--autopilot`.

---

## 📋 Дальше

### 4b. Выгрузка результатов (`--export`)  ✅ ГОТОВО
Verified-ссылки из `.success` → CSV в `export_dir` на шаре. Инкрементально (офсет в
state — только новое). Страна: **ccTLD как в GSA** (подтверждено: Sven, forum 11666),
для gTLD — добор по **IP-GeoIP** (`lib/geoip.py`, MaxMind GeoLite2, `geoip_db`, кэш
`data/geoip_cache.json`); колонка `country_src`=tld/ip. Колонки project/country/
country_src/url/date/engine/type/anchor/target; `--dry-run`/`--full`; сводка по странам
+ Telegram. Планировщик — раз в день.

### 5. Telegram-уведомления + heartbeat  ✅ ГОТОВО (базовое)
`lib/telegram.py` (порт из Aparser-checker: прямая отправка / прокси / релей).
`--notify`: остаток < `low_targets_threshold` → «⏳ мало целей», `0` → «🛑 цели
кончились», рост выше порога → «✅ пополнились»; кулдаун `cooldown_hours` и дедуп в
`data/gsa_checker.state.json`; heartbeat «🟢 всё ок» раз в `heartbeat_hours`.
`--test-telegram` — проверка канала. `--notify --dry-run` — превью без отправки.
`--notify` шлёт «🟠 проект встал» по time-series (п.2b) и ETA в «мало целей». ✅

### 6. Автопилот  ✅ ГОТОВО (равномерная раздача, server-9 модель)
`--autopilot`: кормит **активные** проекты (исключая имена из `autopilot_exclude_names`,
по умолчанию содержащие `CC`/`TEST`/`Common` — фильтр по имени, т.к. `last status` в
`.prj` не различает active/inactive). При остатке любого проекта ниже
`autopilot_min_targets` (20 000) берёт новейшие неиспользованные батчи из
`autopilot_pool_dir` (до `autopilot_batch_limit_mb` ≈120 МБ), делит их цели **ПОРОВНУ**
между проектами (round-robin, каждому свой кусок, данные не стирает, дописывает в
`.new_targets`), переносит батчи в `autopilot_used_dir` (журнал `data/gsa_autopilot.jsonl`),
и при `--apply` делает один `--ui-refresh`. Раз в `email_reminder_days` (30) шлёт в
Telegram напоминание обновить почты (сами почты не трогает при работающем GSA). НЕ
создаёт новые проекты. Ставить в планировщик раз в час.
```
python gsa_checker.py --autopilot            # превью
python gsa_checker.py --autopilot --apply     # раздать + перенести батчи + рефреш
```

### 6b. Обновление почт (`--emails`)  ✅ ГОТОВО
Перегенерирует `[email accounts]` свежими почтами (`emails_per_project`, провайдер
`email_provider_ini`) в нативном формате GSA — разделитель один байт `0xFF` (порт
`fill_gsa_emails`, но без бага двухбайтового `ÿ`). `lib/emails.py` + `Prj.replace_section`.
Сухой прогон/`--apply`+бэкап/`--only`/`--count`. При закрытом GSA.

### 7. Встраивание в конвейер
keygen → A-Parser → `for_gsa_ser` → **gsa-checker создаёт проекты** — замкнуть цикл,
чтобы созданные A-Parser'ом списки автоматически превращались в проекты GSA.

### 8. Централизованный режим (control plane)
Как в Aparser-checker: `--stats --json`/`--remaining --json` с узлов → сводный дашборд
и остаток/ETA по всему парку GSA-серверов в одном месте.

---

## 🧩 Что нужно от оператора, чтобы снять блокировку
1. Образец с Windows-сервера: один `.prj` + его data-файлы (пароли можно затереть).
2. Версия GSA SER и путь к папке `projects`.
3. Прогон `python gsa_checker.py --check` на сервере — покажет реальные расширения
   файлов в папке проектов (уточнить `target_cache_glob` и имена data-файлов).
4. Эталонный шаблон проекта (какие движки/настройки — база для `--create`).
