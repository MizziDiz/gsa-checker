#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xrumer_mcp.py — настоящий MCP-сервер (Model Context Protocol) для XRumer 23 («X23»).

Даёт ИИ-агенту (Claude Desktop, Claude Code, любой MCP-клиент) управлять XRumer как
набором инструментов: статус, старт/стоп, потоки, поля проекта, файлы проектов и баз,
логи, скриншот. Под капотом — `lib/xrumer.py`:
  • команды X23 идут через фирменный «MCP-коннектор» вендора (сервер botmasterru.com,
    ключ `xmcp_…`) — это НЕ Model Context Protocol, а внутренний транспорт;
  • файлы проектов/баз/логов — по SMB с ноды.

Транспорт MCP: stdio (по умолчанию) или streamable-http (для доступа через туннель).

Конфиг берётся из data/gsa_checker.config.json (секция `xrumer`) и/или переменных
окружения (XRUMER_KEY_FILE, XRUMER_HOST, XRUMER_SMB_HOST, XRUMER_SMB_CRED, …) — см.
lib.xrumer.from_config. Ключ и SMB-пароль в аргументы/логи не попадают.

Запуск:
  stdio (для локального ИИ-клиента):   python xrumer_mcp.py
  http  (за туннелем):                 python xrumer_mcp.py --http --port 8792

Регистрация в Claude Code:
  claude mcp add xrumer -- /root/.venvs/xrumer-mcp/bin/python /root/gsa-checker/xrumer_mcp.py

Зависимость: пакет `mcp` (официальный SDK). Всё остальное — stdlib + smbclient.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib import xrumer as xr  # noqa: E402

try:
    from mcp.server.mcpserver import Image, MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
except ModuleNotFoundError as exc:                          # noqa: BLE001
    sys.exit("Нужен пакet mcp (официальный MCP SDK): "
             "python -m venv .venv && .venv/bin/pip install 'mcp>=2' — " + str(exc))

CONFIG_PATH = ROOT / "data" / "gsa_checker.config.json"


def _load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


# Один коннектор и один файловый слой на процесс: у вендора лимит на частоту опроса,
# а VendorConnector внутри держит кэш state/sessions/result. Ленивое создание —
# чтобы сервер поднимался даже при недоступном ключе, а ошибка всплывала в вызове.
_HANDLES: dict = {}


def _handles() -> tuple[xr.VendorConnector | None, xr.SmbFiles | None]:
    if "ready" not in _HANDLES:
        client, files, section = xr.from_config(_load_cfg(), ROOT)
        _HANDLES.update(ready=True, client=client, files=files, section=section)
    return _HANDLES["client"], _HANDLES["files"]


def _need_vendor() -> xr.VendorConnector:
    client, _ = _handles()
    if client is None:
        raise xr.VendorError("коннектор вендора не настроен: задайте xrumer.key_file "
                             "(или XRUMER_KEY_FILE) — см. lib/xrumer.py")
    return client


def _need_files() -> xr.SmbFiles:
    _, files = _handles()
    if files is None:
        raise xr.VendorError("файловый слой не настроен: задайте xrumer.smb_host/smb_cred "
                             "(или XRUMER_SMB_*) — см. lib/xrumer.py")
    return files


def _current_project(client: xr.VendorConnector, sid: str | None) -> str | None:
    try:
        return (client.state(sid).get("state") or {}).get("project")
    except xr.VendorError:
        return None


def _guard(fn):
    """Ожидаемые ошибки (сеть вендора, whitelist, файлы) → ToolError: их текст доходит до
    модели, чтобы она поправилась сама. Всё прочее SDK прячет как внутренний сбой."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except (xr.VendorError, xr.BadCommand, ValueError, FileExistsError, FileNotFoundError) as exc:
            raise ToolError(str(exc)) from exc
    return wrapper


RO = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WR = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)

srv = MCPServer(
    name="xrumer",
    version="1.0.0",
    instructions=(
        "Управление XRumer 23 (X23) — программой автопостинга на форумы/блоги. "
        "Команды исполняются с задержкой 10–25 секунд (X23 выходит на связь раз в ~10 с), "
        "поэтому инструменты уже ждут результата и могут отвечать не мгновенно. "
        "Если копий X23 несколько — сначала xrumer_sessions, потом передавай sid. "
        "Старт постинга (xrumer_start) — боевое действие: перед ним покажи оператору "
        "текущий проект, базу и число потоков через xrumer_state и получи подтверждение. "
        "Тексты постов и темы редактируются как поля проекта (subject1/subject2/text) "
        "со спинтаксом {вариант1|вариант2} и макросами X23."
    ),
)


# ───────────────────────── статус и сессии (read-only) ─────────────────────────

@srv.tool(annotations=RO,
          description="Список запущенных копий X23 на связи (у одного ключа их может быть "
                      "несколько). Возвращает sid, метку (хост+PID), онлайн и возраст отстука. "
                      "sid меняется при перезапуске X23 — бери его отсюда, не запоминай.")
@_guard
def xrumer_sessions() -> dict:
    return {"sessions": _need_vendor().sessions()}


@srv.tool(annotations=RO,
          description="Полное состояние копии X23: проект, база, режим постинга (job), потоки, "
                      "прогресс, счётчики (успешные/профили), прокси, ошибки, настройки LLM. "
                      "Поле summary — короткая выжимка. sid не обязателен: берётся первая онлайн-копия.")
@_guard
def xrumer_state(sid: str | None = None) -> dict:
    st = _need_vendor().state(sid or None)
    return {"summary": xr.summarize_state(st), "raw": st.get("state") or {}}


@srv.tool(annotations=RO,
          description="Поля проекта, СЕЙЧАС загруженного в X23 (ник, e-mail, тема, текст поста и т.д.), "
                      "прочитанные из самой программы, а не из файла. Для чтения файла проекта с ноды "
                      "используй xrumer_read_project.")
@_guard
def xrumer_get_project(sid: str | None = None) -> dict:
    res = _need_vendor().push_wait("project.get", {}, sid or None)
    if not res.get("ok"):
        raise xr.VendorError(f"project.get: {res.get('msg') or res}")
    return res.get("data") or {}


@srv.tool(annotations=RO,
          description="Настройки нейросети (LLM) в X23: провайдер, модель, задан ли ключ, режим "
                      "перевода, израсходованные токены, список доступных провайдеров.")
@_guard
def xrumer_get_llm(sid: str | None = None) -> dict:
    res = _need_vendor().push_wait("llm.get", {}, sid or None)
    if not res.get("ok"):
        raise xr.VendorError(f"llm.get: {res.get('msg') or res}")
    return res.get("data") or {}


# ───────────────────────── управление постингом (write) ─────────────────────────

@srv.tool(annotations=DESTRUCTIVE,
          description="Запустить/продолжить постинг в X23 (боевое действие: X23 начнёт слать "
                      "сообщения по текущей базе). Перед вызовом покажи оператору проект, базу и "
                      "потоки (xrumer_state) и получи явное согласие. Возвращает результат от X23.")
@_guard
def xrumer_start(sid: str | None = None) -> dict:
    return _need_vendor().push_wait("run.continue", {}, sid or None)


@srv.tool(annotations=WR,
          description="Остановить постинг в X23. Безопасно вызывать в любой момент.")
@_guard
def xrumer_stop(sid: str | None = None) -> dict:
    return _need_vendor().push_wait("run.stop", {}, sid or None)


@srv.tool(annotations=WR,
          description="Задать максимальное число потоков постинга (1..1000). Применяется на лету.")
@_guard
def xrumer_set_threads(value: int, sid: str | None = None) -> dict:
    return _need_vendor().push_wait("threads.set", {"value": int(value)}, sid or None)


@srv.tool(annotations=WR,
          description="Изменить одно поле проекта, СЕЙЧАС загруженного в X23 (в самой программе, "
                      "не в файле). Допустимые field: nick, real, pass, email, homepage, "
                      "subject1, subject2, city, country, occupation, interests, signature, text. "
                      "В subject/text можно спинтакс {а|б} и макросы X23. Изменить несколько полей "
                      "разом — xrumer_set_fields.")
@_guard
def xrumer_set_field(field: str, value: str, sid: str | None = None) -> dict:
    return _need_vendor().push_wait("project.set", {"field": field, "value": str(value)}, sid or None)


@srv.tool(annotations=WR,
          description="Изменить несколько полей текущего проекта X23 разом. fields — объект "
                      "{имя_поля: значение}; допустимые имена как в xrumer_set_field. Команды идут "
                      "в очередь X23 по порядку под один общий ожидатель.")
@_guard
def xrumer_set_fields(fields: dict, sid: str | None = None) -> dict:
    if not isinstance(fields, dict) or not fields:
        raise xr.BadCommand("fields: непустой объект {поле: значение}")
    cmds = [xr.validate_command("project.set", {"field": k, "value": str(v)})
            for k, v in fields.items()]
    res = _need_vendor().push_many_wait(cmds, sid or None)
    return {"ok": all(r.get("ok") for r in res), "results": dict(zip(fields, res))}


@srv.tool(annotations=WR,
          description="Изменить настройки нейросети (LLM) в X23: например provider, model, key, "
                      "trans_mode, temperature, tokens. settings — объект {имя: значение}. "
                      "Осторожно: сюда можно передать API-ключ LLM — он уйдёт в X23 через сервер вендора.")
@_guard
def xrumer_set_llm(settings: dict, sid: str | None = None) -> dict:
    if not isinstance(settings, dict) or not settings:
        raise xr.BadCommand("settings: непустой объект")
    return _need_vendor().push_wait("llm.set", settings, sid or None)


# ───────────────────────── файлы на ноде (SMB) ─────────────────────────

@srv.tool(annotations=RO,
          description="Список файлов X23 на ноде по SMB: проекты (Projects\\*.xml), базы ссылок "
                      "(Links\\*.txt) с размерами и сводка логов (Logs\\<проект>\\<база>). "
                      "Это то, чего коннектор вендора не отдаёт.")
@_guard
def xrumer_list_files() -> dict:
    f = _need_files()
    return {"projects": f.projects(), "bases": f.bases(), "logs": f.logs()}


@srv.tool(annotations=RO,
          description="Прочитать файл проекта Projects\\<name>.xml с ноды по SMB и вернуть его поля. "
                      "name — имя проекта без .xml.")
@_guard
def xrumer_read_project(name: str) -> dict:
    return _need_files().read_project(name)


@srv.tool(annotations=RO,
          description="Логи по конкретному проекту: Logs\\<name>\\<база>\\<файлы> с размерами "
                      "(successful/failed/profiles и т.п.). name — имя проекта.")
@_guard
def xrumer_project_logs(name: str) -> dict:
    return _need_files().logs(name)


@srv.tool(annotations=WR,
          description="Создать НОВЫЙ файл проекта Projects\\<name>.xml на ноде из шаблона "
                      "(по умолчанию Template) с заданными полями fields ({поле: значение}, имена "
                      "как в xrumer_set_field). По умолчанию не перезаписывает существующий и "
                      "отказывается трогать проект, открытый сейчас в X23 (он затрётся при выходе). "
                      "overwrite=true снимает первую защиту.")
@_guard
def xrumer_create_project(name: str, fields: dict | None = None, template: str = "Template",
                          overwrite: bool = False) -> dict:
    fields = fields or {}
    if not isinstance(fields, dict):
        raise xr.BadCommand("fields: объект {поле: значение}")
    client, _ = _handles()
    current = _current_project(client, None) if client is not None else None
    where = _need_files().write_project(name, fields, template=template,
                                        overwrite=bool(overwrite), current=current)
    return {"ok": True, "path": where}


@srv.tool(annotations=WR,
          description="Загрузить поля из файла проекта Projects\\<name>.xml (с ноды) в X23, "
                      "СЕЙЧАС открытый: читает файл по SMB и применяет его поля через project.set. "
                      "Сменить сам активный проект X23 через коннектор нельзя — это переносит "
                      "содержимое в текущий проект программы.")
@_guard
def xrumer_apply_project(name: str, sid: str | None = None) -> dict:
    f = _need_files()
    client = _need_vendor()
    prj = f.read_project(name)
    fields = {k: prj[k] for k in xr.PROJECT_FIELDS if k in prj and prj[k] != ""}
    cmds = [("project.set", {"field": k, "value": v}) for k, v in fields.items()]
    res = client.push_many_wait(cmds, sid or None)
    return {"ok": all(r.get("ok") for r in res), "name": name,
            "applied": dict(zip(fields, res))}


# ───────────────────────── скриншот ─────────────────────────

@srv.tool(annotations=RO,
          description="Скриншот окна X23 (для проверки, что видит оператор). Просит X23 сделать "
                      "снимок и ждёт его — до ~40 секунд. Возвращает картинку.")
@_guard
def xrumer_screenshot(sid: str | None = None) -> Image:
    import time
    client = _need_vendor()
    res = client.push_wait("screenshot.get", {}, sid or None, timeout=40)
    if not res.get("ok"):
        raise xr.VendorError(f"screenshot: {res.get('msg') or res}")
    time.sleep(2)                       # картинка доезжает до сервера чуть позже результата команды
    return Image(data=client.shot(sid or None), format="jpeg")


def main() -> None:
    ap = argparse.ArgumentParser(description="MCP-сервер для XRumer 23 (X23)")
    ap.add_argument("--http", action="store_true", help="streamable-http вместо stdio")
    ap.add_argument("--host", default="127.0.0.1", help="хост для --http (по умолчанию 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8792, help="порт для --http")
    ap.add_argument("--check", action="store_true",
                    help="не запускать сервер: проверить конфиг и связь, вывести и выйти")
    args = ap.parse_args()

    if args.check:
        client, files = _handles()
        out: dict = {"vendor_configured": client is not None, "smb_configured": files is not None}
        try:
            if client is not None:
                out["sessions"] = client.sessions()
        except xr.VendorError as exc:
            out["vendor_error"] = str(exc)
        try:
            if files is not None:
                out["projects"] = [p["name"] for p in files.projects()]
        except xr.VendorError as exc:
            out["smb_error"] = str(exc)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if args.http:
        srv.run("streamable-http", host=args.host, port=args.port)
    else:
        srv.run("stdio")


if __name__ == "__main__":
    main()
