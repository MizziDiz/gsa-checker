#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent.py — HTTP control-агент ноды для панели управления gsa-checker.

Маленький authenticated HTTP-сервис на КАЖДОЙ ноде: control plane (сайт/оркестратор)
вызывает его напрямую, чтобы запускать разрешённые gsa-checker-команды и снимать статус.
Только stdlib — работает на Windows-ноде без pip.

Безопасность (обязательно к пониманию перед выкладкой):
  • ТОЛЬКО whitelist действий — action → фиксированный argv, без shell, без подстановки
    пользовательских аргументов. Произвольные команды выполнить нельзя.
  • Bearer-токен на все действия (кроме /health), сравнение constant-time. Токен — в
    gitignored data/gsa_checker.config.json (ключ `agent_token`), в коде/логе не светится.
  • Аудит каждого вызова в data/agent_audit.jsonl.
  • Bind по умолчанию 127.0.0.1 — держать за VPN/файрволом; LAN/VPN-интерфейс задавать
    явно (`agent_bind`). Длинные действия (autopilot/report) выполняются в фоне: /run
    отдаёт job_id, статус — по /job/<id>.

Запуск:  python agent.py            # bind/token/actions — из конфига
Эндпоинты:
  GET  /health                 -> {node, ok, time}                 (без токена)
  GET  /actions                -> список разрешённых действий      (токен)
  GET  /status                 -> остаток/последний забор/свежесть (токен)
  POST /run   {"action": "..."} -> {job_id}   (gsa-действия + системные, напр. git-pull)
  GET  /job/<id>               -> {status, rc, tail, started, ...}  (токен)
  GET  /config                 -> текущие автопилот-ключи (whitelist)        (токен)
  POST /config {"set": {...}}  -> запись автопилот-ключей (только whitelist)  (токен)

Системные действия (git-pull) и правка автопилот-конфига НЕ ломают whitelist-модель:
git-pull — фиксированный argv (`git -C <dir> pull --ff-only`) по папкам из agent_git_dirs;
/config пишет ТОЛЬКО ключи из AUTOPILOT_KEYS (маски/пороги), пути и секреты не трогает.
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "gsa_checker.config.json"
JOBS_DIR = DATA_DIR / "agent_jobs"
AUDIT_PATH = DATA_DIR / "agent_audit.jsonl"

log = logging.getLogger("gsa_agent")

# Дефолтный whitelist: имя действия → аргументы к gsa_checker.py (БЕЗ shell, фиксированные).
# Мутирующие .prj команды (respin/settings/emails) сюда НЕ входят — их при желании
# оператор добавляет явно через agent_actions в конфиге.
DEFAULT_ACTIONS: dict[str, list[str]] = {
    "remaining":    ["--remaining", "--json"],
    "stats":        ["--stats", "--json"],
    "report-dry":   ["--report", "--dry-run"],
    "report":       ["--report"],
    "autopilot-dry": ["--autopilot", "--dry-run"],
    "autopilot":    ["--autopilot", "--apply"],
    "emails-dry":   ["--emails", "--dry-run"],
    "emails":       ["--emails", "--apply"],
    "collect":      ["--collect-success"],
    "backup":       ["--backup", "--only", "Split"],
}

# Ключи автопилота, разрешённые к удалённой правке из панели (маски + пороги).
# Пути (*_dir), секреты и exe СЮДА НЕ входят — их меняют только на самой ноде.
AUTOPILOT_KEYS: dict[str, str] = {
    "autopilot_include_names":  "list[str]",   # маска проектов для заполнения
    "autopilot_exclude_names":  "list[str]",   # маска исключений
    "autopilot_min_targets":    "int",         # порог долива таргетов
    "autopilot_batch_limit_mb": "number",      # размер батча, МБ
    "autopilot_append_ext":     "str",         # расширение файла дозаписи
    "autopilot_batch_glob":     "str",         # маска файлов пула
}

# job_id -> dict(status, rc, action, started, finished, log_path)
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_CONFIG_LOCK = threading.Lock()


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        log.error("agent: битый конфиг %s: %s", CONFIG_PATH, exc)
        return {}


def allowed_actions(cfg: dict) -> dict[str, list[str]]:
    """Дефолтный whitelist + расширения из cfg['agent_actions'] (значение — список строк)."""
    actions = dict(DEFAULT_ACTIONS)
    extra = cfg.get("agent_actions") or {}
    if isinstance(extra, dict):
        for name, args in extra.items():
            if isinstance(args, list) and all(isinstance(a, str) for a in args):
                actions[str(name)] = args
            else:
                log.warning("agent: пропущено кривое действие %r (нужен список строк)", name)
    return actions


def git_dirs(cfg: dict) -> list[str]:
    """Папки для `git-pull`. По умолчанию — папка агента (напр. A-GSA)."""
    dirs = cfg.get("agent_git_dirs")
    if isinstance(dirs, list):
        got = [str(d) for d in dirs if isinstance(d, str) and d.strip()]
        if got:
            return got
    return [str(ROOT)]


def system_actions(cfg: dict) -> dict[str, list[list[str]]]:
    """Системные действия (не gsa_checker): имя → список ФИКС. команд (argv, без shell)."""
    return {"git-pull": [["git", "-C", d, "pull", "--ff-only"] for d in git_dirs(cfg)]}


def _validate_autopilot(updates: dict) -> tuple[dict, list[str]]:
    """Проверяет обновления автопилот-конфига по whitelist + типам. → (clean, errors)."""
    clean: dict = {}
    errors: list[str] = []
    for k, v in (updates or {}).items():
        kind = AUTOPILOT_KEYS.get(k)
        if kind is None:
            errors.append(f"{k}: ключ не разрешён")
        elif kind == "list[str]" and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errors.append(f"{k}: ожидается список строк")
        elif kind == "str" and not isinstance(v, str):
            errors.append(f"{k}: ожидается строка")
        elif kind == "int" and (isinstance(v, bool) or not isinstance(v, int)):
            errors.append(f"{k}: ожидается целое")
        elif kind == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            errors.append(f"{k}: ожидается число")
        else:
            clean[k] = v
    return clean, errors


def _audit(entry: dict) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _start_job(action: str, cmds: list[list[str]]) -> str:
    """Запускает одну/несколько ФИКС. команд последовательно в фоне. Возвращает job_id.

    Останавливается на первой команде с ненулевым rc. Всё без shell (argv-списки).
    """
    job_id = uuid.uuid4().hex[:12]
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = JOBS_DIR / f"{job_id}.log"
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "running", "rc": None, "action": action,
                         "started": int(time.time()), "finished": None,
                         "log_path": str(log_path)}

    def run() -> None:
        rc = 0
        try:
            with log_path.open("wb") as out:
                for argv in cmds:
                    out.write(("$ " + " ".join(argv) + "\n").encode("utf-8"))
                    out.flush()
                    rc = subprocess.run(argv, stdout=out, stderr=subprocess.STDOUT,
                                        cwd=str(ROOT)).returncode
                    if rc != 0:
                        break
        except OSError as exc:
            rc = -1
            with log_path.open("ab") as out:
                out.write(f"agent: не запустить: {exc}\n".encode("utf-8"))
        with _JOBS_LOCK:
            _JOBS[job_id].update(status="done", rc=rc, finished=int(time.time()))
        _audit({"event": "job_done", "job_id": job_id, "action": action, "rc": rc})

    threading.Thread(target=run, daemon=True).start()
    return job_id


def _job_view(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        job = dict(job)
    tail = ""
    try:
        data = Path(job["log_path"]).read_bytes()[-4000:]
        tail = data.decode("utf-8", "replace")
    except OSError:
        pass
    return {"job_id": job_id, "status": job["status"], "rc": job["rc"],
            "action": job["action"], "started": job["started"],
            "finished": job["finished"], "tail": tail}


def _node_status(cfg: dict) -> dict:
    """Лёгкий статус ноды для панели: имя, последний забор автопилота, метка сбора."""
    name = str(cfg.get("server_name", "node"))
    out: dict = {"node": name, "time": int(time.time())}
    # последняя строка autopilot-статистики этой ноды на шаре
    stats_dir = cfg.get("autopilot_stats_dir")
    if stats_dir:
        p = Path(stats_dir) / f"{name}.jsonl"
        try:
            lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                out["last_autopilot"] = json.loads(lines[-1])
        except (OSError, json.JSONDecodeError):
            pass
    # активных фоновых заданий
    with _JOBS_LOCK:
        out["jobs_running"] = sum(1 for j in _JOBS.values() if j["status"] == "running")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "gsa-agent/1"
    cfg: dict = {}
    token: str = ""

    def log_message(self, fmt, *a):          # заглушаем стандартный шумный лог
        log.debug("agent: " + fmt, *a)

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if not self.token:                    # без заданного токена агент не отвечает на действия
            return False
        got = self.headers.get("Authorization", "")
        prefix = "Bearer "
        got = got[len(prefix):] if got.startswith(prefix) else ""
        return bool(got) and hmac.compare_digest(got, self.token)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, {"node": self.cfg.get("server_name", "node"),
                             "ok": True, "time": int(time.time())})
            return
        if not self._authed():
            _audit({"event": "deny", "path": path, "from": self.client_address[0]})
            self._send(401, {"error": "unauthorized"})
            return
        if path == "/actions":
            names = sorted(list(allowed_actions(self.cfg)) + list(system_actions(self.cfg)))
            self._send(200, {"actions": names})
        elif path == "/status":
            self._send(200, _node_status(self.cfg))
        elif path == "/config":
            cfg = load_config()                       # читаем свежий файл, не кэш
            self._send(200, {k: cfg.get(k) for k in AUTOPILOT_KEYS})
        elif path.startswith("/job/"):
            view = _job_view(path[len("/job/"):])
            self._send(200 if view else 404, view or {"error": "no such job"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._authed():
            _audit({"event": "deny", "path": path, "from": self.client_address[0]})
            self._send(401, {"error": "unauthorized"})
            return
        if path not in ("/run", "/config"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 20_000)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return

        if path == "/run":
            action = str(payload.get("action", ""))
            gsa = allowed_actions(self.cfg)
            sysa = system_actions(self.cfg)
            if action in gsa:
                cmds = [[sys.executable, str(ROOT / "gsa_checker.py"), *gsa[action]]]
            elif action in sysa:
                cmds = sysa[action]
            else:
                _audit({"event": "run_denied", "action": action, "from": self.client_address[0]})
                self._send(403, {"error": "action not allowed",
                                 "allowed": sorted(list(gsa) + list(sysa))})
                return
            job_id = _start_job(action, cmds)
            _audit({"event": "run", "action": action, "job_id": job_id,
                    "from": self.client_address[0]})
            self._send(202, {"job_id": job_id, "action": action})
            return

        # path == "/config": запись ТОЛЬКО whitelist-ключей автопилота, атомарно
        clean, errors = _validate_autopilot(payload.get("set") or {})
        if errors:
            self._send(400, {"error": "validation", "detail": errors})
            return
        if not clean:
            self._send(400, {"error": "no valid keys in 'set'"})
            return
        with _CONFIG_LOCK:
            cfg = load_config()
            cfg.update(clean)
            tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, CONFIG_PATH)
            type(self).cfg = cfg                      # обновить кэш агента
        _audit({"event": "config_set", "keys": sorted(clean), "from": self.client_address[0]})
        self._send(200, {"ok": True, "applied": clean})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="gsa-checker node control agent")
    ap.add_argument("--bind", help="host:port (по умолчанию из agent_bind или 127.0.0.1:8787)")
    args = ap.parse_args()

    cfg = load_config()
    token = str(cfg.get("agent_token", "")).strip()
    if not token:
        sys.exit("Не задан agent_token в data/gsa_checker.config.json — агент не запускаю "
                 "(без токена он бы принимал команды без аутентификации).")
    bind = args.bind or cfg.get("agent_bind", "127.0.0.1:8787")
    host, _, port = bind.partition(":")
    Handler.cfg = cfg
    Handler.token = token

    httpd = ThreadingHTTPServer((host or "127.0.0.1", int(port or 8787)), Handler)
    log.info("gsa-agent слушает %s:%s (нода %s); действий в whitelist: %d",
             host or "127.0.0.1", port or 8787, cfg.get("server_name", "node"),
             len(allowed_actions(cfg)))
    log.info("⚠ держите агент за VPN/файрволом; наружу — только с сильным agent_token.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
