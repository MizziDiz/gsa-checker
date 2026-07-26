#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intake.py — API приёма заявок на прокачку GSA (для внешних систем, напр. Антона).

Внешняя система шлёт заявку по HTTP; сервис валидирует, генерирует готовый к импорту
GSA-проект (через `gsa_checker.py --create`: URL + анкоры + лимит ссылок/день + база
нужной страны как .targets) и кладёт бандл в очередь на шаре. Дальше нода-исполнитель
(gsa-02) импортирует его в живой GSA (см. --import-boost / действие агента). Только stdlib.

Безопасность:
  • Bearer-токен (`intake_token`) на все /api/* (кроме /health); сравнение constant-time.
  • НИКАКОГО shell/eval с данными заявки — параметры уходят в gsa_checker.py фикс. argv.
  • bind по умолчанию 127.0.0.1 (наружу — через Cloudflare Tunnel/VPN с сильным токеном).
  • аудит в data/intake_audit.jsonl.

Заявка (POST /api/tasks):
  {"url":"https://site/", "country":"Poland",
   "anchors":["a1","a2","a3","a4"], "links_per_day":10}

Эндпоинты:
  GET  /health                 -> {ok}                              (без токена)
  GET  /api/countries          -> [валидные значения country]        (токен)
  POST /api/tasks              -> {task_id, status, project}         (токен)
  GET  /api/tasks/<id>         -> статус заявки                       (токен)
  GET  /api/tasks              -> последние заявки                    (токен)
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "gsa_checker.config.json"
AUDIT_PATH = DATA_DIR / "intake_audit.jsonl"
TASKS_PATH = DATA_DIR / "intake_tasks.jsonl"

log = logging.getLogger("gsa_intake")

MAX_ANCHORS = 8
MAX_LINKS_PER_DAY = 1000
DEFAULT_TEMPLATE = "/srv/share/Spin-generator/templates/template.prj"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        log.error("intake: битый конфиг %s: %s", CONFIG_PATH, exc)
        return {}


def _audit(entry: dict) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _save_task(rec: dict) -> None:
    try:
        TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TASKS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_tasks() -> list[dict]:
    out = []
    if TASKS_PATH.exists():
        for line in TASKS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def country_bucket(cfg: dict, country: str) -> str | None:
    """country ('Poland'/'brazil'/'latam'…) → путь к файлу-бакету базы или None."""
    from lib import buckets
    bdir = Path(cfg.get("buckets_dir") or (DATA_DIR / "out_country_buckets"))
    fname = buckets.bucket_for_country(buckets.resolve_country(country))
    if not fname or fname == buckets.NOT_STATED_FILE:
        return None
    p = bdir / fname
    return str(p) if p.is_file() else None


def valid_countries(cfg: dict) -> list[str]:
    """Имена доступных стран (по существующим файлам-бакетам, без Not Stated)."""
    from lib import buckets
    bdir = Path(cfg.get("buckets_dir") or (DATA_DIR / "out_country_buckets"))
    if not bdir.is_dir():
        return []
    ns = buckets.NOT_STATED_FILE
    return sorted(p.name[:-4] for p in bdir.glob("*.txt") if p.name != ns)


def validate_task(cfg: dict, body: dict) -> tuple[dict, list[str]]:
    """→ (clean, errors). clean: url, country, bucket(файл), anchors[list], links_per_day."""
    errors: list[str] = []
    url = str(body.get("url", "")).strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        errors.append("url: нужен http(s)://…")

    country = str(body.get("country", "")).strip()
    bucket = country_bucket(cfg, country) if country else None
    if not country:
        errors.append("country: обязателен")
    elif not bucket:
        errors.append(f"country: '{country}' не сопоставлен с базой (см. GET /api/countries)")

    anchors = body.get("anchors")
    if not isinstance(anchors, list) or not (1 <= len(anchors) <= MAX_ANCHORS):
        errors.append(f"anchors: список 1..{MAX_ANCHORS} строк")
        anchors = []
    else:
        anchors = [str(a).strip() for a in anchors if str(a).strip()]
        if not anchors:
            errors.append("anchors: пустые")

    lpd = body.get("links_per_day", 0)
    try:
        lpd = int(lpd)
    except (TypeError, ValueError):
        lpd = 0
    if not (1 <= lpd <= MAX_LINKS_PER_DAY):
        errors.append(f"links_per_day: целое 1..{MAX_LINKS_PER_DAY}")

    if errors:
        return {}, errors
    return {"url": url, "country": country, "bucket": bucket,
            "anchors": anchors, "links_per_day": lpd}, []


def _safe_label(url: str) -> str:
    host = (urlparse(url).netloc or "site").replace(":", "_")
    return "".join(c for c in host if c.isalnum() or c in ".-_") or "site"


def build_project(cfg: dict, task: dict) -> tuple[bool, str, str]:
    """Зовёт gsa_checker.py --create фикс. argv. → (ok, project_name, message)."""
    project = f"boost - {_safe_label(task['url'])} - {task['country']}"
    out_dir = cfg.get("intake_out_dir") or "/srv/share/intake/pending"
    template = cfg.get("intake_template") or cfg.get("gsa_template_prj") or DEFAULT_TEMPLATE
    argv = [sys.executable, str(ROOT / "gsa_checker.py"), "--create",
            "--name", project, "--url", task["url"],
            "--links-per-day", str(task["links_per_day"]),
            "--targets", task["bucket"], "--template", template,
            "--out", out_dir, "--force"]
    for a in task["anchors"]:
        argv += ["--anchor", a]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, project, f"create не запущен: {exc}"
    if r.returncode != 0:
        return False, project, (r.stderr or r.stdout or "create rc!=0").strip()[-500:]
    return True, project, (r.stdout or "").strip()[-500:]


class Handler(BaseHTTPRequestHandler):
    server_version = "gsa-intake/1"
    cfg: dict = {}
    token: str = ""

    def log_message(self, fmt, *a):
        log.debug("intake: " + fmt, *a)

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
        if path == "/health":
            self._send(200, {"ok": True, "time": int(time.time())})
            return
        if not self._authed():
            _audit({"event": "deny", "path": path, "from": self.client_address[0]})
            self._send(401, {"error": "unauthorized"})
            return
        if path == "/api/countries":
            self._send(200, valid_countries(self.cfg))
        elif path == "/api/tasks":
            self._send(200, _read_tasks()[-50:])
        elif path.startswith("/api/tasks/"):
            tid = path[len("/api/tasks/"):]
            rec = next((t for t in reversed(_read_tasks()) if t.get("task_id") == tid), None)
            self._send(200 if rec else 404, rec or {"error": "no such task"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._authed():
            _audit({"event": "deny", "path": path, "from": self.client_address[0]})
            self._send(401, {"error": "unauthorized"})
            return
        if path != "/api/tasks":
            self._send(404, {"error": "not found"})
            return
        try:
            length = min(int(self.headers.get("Content-Length", 0)), 20_000)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return

        clean, errors = validate_task(self.cfg, body)
        if errors:
            self._send(400, {"error": "validation", "detail": errors})
            return

        task_id = "t_" + uuid.uuid4().hex[:10]
        ok, project, msg = build_project(self.cfg, clean)
        rec = {"task_id": task_id, "ts": int(time.time()),
               "url": clean["url"], "country": clean["country"],
               "anchors": clean["anchors"], "links_per_day": clean["links_per_day"],
               "project": project, "status": "queued" if ok else "error",
               "note": msg, "from": self.client_address[0]}
        _save_task(rec)
        _audit({"event": "task", "task_id": task_id, "project": project,
                "ok": ok, "from": self.client_address[0]})
        if not ok:
            self._send(500, {"task_id": task_id, "status": "error", "error": msg})
            return
        self._send(202, {"task_id": task_id, "status": "queued", "project": project})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="gsa-checker intake API (заявки на прокачку)")
    ap.add_argument("--bind", help="host:port (по умолчанию intake_bind или 127.0.0.1:8791)")
    args = ap.parse_args()

    cfg = load_config()
    token = str(cfg.get("intake_token", "")).strip()
    if not token:
        sys.exit("Не задан intake_token в data/gsa_checker.config.json — не запускаю "
                 "(без токена API принимал бы заявки без аутентификации).")
    bind = args.bind or cfg.get("intake_bind", "127.0.0.1:8791")
    host, _, port = bind.partition(":")
    Handler.cfg = cfg
    Handler.token = token

    httpd = ThreadingHTTPServer((host or "127.0.0.1", int(port or 8791)), Handler)
    log.info("gsa-intake слушает %s:%s; страны: %d; очередь: %s",
             host or "127.0.0.1", port or 8791, len(valid_countries(cfg)),
             cfg.get("intake_out_dir") or "/srv/share/intake/pending")
    log.info("⚠ наружу — только через Cloudflare Tunnel/VPN и с сильным intake_token.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
