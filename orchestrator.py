#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""orchestrator.py — control plane: один API на шаре поверх node-агентов.

Знает все ноды (реестр `nodes` в конфиге: name → url агента + token), фан-аутит команды и
агрегирует статус. Сайт/панель (роль master) вызывает ТОЛЬКО оркестратор — он уже ходит к
`agent.py` каждой ноды. Только stdlib.

Безопасность:
  • свой Bearer-токен (`orchestrator_token`) на все действия, кроме /health; сравнение
    constant-time; токен — в gitignored `data/gsa_checker.config.json`, в код/логи не идёт.
  • оркестратор НЕ выполняет произвольных команд — он лишь ПЕРЕДАЁТ имя действия агенту,
    а тот сам сверяет со своим whitelist (защита в глубину).
  • токены нод хранятся в конфиге, наружу (в /nodes) НЕ отдаются.
  • аудит в `data/orchestrator_audit.jsonl`; bind по умолчанию 127.0.0.1 (за Tunnel/VPN).

Запуск:  python orchestrator.py
Эндпоинты:
  GET  /health                          -> {ok, nodes}                     (без токена)
  GET  /nodes                           -> [{name, url}]                    (токен)
  GET  /status                          -> {name: <agent /status | error>} (токен, параллельно)
  POST /run  {"target":"name|all","action":"..."} -> {name: {job_id|error}} (токен)
  GET  /job/<node>/<job_id>             -> проксирует agent /job/<id>       (токен)
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "gsa_checker.config.json"
AUDIT_PATH = DATA_DIR / "orchestrator_audit.jsonl"

log = logging.getLogger("gsa_orchestrator")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        log.error("orchestrator: битый конфиг %s: %s", CONFIG_PATH, exc)
        return {}


def node_registry(cfg: dict) -> dict[str, dict]:
    """`nodes` из конфига → {name: {url, token}}. Кривые записи пропускаются."""
    reg: dict[str, dict] = {}
    for n in cfg.get("nodes") or []:
        if isinstance(n, dict) and n.get("name") and n.get("url"):
            reg[str(n["name"])] = {"url": str(n["url"]).rstrip("/"),
                                   "token": str(n.get("token", ""))}
    return reg


def _audit(entry: dict) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _call_agent(node: dict, path: str, method: str = "GET",
                payload: dict | None = None, timeout: float = 10.0) -> dict:
    """Запрос к агенту ноды. Возвращает разобранный JSON или {"error": ...}."""
    url = node["url"] + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(url, data=data, method=method)
    if node.get("token"):
        req.add_header("Authorization", f"Bearer {node['token']}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except HTTPError as e:
        try:
            return {"error": f"http {e.code}", "detail": json.loads(e.read() or b"{}")}
        except (ValueError, json.JSONDecodeError):
            return {"error": f"http {e.code}"}
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as e:
        return {"error": f"unreachable: {type(e).__name__}"}


def _fanout(reg: dict[str, dict], path: str, method: str = "GET",
            payload: dict | None = None) -> dict:
    """Параллельно опрашивает все ноды, возвращает {name: результат}."""
    out: dict[str, dict] = {}
    lock = threading.Lock()

    def worker(name: str, node: dict) -> None:
        res = _call_agent(node, path, method, payload)
        with lock:
            out[name] = res

    threads = [threading.Thread(target=worker, args=(n, node), daemon=True)
               for n, node in reg.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "gsa-orchestrator/1"
    cfg: dict = {}
    token: str = ""

    def log_message(self, fmt, *a):
        log.debug("orchestrator: " + fmt, *a)

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        if not self.token:
            return False
        got = self.headers.get("Authorization", "")
        got = got[7:] if got.startswith("Bearer ") else ""
        return bool(got) and hmac.compare_digest(got, self.token)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        reg = node_registry(self.cfg)
        if path == "/health":
            self._send(200, {"ok": True, "nodes": len(reg), "time": int(time.time())})
            return
        if not self._authed():
            _audit({"event": "deny", "path": path, "from": self.client_address[0]})
            self._send(401, {"error": "unauthorized"})
            return
        if path == "/nodes":
            self._send(200, [{"name": n, "url": node["url"]} for n, node in reg.items()])
        elif path == "/status":
            self._send(200, _fanout(reg, "/status"))
        elif path.startswith("/job/"):
            parts = path[len("/job/"):].split("/", 1)
            if len(parts) != 2 or parts[0] not in reg:
                self._send(404, {"error": "no such node/job"})
                return
            self._send(200, _call_agent(reg[parts[0]], f"/job/{parts[1]}"))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._authed():
            _audit({"event": "deny", "path": path, "from": self.client_address[0]})
            self._send(401, {"error": "unauthorized"})
            return
        if path != "/run":
            self._send(404, {"error": "not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 10_000)
            payload = json.loads(self.rfile.read(length) or b"{}")
            action = str(payload.get("action", ""))
            target = str(payload.get("target", "all"))
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return
        if not action:
            self._send(400, {"error": "no action"})
            return
        reg = node_registry(self.cfg)
        if target != "all" and target not in reg:
            self._send(404, {"error": f"no such node: {target}"})
            return
        chosen = reg if target == "all" else {target: reg[target]}
        _audit({"event": "run", "action": action, "target": target,
                "from": self.client_address[0]})
        # оркестратор лишь передаёт имя действия — whitelist проверяет агент
        result = _fanout(chosen, "/run", "POST", {"action": action})
        self._send(200, result)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="gsa-checker control-plane orchestrator")
    ap.add_argument("--bind", help="host:port (по умолчанию orchestrator_bind или 127.0.0.1:8790)")
    args = ap.parse_args()

    cfg = load_config()
    token = str(cfg.get("orchestrator_token", "")).strip()
    if not token:
        sys.exit("Не задан orchestrator_token в data/gsa_checker.config.json — не запускаю "
                 "(без токена оркестратор принимал бы команды без аутентификации).")
    reg = node_registry(cfg)
    if not reg:
        log.warning("orchestrator: реестр `nodes` пуст — добавьте ноды в конфиг.")
    bind = args.bind or cfg.get("orchestrator_bind", "127.0.0.1:8790")
    host, _, port = bind.partition(":")
    Handler.cfg = cfg
    Handler.token = token

    httpd = ThreadingHTTPServer((host or "127.0.0.1", int(port or 8790)), Handler)
    log.info("gsa-orchestrator слушает %s:%s; нод в реестре: %d",
             host or "127.0.0.1", port or 8790, len(reg))
    log.info("⚠ наружу — только через Cloudflare Tunnel/VPN и с сильным orchestrator_token.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
