#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lib/xrumer.py — XRumer 23 StrongAI («X23»): коннектор вендора + файлы по SMB.

ВАЖНО ПРО СЛОВО «MCP». У вендора XRumer есть фича «MCP-коннектор» (папка C:\\MCP на
ноде, ключ `xmcp_…`). Это ЕГО СОБСТВЕННЫЙ HTTP-протокол через сервер botmasterru.com,
НЕ Model Context Protocol. Этот модуль — клиент к нему (VendorConnector) и ничего
больше. Настоящий MCP-сервер (Model Context Protocol), через который ИИ-агент
управляет XRumer инструментами, — `xrumer_mcp.py` в корне репо; он использует этот модуль.

Два слоя, оба только stdlib (+ внешний бинарь `smbclient` для файлов):

1. **Коннектор вендора** (`VendorConnector`) — штатный «пульт» X23. Программа раз в ~10 с
   отстукивает на сервер вендора (botmasterru.com / botmasterlabs.net) по
   персональному ключу `xmcp_…`; мы кладём команды в очередь (`a=push`) и читаем
   состояние (`a=state`) и результаты (`a=result`). Документация — `C:\\MCP\\help_mcp_rus.htm`
   на ноде, образец — `C:\\MCP\\mcp_client.py`. Правила вендора, которые тут зашиты:
     • заголовок `x-connector` обязателен;
     • опрашивать не чаще раза в ~10 с — поэтому state/sessions/result кэшируются
       (`POLL_MIN_INTERVAL`) и один клиент на процесс;
     • три РАЗНЫХ неверных ключа с одного IP = бан на 24 ч — ключ берётся только из
       файла/конфига, никаких переборов;
     • команды исполняются с задержкой 10–25 с — `push_wait` ждёт результат.
   Команды проходят через whitelist `COMMANDS` (имя + типы параметров): наружу
   не уходит произвольный JSON от вызывающего.

2. **Файловый слой** (`SmbFiles`) — то, чего коннектор вендора не умеет: проекты `Projects\\*.xml`,
   базы `Links\\*.txt`, отчёты `Logs\\<проект>\\<база>\\`. Ходит по SMB (админ-шара C$
   или выделенная) через `smbclient -A <cred>`; имена файлов проверяются, `..` и
   кавычки не пропускаются. Запись — только новых файлов, если не сказано overwrite;
   текущий открытый в X23 проект перезаписывать нельзя (X23 перепишет его при выходе).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

VENDOR_HOSTS = {"ru": "http://botmasterru.com", "int": "http://botmasterlabs.net"}
VENDOR_PATH = "/test.mcp23.php"
# Значение заголовка x-connector — из образца вендора MCP\mcp_client.py (обязателен).
CONNECTOR = hashlib.md5(b"x23mcp_5b8e1c9d4f7a20e6").hexdigest()
POLL_MIN_INTERVAL = 9.0        # сек; вендор просит не чаще раза в ~10 с на действие
RESULT_POLL_EVERY = 5.0        # сек между опросами a=result в push_wait
KEY_RE = re.compile(r"xmcp_[A-Za-z0-9]{8,}")

# Whitelist команд X23 (метод → {параметр: допустимые типы}). Всё, чего тут нет,
# вендору НЕ уходит. Источник — таблица «Команды для X23» в help_mcp_rus.htm.
COMMANDS: dict[str, dict[str, tuple]] = {
    "run.continue":   {},
    "run.stop":       {},
    "threads.set":    {"value": (int,)},
    "project.get":    {},
    "project.set":    {"field": (str,), "value": (str,)},
    "options.set":    {"name": (str,), "value": (str, int, bool)},
    "llm.get":        {},
    "llm.set":        None,      # None = произвольные строковые/числовые/булевы поля
    "screenshot.get": {},
}
THREADS_MAX = 1000

# Поля проекта: имя у вендора (project.get/set) ↔ тег в Projects\<имя>.xml.
PROJECT_FIELDS: dict[str, str] = {
    "nick": "NickName", "real": "RealName", "pass": "Password",
    "email": "EmailAddress", "homepage": "Homepage",
    "subject1": "Subject1", "subject2": "Subject2",
    "city": "City", "country": "Country", "occupation": "Occupation",
    "interests": "Interests", "signature": "Signature", "text": "PostText",
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.\- ()\[\]#+@,а-яА-ЯёЁ]{1,120}$")


class VendorError(Exception):
    """Ошибка канала вендора (сеть, отказ сервера, неверный ключ, таймаут команды, smbclient)."""


class BadCommand(ValueError):
    """Команда/параметры не проходят whitelist."""


# ───────────────────────────── ключ ─────────────────────────────

def read_key(cfg_xr: dict, root: Path) -> str:
    """Ключ коннектора вендора из `key` или файла `key_file` (относительно root). Только `xmcp_…`."""
    raw = str(cfg_xr.get("key") or "")
    if not raw and cfg_xr.get("key_file"):
        p = Path(str(cfg_xr["key_file"]))
        if not p.is_absolute():
            p = root / p
        try:
            raw = p.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise VendorError(f"не прочитан key_file {p}: {exc}") from exc
    m = KEY_RE.search(raw)
    if not m:
        raise VendorError("ключ вендора не найден (ожидается xmcp_…)")
    return m.group(0)


def mask_key(key: str) -> str:
    return (key[:8] + "…" + key[-4:]) if len(key) > 12 else "***"


# ───────────────────────────── whitelist ─────────────────────────────

def validate_command(m: Any, p: Any) -> tuple[str, dict]:
    """Проверяет метод и параметры по COMMANDS; возвращает нормализованную пару."""
    if not isinstance(m, str) or m not in COMMANDS:
        raise BadCommand(f"команда не в whitelist: {m!r}")
    spec = COMMANDS[m]
    p = p or {}
    if not isinstance(p, dict):
        raise BadCommand("параметры должны быть объектом")
    if spec is None:                                  # llm.set: плоский объект скаляров
        for k, v in p.items():
            if not isinstance(k, str) or not isinstance(v, (str, int, float, bool)):
                raise BadCommand(f"llm.set: недопустимое поле {k!r}")
        return m, dict(p)
    extra = set(p) - set(spec)
    if extra:
        raise BadCommand(f"{m}: лишние параметры {sorted(extra)}")
    out: dict = {}
    for k, types in spec.items():
        if k not in p:
            raise BadCommand(f"{m}: нет параметра {k!r}")
        v = p[k]
        if isinstance(v, bool) and bool not in types:   # bool — подкласс int, не пропускаем
            raise BadCommand(f"{m}: {k} — неверный тип")
        if not isinstance(v, types):
            raise BadCommand(f"{m}: {k} — неверный тип ({type(v).__name__})")
        out[k] = v
    if m == "threads.set" and not (1 <= out["value"] <= THREADS_MAX):
        raise BadCommand(f"threads.set: value вне 1..{THREADS_MAX}")
    if m == "project.set" and out["field"] not in PROJECT_FIELDS:
        raise BadCommand(f"project.set: неизвестное поле {out['field']!r}")
    return m, out


# ───────────────────────────── коннектор вендора ─────────────────────────────

class VendorConnector:
    """Клиент к «MCP-коннектору» вендора (см. шапку модуля). Один на процесс: внутри кэш опросов."""

    def __init__(self, key: str, host: str = "ru", timeout: float = 20.0,
                 base_url: str | None = None, min_interval: float = POLL_MIN_INTERVAL):
        if base_url is None:
            if host not in VENDOR_HOSTS:
                raise VendorError(f"неизвестный host {host!r} (ru|int)")
            base_url = VENDOR_HOSTS[host]
        self.url = base_url.rstrip("/") + VENDOR_PATH
        self.key = key
        self.timeout = timeout
        self.min_interval = min_interval
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    # низкий уровень
    def _request(self, action: str, params: dict | None = None, sid: str | None = None,
                 raw: bool = False) -> Any:
        body: dict = {"a": action, "key": self.key}
        if sid:
            body["sid"] = sid
        body.update(params or {})
        req = urllib.request.Request(
            self.url, data=urllib.parse.urlencode(body).encode("utf-8"),
            headers={"x-connector": CONNECTOR, "Accept": "*/*",
                     "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            data = e.read() or b""
            if e.code == 403 and data.strip().startswith(b"{"):
                # структурный отказ (например ip_blocked) — отдаём как ответ, не как сбой
                pass
            else:
                raise VendorError(f"вендор: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise VendorError(f"сервер вендора недоступен: {type(e).__name__}") from e
        if raw:
            return data
        head = data[:64].lstrip().lower()
        if head.startswith(b"<!doctype") or head.startswith(b"<html"):
            raise VendorError("вендор: HTML вместо JSON (не тот адрес?)")
        try:
            return json.loads(data.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError as e:
            raise VendorError("вендор: не JSON в ответе") from e

    def _cached(self, action: str, sid: str | None, max_age: float | None = None) -> Any:
        """Опросные действия кэшируются на min_interval — вендор режет частые опросы."""
        ttl = self.min_interval if max_age is None else max_age
        slot = (action, sid or "")
        with self._lock:
            hit = self._cache.get(slot)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
        val = self._request(action, sid=sid)
        with self._lock:
            self._cache[slot] = (time.monotonic(), val)
        return val

    # высокий уровень
    def sessions(self, max_age: float | None = None) -> list[dict]:
        r = self._cached("sessions", None, max_age)
        if not r.get("ok", True):
            raise VendorError(f"sessions: {r.get('error') or r}")
        return list(r.get("sessions") or [])

    def default_sid(self) -> str | None:
        """Первая онлайн-копия (или первая вообще). sid меняется с PID при перезапуске X23."""
        ss = self.sessions()
        for s in ss:
            if s.get("online"):
                return s.get("sid")
        return ss[0].get("sid") if ss else None

    def state(self, sid: str | None = None, max_age: float | None = None) -> dict:
        r = self._cached("state", sid, max_age)
        if not r.get("ok", True):
            raise VendorError(f"state: {r.get('error') or r}")
        return r

    def push(self, m: str, p: dict | None = None, sid: str | None = None) -> str:
        m, p = validate_command(m, p)
        r = self._request("push", {"cmd": json.dumps({"m": m, "p": p}, ensure_ascii=False)}, sid=sid)
        if not r.get("ok") or not r.get("id"):
            raise VendorError(f"push {m}: {r.get('error') or r}")
        return str(r["id"])

    def results(self, sid: str | None = None) -> dict:
        r = self._request("result", sid=sid)
        if isinstance(r, dict) and r.get("ok") is False:
            raise VendorError(f"result: {r.get('error') or r}")
        return {k: v for k, v in r.items() if isinstance(v, dict) and "id" in v} if isinstance(r, dict) else {}

    def wait(self, ids: list[str], sid: str | None = None, timeout: float = 45.0) -> dict[str, dict]:
        """Ждёт результаты команд по id (опрос a=result каждые RESULT_POLL_EVERY с)."""
        want = set(ids)
        got: dict[str, dict] = {}
        deadline = time.monotonic() + timeout
        while want and time.monotonic() < deadline:
            time.sleep(min(RESULT_POLL_EVERY, max(0.0, deadline - time.monotonic())))
            for k, v in self.results(sid).items():
                if k in want:
                    got[k] = v
                    want.discard(k)
        for k in want:
            got[k] = {"id": k, "ok": False, "msg": f"нет результата за {int(timeout)} с "
                                                  "(X23 не забрал команду?)", "timeout": True}
        return got

    def push_wait(self, m: str, p: dict | None = None, sid: str | None = None,
                  timeout: float = 45.0) -> dict:
        cid = self.push(m, p, sid)
        return self.wait([cid], sid, timeout)[cid]

    def push_many_wait(self, cmds: list[tuple[str, dict]], sid: str | None = None,
                       timeout: float = 90.0) -> list[dict]:
        """Несколько команд в очередь разом (X23 исполняет по порядку), один общий wait."""
        ids = [self.push(m, p, sid) for m, p in cmds]
        got = self.wait(ids, sid, timeout)
        return [got[i] for i in ids]

    def shot(self, sid: str | None = None) -> bytes:
        """Последний присланный скриншот (JPEG). Перед этим нужен push screenshot.get."""
        data = self._request("shot", sid=sid, raw=True)
        if not data or data[:2] != b"\xff\xd8":
            raise VendorError("shot: скриншот ещё не пришёл")
        return data


def summarize_state(st: dict) -> dict:
    """Короткая выжимка состояния для панели/Telegram (без списка провайдеров LLM)."""
    s = st.get("state") or {}
    run, rep, llm = s.get("run") or {}, s.get("reports") or {}, s.get("llm") or {}
    return {
        "online": bool(st.get("_online")), "age": st.get("_age"), "sid": s.get("sid") or st.get("_sid"),
        "label": s.get("sid_label"), "host": s.get("host"), "ver": s.get("ver"),
        "project": s.get("project"), "base": s.get("base"),
        "job": run.get("job"), "threads": run.get("active_threads"), "max_threads": run.get("max_threads"),
        "pos": run.get("pos"), "total": run.get("total"), "speed": run.get("speed"),
        "success": rep.get("success"), "half": rep.get("half"), "profiles": rep.get("profiles"),
        "proxy": (s.get("proxy") or {}).get("count"), "proxy_use": (s.get("proxy") or {}).get("use"),
        "errors": s.get("errors"), "schedule": s.get("schedule"),
        "llm": {"provider": llm.get("provider"), "model": llm.get("model"),
                "key_set": llm.get("key_set"), "tokens_used": llm.get("tokens_used")},
    }


# ───────────────────────────── файлы по SMB ─────────────────────────────

_LS_RE = re.compile(r"^\s{2}(?P<name>.+?)\s{2,}(?P<attr>[A-Za-z]*)\s+(?P<size>\d+)\s+"
                    r"(?P<mtime>\w{3}\s+\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\d{4})\s*$")


def parse_ls(text: str) -> list[dict]:
    """Разбор вывода `smbclient -c ls`: [{name, size, mtime, is_dir}] без . и .."""
    out = []
    for line in text.splitlines():
        m = _LS_RE.match(line)
        if not m or m["name"] in (".", ".."):
            continue
        out.append({"name": m["name"], "size": int(m["size"]), "mtime": m["mtime"],
                    "is_dir": "D" in m["attr"]})
    return out


def safe_name(name: Any) -> str:
    """Имя файла проекта/базы: без разделителей, кавычек и `..`."""
    if not isinstance(name, str) or not _SAFE_NAME.match(name) or ".." in name:
        raise ValueError(f"недопустимое имя: {name!r}")
    return name


class SmbFiles:
    """Папка X23 на ноде через smbclient. Пути внутри шары — с обратным слешем."""

    def __init__(self, host: str, cred_file: str | Path, share: str = "C$", root: str = "",
                 smbclient: str = "smbclient", timeout: float = 90.0):
        self.host, self.share, self.cred = host, share, str(cred_file)
        self.root = root.strip("\\/")
        self.smbclient, self.timeout = smbclient, timeout

    def _path(self, rel: str) -> str:
        rel = rel.strip("\\/")
        return f"{self.root}\\{rel}" if self.root else rel

    def _run(self, cmd: str) -> str:
        argv = [self.smbclient, f"//{self.host}/{self.share}", "-A", self.cred, "-c", cmd]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout,
                               errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VendorError(f"smbclient: {exc}") from exc
        if r.returncode != 0:
            err = (r.stdout + r.stderr).strip().splitlines()
            raise VendorError("smbclient: " + (err[-1] if err else f"rc {r.returncode}"))
        return r.stdout

    def listdir(self, rel: str, pattern: str = "*") -> list[dict]:
        return parse_ls(self._run(f'ls "{self._path(rel)}\\{pattern}"'))

    def exists(self, rel: str) -> bool:
        try:
            return bool(parse_ls(self._run(f'ls "{self._path(rel)}"')))
        except VendorError:
            return False

    def get_bytes(self, rel: str) -> bytes:
        with tempfile.TemporaryDirectory() as td:
            local = Path(td) / "f.bin"
            self._run(f'lcd "{td}"; get "{self._path(rel)}" f.bin')
            return local.read_bytes()

    def put_bytes(self, rel: str, data: bytes, overwrite: bool = False) -> None:
        if not overwrite and self.exists(rel):
            raise FileExistsError(f"уже есть: {rel}")
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "f.bin").write_bytes(data)
            self._run(f'lcd "{td}"; put f.bin "{self._path(rel)}"')

    # X23-специфика
    def projects(self) -> list[dict]:
        return [f for f in self.listdir("Projects", "*.xml") if not f["is_dir"]]

    def bases(self) -> list[dict]:
        return [f for f in self.listdir("Links", "*.txt") if not f["is_dir"]]

    def logs(self, project: str | None = None) -> dict:
        """Logs\\<проект>\\<база>\\<файлы>: сводка размеров, 2 уровня вниз."""
        out: dict = {}
        tops = [{"name": safe_name(project), "is_dir": True}] if project else self.listdir("Logs")
        for t in tops:
            if not t["is_dir"]:
                continue
            out[t["name"]] = {}
            for b in self.listdir(f"Logs\\{t['name']}"):
                if b["is_dir"]:
                    files = [f for f in self.listdir(f"Logs\\{t['name']}\\{b['name']}") if not f["is_dir"]]
                    out[t["name"]][b["name"]] = {f["name"]: f["size"] for f in files}
                else:
                    out[t["name"]][b["name"]] = b["size"]
        return out

    def read_project(self, name: str) -> dict:
        xml = self.get_bytes(f"Projects\\{safe_name(name)}.xml")
        return parse_project_xml(xml)

    def write_project(self, name: str, fields: dict, template: str = "Template",
                      overwrite: bool = False, current: str | None = None) -> str:
        """Новый Projects\\<name>.xml из шаблона + полей (ключи как в PROJECT_FIELDS)."""
        name = safe_name(name)
        if current and name == current and not overwrite:
            raise FileExistsError(f"проект {name!r} сейчас открыт в X23 — файл перепишется при выходе")
        tpl = self.get_bytes(f"Projects\\{safe_name(template)}.xml")
        self.put_bytes(f"Projects\\{name}.xml", build_project_xml(tpl, name, fields), overwrite)
        return f"Projects\\{name}.xml"


def parse_project_xml(data: bytes) -> dict:
    root = ET.fromstring(data)
    out = {"name": (root.findtext("PrimarySection/ProjectName") or "").strip()}
    for key, tag in PROJECT_FIELDS.items():
        el = root.find(f"PrimarySection/{tag}")
        if el is None:
            el = root.find(f"SecondarySection/{tag}")
        out[key] = (el.text or "") if el is not None else ""
    out["prior"] = root.findtext("SecondarySection/Prior") or ""
    return out


def build_project_xml(template: bytes, name: str, fields: dict) -> bytes:
    """Шаблон → новый проект: ProjectName = name, поля из `fields` (ключи PROJECT_FIELDS)."""
    unknown = set(fields) - set(PROJECT_FIELDS)
    if unknown:
        raise ValueError(f"неизвестные поля проекта: {sorted(unknown)}")
    root = ET.fromstring(template)
    pn = root.find("PrimarySection/ProjectName")
    if pn is None:
        raise ValueError("шаблон без PrimarySection/ProjectName")
    pn.text = name
    for key, val in fields.items():
        if not isinstance(val, str):
            raise ValueError(f"поле {key}: ожидается строка")
        tag = PROJECT_FIELDS[key]
        el = root.find(f"PrimarySection/{tag}")
        if el is None:
            el = root.find(f"SecondarySection/{tag}")
        if el is None:
            raise ValueError(f"в шаблоне нет тега {tag}")
        el.text = val
        # у X23 логин почты = адрес; держим их согласованными, как в шаблоне
        if key == "email":
            lg = root.find("PrimarySection/EmailLogin")
            if lg is not None:
                lg.text = val
    ET.indent(root, space="  ")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


# ───────────────────────────── сборка из конфига ─────────────────────────────

def from_config(cfg: dict, root: Path) -> tuple[VendorConnector | None, SmbFiles | None, dict]:
    """Секция `xrumer` конфига → (коннектор вендора | None, файлы | None, сама секция).

    {"host": "ru"|"int", "key_file": "data/ops/xrumer_mcp.key", "node": "gsa-03",
     "smb_host": "176.123.10.21", "smb_share": "C$", "smb_cred": "data/ops/smb_gsa-03.cred",
     "smb_root": ""}
    """
    xr = dict(cfg.get("xrumer") or {}) if isinstance(cfg.get("xrumer"), dict) else {}
    for env, key in (("XRUMER_KEY_FILE", "key_file"), ("XRUMER_HOST", "host"),
                     ("XRUMER_SMB_HOST", "smb_host"), ("XRUMER_SMB_CRED", "smb_cred"),
                     ("XRUMER_SMB_SHARE", "smb_share"), ("XRUMER_SMB_ROOT", "smb_root")):
        if os.environ.get(env):
            xr[key] = os.environ[env]
    if not xr:
        return None, None, {}
    client = None
    if xr.get("key") or xr.get("key_file"):
        client = VendorConnector(read_key(xr, root), host=str(xr.get("host") or "ru"))
    files = None
    if xr.get("smb_host") and xr.get("smb_cred"):
        cred = Path(str(xr["smb_cred"]))
        if not cred.is_absolute():
            cred = root / cred
        files = SmbFiles(str(xr["smb_host"]), cred, share=str(xr.get("smb_share") or "C$"),
                         root=str(xr.get("smb_root") or ""))
    return client, files, xr
