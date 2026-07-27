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

Заявка (POST /api/tasks) — один объект ИЛИ массив (батч):
  {"url":"https://site/", "country":"Poland",
   "anchors":["a1","a2","a3","a4"], "links_per_day":10}
После обработки запроса шлётся ОДИН отчёт в Telegram (список проектов ✅/❌+код,
тег intake_report_mention для ручного refresh GSA).

Эндпоинты:
  GET  /health                 -> {ok}                              (без токена)
  GET  /api/countries          -> {regions:[рус], english_fallback:[англ]}  (токен)
  POST /api/tasks              -> {task_id, status, project}         (токен)
  GET  /api/tasks/<id>         -> статус заявки                       (токен)
  GET  /api/tasks              -> последние заявки                    (токен)
"""

from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
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

# Кастомные регионы для заявок (поверх split1404 SUMMARY_ORDER) — объединения/переименования:
#   «Африка» = Южная Африка + Другие страны Африки (две базы объединяются в .targets);
#   «Европа» ← Другие страны Европы; «Азия» ← Другие страны Азии.
CUSTOM_REGIONS: dict[str, list[str]] = {
    "Африка": ["africa.txt", "Other-Africa.txt"],
    "Европа": ["Europe-Other.txt"],
    "Азия":   ["Asia-other.txt"],
}
# нормализованные имена SUMMARY_ORDER, заменённые кастомными (убираем из /api/countries)
HIDDEN_REGIONS = {"южная африка", "другие страны африки",
                  "другие страны европы", "другие страны азии"}


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


def _norm_region(s: str) -> str:
    """Нормализация имени региона: срезать ведущие эмодзи/символы, lower, strip."""
    s = str(s or "").strip().lower()
    while s and not s[0].isalpha():
        s = s[1:].lstrip()
    return s


def _region_map() -> dict[str, str]:
    """Русское имя региона (как в split1404 SUMMARY_ORDER) → имя файла-бакета."""
    from lib import buckets
    return {_norm_region(label): f for f, label in buckets.SUMMARY_ORDER
            if f != buckets.NOT_STATED_FILE}


def region_files(cfg: dict, country: str) -> list[str]:
    """Регион (рус.) → список путей к файлам-бакетам (>1 у объединённых, напр. «Африка»).
    Приоритет: кастомные (CUSTOM_REGIONS) → split1404 SUMMARY_ORDER → англ. имя страны.
    [] если не сопоставлено."""
    from lib import buckets
    bdir = Path(cfg.get("buckets_dir") or (DATA_DIR / "out_country_buckets"))
    key = _norm_region(country)
    files: list[str] | None = None
    for disp, fs in CUSTOM_REGIONS.items():
        if _norm_region(disp) == key:
            files = fs
            break
    if files is None:
        f = _region_map().get(key)                              # split1404 (рус.)
        if not f:  # английское имя файла-бакета (стем): latam/USA/china-mix/…
            f = {p.stem.lower(): p.name for p in bdir.glob("*.txt")}.get(key)
        if not f:  # англ. имя страны через resolve_country
            g = buckets.bucket_for_country(buckets.resolve_country(country))
            f = g if g and g != buckets.NOT_STATED_FILE else None
        files = [f] if f else []
    return [str(bdir / f) for f in files if (bdir / f).is_file()]


def english_aliases(cfg: dict) -> list[str]:
    """Английские имена (стемы файлов-бакетов) — фолбэк-варианты для country."""
    from lib import buckets
    bdir = Path(cfg.get("buckets_dir") or (DATA_DIR / "out_country_buckets"))
    if not bdir.is_dir():
        return []
    return sorted(p.name[:-4] for p in bdir.glob("*.txt") if p.name != buckets.NOT_STATED_FILE)


def valid_countries(cfg: dict) -> list[str]:
    """Русские названия регионов для заявок: split1404 (минус заменённые) + кастомные."""
    from lib import buckets
    bdir = Path(cfg.get("buckets_dir") or (DATA_DIR / "out_country_buckets"))
    out = []
    for f, label in buckets.SUMMARY_ORDER:
        if f == buckets.NOT_STATED_FILE or not (bdir / f).is_file():
            continue
        name = label.split(" ", 1)[1] if " " in label else label   # без эмодзи
        if _norm_region(name) in HIDDEN_REGIONS:
            continue                                                # заменён кастомным
        out.append(name)
    for disp, fs in CUSTOM_REGIONS.items():
        if any((bdir / f).is_file() for f in fs):
            out.append(disp)
    return out


def validate_task(cfg: dict, body: dict) -> tuple[dict, list[str]]:
    """→ (clean, errors). clean: url, country, bucket(файл), anchors[list], links_per_day."""
    errors: list[str] = []
    url = str(body.get("url", "")).strip()
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        errors.append("url: нужен http(s)://…")

    country = str(body.get("country", "")).strip()
    blist = region_files(cfg, country) if country else []
    if not country:
        errors.append("country: обязателен")
    elif not blist:
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
    return {"url": url, "country": country, "buckets": blist,
            "anchors": anchors, "links_per_day": lpd}, []


def _safe_label(url: str) -> str:
    host = (urlparse(url).netloc or "site").replace(":", "_")
    # только ASCII: GSA не переваривает кириллицу в имени проекта → показывает пустым
    return "".join(c for c in host if (c.isalnum() and c.isascii()) or c in ".-_") or "site"


def _ascii_country(task: dict) -> str:
    """ASCII-метка страны для имени проекта: стем файла-бакета (Malaysia.txt→Malaysia;
    у объединённых — первый). Русское имя региона в имя проекта не попадает."""
    for bp in task.get("buckets") or []:
        stem = "".join(c for c in Path(bp).stem
                       if (c.isalnum() and c.isascii()) or c in ".-_ ").strip()
        if stem:
            return stem
    return "geo"


def build_project(cfg: dict, task: dict) -> tuple[bool, str, str]:
    """Зовёт gsa_checker.py --create фикс. argv. → (ok, project_name, message)."""
    project = f"boost - {_safe_label(task['url'])} - {_ascii_country(task)}"
    out_dir = cfg.get("intake_out_dir") or "/srv/share/intake/pending"
    template = cfg.get("intake_template") or cfg.get("gsa_template_prj") or DEFAULT_TEMPLATE
    buckets_ = task["buckets"]
    tmp = None
    if len(buckets_) == 1:
        targets_arg = buckets_[0]
    else:  # объединить несколько баз в один temp .targets (дедуп) — напр. «Африка»
        import tempfile
        seen, lines = set(), []
        for bp in buckets_:
            for ln in Path(bp).read_text(encoding="utf-8", errors="replace").splitlines():
                ln = ln.strip()
                if ln and ln not in seen:
                    seen.add(ln)
                    lines.append(ln)
        tf = tempfile.NamedTemporaryFile("w", suffix=".targets", delete=False, encoding="utf-8")
        tf.write("\n".join(lines))
        tf.close()
        targets_arg = tmp = tf.name
    argv = [sys.executable, str(ROOT / "gsa_checker.py"), "--create",
            "--name", project, "--url", task["url"],
            "--links-per-day", str(task["links_per_day"]),
            "--targets", targets_arg, "--template", template,
            "--out", out_dir, "--force"]
    for a in task["anchors"]:
        argv += ["--anchor", a]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, project, f"create не запущен: {exc}"
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if r.returncode != 0:
        return False, project, (r.stderr or r.stdout or "create rc!=0").strip()[-500:]
    return True, project, (r.stdout or "").strip()[-500:]


def _tg_send(cfg: dict, text: str) -> bool:
    """Плоская отправка в Telegram (без parse_mode — @упоминания и ссылки авто-линкуются)."""
    bt = cfg.get("telegram_bot_token")
    chat = cfg.get("intake_report_chat_id") or cfg.get("telegram_chat_id")
    if not (bt and chat):
        return False
    import urllib.error
    import urllib.request
    url = f"https://api.telegram.org/bot{bt}/sendMessage"
    data = json.dumps({"chat_id": chat, "text": text,
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except (urllib.error.URLError, OSError) as exc:
        log.warning("intake: отчёт в Telegram не ушёл: %s", exc)
        return False


def build_report(records: list[dict], mention: str = "") -> str:
    """Один отчёт на запрос: список проектов с ✅/❌+код; тег для ручного refresh."""
    n = len(records)
    ok = sum(1 for r in records if r["status"] == "queued")
    head = f"🤖 intake · заявка обработана: {n} проект(ов) — ✅ {ok}"
    if ok < n:
        head += f" / ⚠ {n - ok}"
    lines = [head]
    for r in records:
        if r["status"] == "queued":
            lines.append(f"✅ {r['url']} → {r['country']}  ({r['task_id']})")
        else:
            tail = f"{r.get('code') or ''} {(r.get('error') or '')[:90]}".strip()
            lines.append(f"❌ {r['url']} → {r.get('country') or '?'} — {tail}")
    if ok and mention.strip():
        lines.append(f"\n{mention.strip()} — проекты добавлены, нужен ручной refresh GSA (gsa-02).")
    return "\n".join(lines)


def _process_one(cfg: dict, body, src_ip: str) -> dict:
    """Валидирует + создаёт один проект; сохраняет запись и аудит. → запись результата."""
    task_id = "t_" + uuid.uuid4().hex[:10]
    clean, errors = validate_task(cfg, body if isinstance(body, dict) else {})
    if errors:
        b = body if isinstance(body, dict) else {}
        rec = {"task_id": task_id, "ts": int(time.time()), "url": str(b.get("url", "")),
               "country": str(b.get("country", "")), "status": "invalid", "code": 400,
               "error": "; ".join(errors), "project": None, "from": src_ip}
    else:
        ok, project, msg = build_project(cfg, clean)
        rec = {"task_id": task_id, "ts": int(time.time()), "url": clean["url"],
               "country": clean["country"], "anchors": clean["anchors"],
               "links_per_day": clean["links_per_day"], "project": project,
               "status": "queued" if ok else "error", "code": None if ok else 500,
               "error": None if ok else msg, "note": msg, "from": src_ip}
    _save_task(rec)
    _audit({"event": "task", "task_id": task_id, "status": rec["status"],
            "project": rec.get("project"), "from": src_ip})
    return rec


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
        # сравниваем в байтах: compare_digest со str падает на не-ASCII (роняет поток запроса)
        return bool(got) and hmac.compare_digest(got.encode("utf-8", "surrogatepass"),
                                                 self.token.encode("utf-8", "surrogatepass"))

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
            self._send(200, {"regions": valid_countries(self.cfg),
                             "english_fallback": english_aliases(self.cfg)})
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
            length = min(int(self.headers.get("Content-Length", 0)), 200_000)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad json"})
            return

        batch = isinstance(body, list)          # заявка может быть одна (dict) или пачкой (list)
        tasks = body if batch else [body]
        if not tasks or len(tasks) > 100:
            self._send(400, {"error": "нужно 1..100 задач"})
            return

        records = [_process_one(self.cfg, t, self.client_address[0]) for t in tasks]
        if self.cfg.get("intake_report", True):     # один отчёт на весь запрос
            _tg_send(self.cfg, build_report(records, str(self.cfg.get("intake_report_mention", ""))))

        if batch:
            self._send(200, {
                "queued": sum(1 for r in records if r["status"] == "queued"),
                "errors": sum(1 for r in records if r["status"] != "queued"),
                "results": [{"task_id": r["task_id"], "status": r["status"],
                             "project": r.get("project"), "error": r.get("error")} for r in records]})
            return
        r = records[0]
        code = {"queued": 202, "invalid": 400}.get(r["status"], 500)
        out = {"task_id": r["task_id"], "status": r["status"]}
        if r["status"] == "queued":
            out["project"] = r["project"]
        else:
            out["error"] = r["error"]
        self._send(code, out)


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
