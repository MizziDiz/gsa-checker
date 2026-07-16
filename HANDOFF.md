# КОНТЕКСТ ПРОЕКТА: gsa-checker

Handoff-заметка по автоматизации **GSA Search Engine Ranker**. Собрана из рабочей сессии.

## 1. Цель
Автоматизировать GSA SER по образцу существующего `Aparser-checker` (мониторинг A-Parser
→ Telegram). Нужно: создавать проекты, заполнять данными, менять настройки, собирать
статистику, видеть остаток целей. Реализовано в проекте **`gsa-checker`**.

## 2. Инфраструктура
- Разработка — на Linux-сервере `.187` = **187.124.131.67** (hostname `srv1815224`).
- **SMB-шара** `\\187.124.131.67\шара` → `/srv/share` (smb-юзер `aparser`) — общий транспорт
  файлов; через неё присылались образцы.
- GSA SER работает на отдельном **Windows-сервере**, деплой в папку `C:\A-GSA`.

## 3. Ключевое ограничение и архитектура
**У GSA SER нет HTTP API** (в отличие от Enterprise-A-Parser). Подход **гибридный**: файлы
`.prj`/data для чтения статистики/остатка и правки настроек; UI-автоматизация — на будущее.
«Остаток» = непереработанные цели в кэше.

## 4. Реальные форматы (разобраны по образцам)
- **`.prj`** — INI, 4 секции: `[data_value]` (URL, Keywords, спин-контент), `[Options]`
  (~100+ настроек), `[engines]` (`Движок=1/0`, ~302), `[email accounts]`. Файл UTF-8, но в
  `[email accounts]` сырой разделитель `0xFF` → читаю/пишу с `surrogateescape` (round-trip
  байт-в-байт).
- **Data-файлы:** verified=`.success`, очередь=`.verify`, обработано=`.urls_done`/
  `.hosts_done`/`.email_done`, аккаунты=`.static`, статьи=`.articles`(+`.articles_idx`).
- **Остаток целей на боевом сервере разбит на 3 файла:** `.targets` + `.new_targets` +
  `.new_targets2` (на одиночном образце `SERVerified` был только `.targets`). Дефолт
  `target_cache_glob` включает все три.
- У оператора уже есть свой генератор `.prj` — **Spin-generator** (на шаре: `generateprjs.py`
  + `template.prj` + `fill_gsa_emails*`). gsa-checker его НЕ дублирует, добавляет недостающее.

## 5. Реализовано (протестировано на реальных данных)
| Команда | Что делает | Статус |
|---|---|---|
| `--check` | диагностика путей/расширений | ✅ |
| `--remaining` | остаток целей (сумма target-файлов) | ✅ |
| `--stats` (+`--json`) | остаток/verified/на проверку/обработано | ✅ |
| `--settings` | массовая правка `[Options]`/`[engines]` в `.prj` (сухой прогон/`--apply`+бэкап/`--only`) | ✅ |
| `--notify` / `--test-telegram` | Telegram: мало целей / кончились / встал / heartbeat; кулдаун+дедуп | ✅ |
| SQLite time-series | ETA до исчерпания `.targets`, детект «проект встал» | ✅ |

Модули: `gsa_checker.py`, `lib/prj.py`, `lib/statsdb.py`, `lib/telegram.py`.
Зависимость — только `requests` (sqlite3 — stdlib).

## 6. Связь с A-Parser
Прямого API нет. **Единственная связка — файлы:** A-Parser (autosend) кладёт списки URL на
шару (`for_gsa_ser`, `Aparser test autosend`), они становятся целями (`.targets`) проектов
GSA. gsa-checker работает на стороне GSA зеркально тому, как Aparser-checker на стороне
A-Parser; общий выход — Telegram.

## 7. Git
- Отдельный **публичный** репозиторий: https://github.com/MizziDiz/gsa-checker (ветка `main`).
- В git только исходники; `data/` (конфиг/состояние/БД) и секреты — в `.gitignore`.
- Коммиты: первый релиз → фикс target-glob (3 файла) → фикс чтения конфига с BOM (`utf-8-sig`).

## 8. Тестовый проект
`/srv/share/gsa_test_project/TestAutosend.*` — `.prj` из шаблона + 5000 целей из autosend
A-Parser. Фикстура для тестов (остаток 5000, verified 0).

## 9. Деплой на сервере (в процессе)
На Windows в `C:\A-GSA`: git+python поставлены, репозиторий склонирован, `--check` прошёл
(путь `...\GSA Search Engine Ranker\projects` верный, 126 проектов).

**Текущий блокер:** `data\gsa_checker.config.json` не парсится — синтаксическая ошибка JSON
на строке 18 (`Expecting ',' delimiter`, кол. 25). BOM уже починен; осталось поправить сам
JSON (пропущенная запятая / одиночные `\` вместо `\\` / `//`-комментарий).

## 10. Открытые вопросы / next steps
1. Починить конфиг (строка 18) → запустить `--remaining`/`--stats`.
2. **Сверка семантики:** взять один проект и сравнить остаток gsa-checker с числом target
   URL cache в самом GSA — подтвердить, что сумма трёх target-файлов = остаток.
3. Из роадмапа осталось «сверх запроса»: автопилот (мало целей → батч из `for_gsa_ser`) и
   централизованный сбор по парку серверов.
4. Мелочь: версия GSA — «актуальная», не критично.

## Развёртывание (шпаргалка)
```
git clone https://github.com/MizziDiz/gsa-checker.git C:\A-GSA
cd C:\A-GSA
python -m pip install -r requirements.txt
mkdir data
copy config.example.json data\gsa_checker.config.json   REM вписать gsa_projects_dir + telegram
python gsa_checker.py --check
python gsa_checker.py --remaining
```
JSON строгий: бэкслеши `\\`, запятые между парами, без `//`-комментариев. Конфиг читается
как `utf-8-sig` (BOM от Блокнота допустим).
