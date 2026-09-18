# XRumer через MCP (управление из ИИ)

`xrumer_mcp.py` — **настоящий MCP-сервер (Model Context Protocol)**. Любой MCP-клиент
(Claude Desktop, Claude Code, свой агент) подключается и управляет XRumer 23 («X23»)
набором инструментов: статус, старт/стоп, потоки, поля проекта, файлы проектов и баз,
логи, скриншот.

## Важно про слово «MCP»

Их два, и это НЕ одно и то же:

- **«MCP-коннектор» XRumer** — фирменная фича вендора (папка `C:\MCP` на ноде, ключ
  `xmcp_…`). X23 раз в ~10 с стучится на сервер вендора (`botmasterru.com` /
  `botmasterlabs.net`), мы кладём команды (`a=push`) и читаем состояние (`a=state`).
  Это внутренний транспорт, а не Model Context Protocol.
- **Model Context Protocol** — открытый стандарт Anthropic. Это то, что отдаёт
  `xrumer_mcp.py` наружу, и через что рулит ИИ.

Схема: `ИИ-клиент → (MCP) → xrumer_mcp.py → lib/xrumer.py → коннектор вендора / SMB → X23`.

## Два канала под капотом (`lib/xrumer.py`)

1. **Команды X23** — через коннектор вендора. Проходят whitelist `COMMANDS` (имя + типы
   параметров): наружу не уходит произвольный JSON. Задержка исполнения 10–25 с (X23
   отстукивает раз в ~10 с) — инструменты уже ждут результата. Опрос кэшируется (лимит
   вендора). Ключ — только из файла; переборов нет (3 разных неверных ключа с IP = бан 24 ч).
2. **Файлы** (проекты `Projects\*.xml`, базы `Links\*.txt`, логи `Logs\`) — по SMB с ноды.
   Имена проверяются (`..`, кавычки, разделители не проходят). Запись — только новых
   файлов, если не задан `overwrite`; проект, открытый сейчас в X23, перезаписать нельзя
   (X23 затрёт его при выходе).

## Инструменты

| Инструмент | Тип | Что делает |
|---|---|---|
| `xrumer_sessions` | read | копии X23 на связи (sid, метка, онлайн) |
| `xrumer_state` | read | состояние: проект, база, потоки, прогресс, счётчики, LLM |
| `xrumer_get_project` | read | поля проекта, загруженного в X23 (из программы) |
| `xrumer_get_llm` | read | настройки нейросети в X23 |
| `xrumer_start` | **боевое** | запустить/продолжить постинг |
| `xrumer_stop` | write | остановить постинг |
| `xrumer_set_threads` | write | число потоков (1..1000) |
| `xrumer_set_field` | write | одно поле проекта в X23 |
| `xrumer_set_fields` | write | несколько полей разом |
| `xrumer_set_llm` | write | настройки нейросети |
| `xrumer_list_files` | read | проекты/базы/логи на ноде (SMB) |
| `xrumer_read_project` | read | поля из `Projects\<name>.xml` |
| `xrumer_project_logs` | read | логи по проекту |
| `xrumer_create_project` | write | новый `Projects\<name>.xml` из шаблона |
| `xrumer_apply_project` | write | поля из файла проекта → в открытый X23 |
| `xrumer_screenshot` | read | скриншот окна X23 |

Поля проекта (для `*_field` / `create`): `nick, real, pass, email, homepage, subject1,
subject2, city, country, occupation, interests, signature, text`. В `subject*`/`text`
допустим спинтакс `{а|б}` и макросы X23.

## Настройка

1. Секция `xrumer` в `data/gsa_checker.config.json` (образец — `config.example.json`):

   ```json
   "xrumer": {
     "host": "ru",
     "key_file": "data/ops/xrumer_mcp.key",
     "node": "gsa-03",
     "smb_host": "176.123.10.21",
     "smb_share": "C$",
     "smb_cred": "data/ops/smb_gsa-03.cred",
     "smb_root": ""
   }
   ```

   Ключ вендора (`xmcp_…`) — в `data/ops/xrumer_mcp.key`; SMB-креды (`smbclient -A`) — в
   `data/ops/smb_gsa-03.cred`. Обе папки gitignored. Вместо конфига можно задать
   `XRUMER_KEY_FILE`, `XRUMER_HOST`, `XRUMER_SMB_HOST`, `XRUMER_SMB_CRED`,
   `XRUMER_SMB_SHARE`, `XRUMER_SMB_ROOT`. В самом X23 должна стоять галочка MCP.

2. Отдельный venv (пакет `mcp` в основной requirements не входит — на ноды он не нужен):

   ```bash
   python3 -m venv /root/.venvs/xrumer-mcp
   /root/.venvs/xrumer-mcp/bin/pip install -r requirements-xrumer.txt
   ```

3. Проверка связи без запуска сервера:

   ```bash
   /root/.venvs/xrumer-mcp/bin/python xrumer_mcp.py --check
   ```

## Запуск и подключение

- **stdio** (локальный ИИ-клиент): `python xrumer_mcp.py`
- **streamable-http** (за туннелем): `python xrumer_mcp.py --http --port 8792`

Регистрация в Claude Code:

```bash
claude mcp add xrumer -- /root/.venvs/xrumer-mcp/bin/python /root/gsa-checker/xrumer_mcp.py
```

В Claude Desktop — в `mcpServers` тот же `command` + `args`, при желании ключ/креды через `env`.

## Безопасность

- Команды X23 ограничены whitelist в `lib/xrumer.py`; неизвестная команда или поле —
  отказ с читаемым текстом (ToolError), а не выполнение.
- Ключ вендора и SMB-пароль в аргументы/логи/ответы не попадают.
- `xrumer_start` помечен destructive: агенту предписано показать оператору проект, базу
  и потоки и получить согласие до запуска постинга.
- `smb_share: "C$"` даёт доступ ко всему диску ноды; при желании сузить — отдельная
  шара на папку X23 и `smb_root` под неё.

## Автопилот целей (аналог GSA `--autopilot`)

`xrumer_autopilot.py` доливает свежие цели в базу XRumer так же, как GSA доливает
основные проекты, но под модель XRumer «одна база на копию». Запускается с шары,
где лежит общий пул A-Parser.

**Схема:** общий пул → атомарный захват батча → дедуп URL → дозапись в локальную
базу-мастер → выгрузка целой базы в `C:\Links\<base>` ноды по SMB.

**Координация с GSA (общий пул).** Батч забирается физическим переносом из
`pool_dir` в `used_dir`, ровно как node-side автопилот GSA. Перенос атомарен:
батч, забранный GSA (по SMB) или XRumer (локально), второй участник не увидит.
Общий журнал не нужен — перенос и есть координация. Каждый батч уходит либо в
GSA, либо в XRumer; харвест делится между ними.

**Одна большая база.** Автопилот владеет одним файлом `C:\Links\<base>`
(по умолчанию `Autopilot.txt`). Кураторские базы (Posting/Trusted/Profiles) он не
трогает. Локальная копия-мастер на шаре (`local_base_dir/<node>/<base>`) даёт
дешёвую дозапись; на ноду уходит целый файл заливкой. Рост ограничен
`max_base_lines` (обрезка старых строк с головы). XRumer сам дедупит базу при
загрузке, поэтому здесь дедуп только в пределах прогона.

**Конфиг** — подсекция `xrumer.autopilot` (см. `config.example.json`):
`pool_dir`, `used_dir`, `batch_glob`, `batch_limit_mb`, `base`, `local_base_dir`,
`max_base_lines`.

**Запуск** (с шары):

```bash
python xrumer_autopilot.py --node gsa-03            # dry-run: ничего не трогает
python xrumer_autopilot.py --node gsa-03 --apply    # боевой долив
```

По умолчанию dry-run: показывает, сколько батчей и URL забрал бы, без переноса и
заливки. Расписание — по крону/таймеру на шаре, как удобно (аналог того, как
node-side автопилот GSA дёргается по расписанию).

**Что остаётся на ноде (руками/GUI, один раз).** XRumer не умеет перечитывать базу
по команде коннектора. Поэтому оператор один раз выбирает базу `Autopilot.txt` в
окне программы, а подхват дозаписанных целей обеспечивает планировщик XRumer или
периодический перезапуск задания постинга. Долив файла автопилот делает сам,
перечитывание базы — за планировщиком ноды.
