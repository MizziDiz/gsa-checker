#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""submit_tasks.py — отправка заявок на прокачку в intake API из файла (json/csv/txt).

Читает задачи из файла и шлёт каждую POST /api/tasks. Форматы (авто по расширению):
  JSON — массив объектов, один объект, или JSONL (по объекту на строку):
         {"url":..,"country":"Poland","anchors":["a1","a2","a3","a4"],"links_per_day":10}
  CSV  — заголовок с колонками url,country,anchors,links_per_day
         (anchors — через '|'; либо отдельные колонки anchor1,anchor2,…)
  TXT  — по задаче на строку, поля через TAB:
         url <TAB> country <TAB> анкор1|анкор2|анкор3|анкор4 <TAB> links_per_day
         (строки с '#' и пустые — пропускаются)

Токен: --token → иначе env INTAKE_TOKEN → иначе intake_token из data/gsa_checker.config.json.
Только stdlib.

  python3 submit_tasks.py tasks.json
  python3 submit_tasks.py tasks.csv --api https://api.graniteloom.com --token XXXX
  python3 submit_tasks.py tasks.txt --dry-run        # разобрать/показать, не отправлять
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API = "https://api.graniteloom.com"
ANCHOR_SEP = "|"
# Cloudflare режет дефолтный UA "Python-urllib/*" (403). Любой свой UA проходит.
USER_AGENT = "gsa-submit/1"


def _anchors(val) -> list[str]:
    if isinstance(val, list):
        return [str(a).strip() for a in val if str(a).strip()]
    return [a.strip() for a in str(val or "").split(ANCHOR_SEP) if a.strip()]


def _norm(d: dict) -> dict:
    """Сырые поля (из любого формата) → задача для API."""
    if d.get("anchors") not in (None, ""):
        anchors = _anchors(d["anchors"])
    else:  # отдельные колонки anchor1..anchorN
        anchors = [str(d[k]).strip() for k in sorted(d)
                   if k.lower().startswith("anchor") and str(d[k]).strip()]
    lpd = d.get("links_per_day", d.get("links", d.get("perday", 0)))
    try:
        lpd = int(lpd)
    except (TypeError, ValueError):
        lpd = 0
    return {"url": str(d.get("url", "")).strip(),
            "country": str(d.get("country", "")).strip(),
            "anchors": anchors, "links_per_day": lpd}


def parse_json(text: str) -> list[dict]:
    t = text.strip()
    if t[:1] in "[{":
        try:
            data = json.loads(text)
            return [_norm(x) for x in (data if isinstance(data, list) else [data])]
        except json.JSONDecodeError:
            pass
    out = []  # JSONL
    for ln in text.splitlines():
        ln = ln.strip()
        if ln:
            out.append(_norm(json.loads(ln)))
    return out


def parse_csv(text: str) -> list[dict]:
    rdr = csv.DictReader(text.splitlines())
    return [_norm({(k or "").strip().lower(): v for k, v in row.items()}) for row in rdr]


def parse_txt(text: str) -> list[dict]:
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        parts = ln.split("\t")
        if len(parts) < 4:
            raise ValueError(f"строка {i}: нужно 4 поля через TAB "
                             f"(url⇥country⇥анкоры|⇥links_per_day), получено {len(parts)}")
        out.append(_norm({"url": parts[0], "country": parts[1],
                          "anchors": parts[2], "links_per_day": parts[3]}))
    return out


def load_tasks(path: str, fmt: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    if fmt == "auto":
        fmt = {".json": "json", ".csv": "csv", ".txt": "txt"}.get(Path(path).suffix.lower(), "")
        if not fmt:  # по содержимому
            head = text.lstrip()[:1]
            fmt = "json" if head in "[{" else ("csv" if "," in (text.splitlines() or [""])[0] else "txt")
    return {"json": parse_json, "csv": parse_csv, "txt": parse_txt}[fmt](text)


def local_issues(t: dict) -> list[str]:
    """Лёгкая клиентская проверка (авторитет — API)."""
    e = []
    if not t["url"].startswith(("http://", "https://")):
        e.append("url")
    if not t["country"]:
        e.append("country")
    if not t["anchors"]:
        e.append("anchors")
    if not (1 <= t["links_per_day"] <= 100000):
        e.append("links_per_day")
    return e


def resolve_token(arg_token: str | None) -> str:
    if arg_token:
        return arg_token
    if os.environ.get("INTAKE_TOKEN"):
        return os.environ["INTAKE_TOKEN"]
    cfg = Path(__file__).resolve().parent / "data" / "gsa_checker.config.json"
    if cfg.exists():
        try:
            return str(json.loads(cfg.read_text(encoding="utf-8-sig")).get("intake_token", ""))
        except json.JSONDecodeError:
            pass
    return ""


def post_task(api: str, token: str, task: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        api.rstrip("/") + "/api/tasks",
        data=json.dumps(task).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                 "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except (ValueError, json.JSONDecodeError):
            return e.code, {"error": f"http {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Отправка заявок на прокачку в intake API")
    ap.add_argument("file", help="файл с задачами (.json/.csv/.txt)")
    ap.add_argument("--api", default=os.environ.get("INTAKE_API", DEFAULT_API),
                    help=f"базовый URL API (по умолчанию {DEFAULT_API})")
    ap.add_argument("--token", help="Bearer-токен (иначе env INTAKE_TOKEN / конфиг)")
    ap.add_argument("--format", choices=["auto", "json", "csv", "txt"], default="auto")
    ap.add_argument("--dry-run", action="store_true", help="разобрать и показать, не отправлять")
    args = ap.parse_args()

    try:
        tasks = load_tasks(args.file, args.format)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as e:
        sys.exit(f"Не разобрать {args.file}: {e}")
    if not tasks:
        sys.exit("В файле нет задач.")
    print(f"Задач в файле: {len(tasks)}  (API: {args.api})")

    if args.dry_run:
        for i, t in enumerate(tasks, 1):
            bad = local_issues(t)
            mark = "⚠ " + ",".join(bad) if bad else "ok"
            print(f"  [{i}] {t['url']} · {t['country']} · анкоров {len(t['anchors'])} · "
                  f"{t['links_per_day']}/день  [{mark}]")
        print("[dry-run] не отправлялось.")
        return

    token = resolve_token(args.token)
    if not token:
        sys.exit("Нет токена: задайте --token, env INTAKE_TOKEN или intake_token в конфиге.")

    ok = err = 0
    for i, t in enumerate(tasks, 1):
        code, body = post_task(args.api, token, t)
        if code == 202:
            ok += 1
            print(f"  [{i}] ✓ {body.get('task_id')} — {body.get('project')}")
        else:
            err += 1
            detail = body.get("detail") or body.get("error") or f"http {code}"
            print(f"  [{i}] ✗ {t['url']} ({t['country']}): {detail}")
    print(f"\nИтог: отправлено {ok}, ошибок {err} из {len(tasks)}.")
    sys.exit(1 if err else 0)


if __name__ == "__main__":
    main()
