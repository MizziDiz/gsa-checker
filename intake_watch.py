#!/usr/bin/env python3
"""Сводка по заявкам: принято - обработано - опубликовано, и сигнал о первых ссылках.

Зачем. Отправитель заявки получает подтверждение приёма и дальше тишину: у всех
200 заявок статус так и стоит "queued", после приёма он не меняется никогда.
"Принято", "взято в работу" и "дало ссылки" для отправителя неразличимы.

Откуда берутся три числа.
  Принято     - строка в data/intake_tasks.jsonl.
  Обработано  - файлы проекта уехали из intake/pending в intake/imported,
                то есть GSA забрал проект в работу.
  Опубликовано- строки в <проект>.success на общем диске. В этом хозяйстве
                .success и есть выгрузка verified: gsa_checker считает verified
                ровно как число строк в файлах по маске verified_glob, а по
                умолчанию эта маска - ["*.success"].

Почему не спрашиваем машину напрямую. Агент отдаёт только последние 4000 байт
вывода, а список проектов в stats отсортирован по остатку убыванию: свежие
заявки с большим остатком стоят в начале и обрезаются первыми. Проверено: в
хвосте stats и remaining слово boost не встречается ни разу. Файлы .success
приезжают на общий диск целиком и обрезке не подвержены.

Чем это грозит и чем не грозит. Выгрузка на диск идёт по расписанию, поэтому
числа могут отставать: на 28.08 у gsa-03 на диске 10 958 строк против 11 313 на
самой машине. Отставание способно ЗАДЕРЖАТЬ сигнал, но не может его выдумать:
ненулевое число строк означает, что ссылки действительно есть. Поэтому ноль
здесь честно называется "пока не видно", а не "ссылок нет".

Три состояния источника различаются намеренно и не сливаются в ноль:
  выгрузки нет вообще - про машину НЕ ИЗВЕСТНО НИЧЕГО;
  выгрузка устарела   - число верное на свою дату, новее могло появиться;
  проекта нет нигде   - в конвейере его не видно, это не "ссылок нет".

Сообщение о первой ссылке уходит ОДИН раз на заявку: отметка лежит на диске,
повторный запуск не повторяет отправку.

  intake_watch.py            сводка, при появлении ссылок отправить
  intake_watch.py --dry-run  то же, показать текст, но не отправлять
  intake_watch.py --list     только сводка
  intake_watch.py --full     показать и заявки без движения
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/gsa-checker")
CFG = ROOT / "data" / "gsa_checker.config.json"
TASKS = ROOT / "data" / "intake_tasks.jsonl"
STATE = ROOT / "data" / "intake_watch_state.json"

PENDING = Path("/srv/share/intake/pending")
IMPORTED = Path("/srv/share/intake/imported")
SUCCESS = Path("/srv/share/gsa_success")
# Выгрузки boost-исполнителя (gsa-02) должны лежать отдельно от недельного отчёта —
# в gsa_success_boost; наблюдатель читает и их. Отложенная папка gsa_success_excluded
# (снимок gsa-02 от 07.09.2026) — запасной источник, пока свежей выгрузки нет.
SUCCESS_EXTRA = [Path("/srv/share/gsa_success_boost"), Path("/srv/share/gsa_success_excluded")]
LINKS_FILE = ROOT / "data" / "boost_links.json"          # учёт ссылок по проектам
ARCHIVE_QUEUE = Path("/srv/share/intake/archive/queue.json")  # проекты, набравшие порог

# Насколько старой может быть выгрузка, прежде чем о ней стоит сказать вслух.
STALE_HOURS = 30      # штатная выгрузка идёт раз в сутки


def refresh(cfg, timeout=240):
    """Попросить машины выложить свежие .success на общий диск.

    Без этого ежечасная проверка бессмысленна: выгрузка идёт раз в сутки, и
    сигнал о первой ссылке опаздывал бы на сутки. Действие collect входит в
    штатный список агента. Машина, которая не ответила, не считается пустой:
    её прошлая выгрузка остаётся в силе, а возраст будет назван вслух.
    """
    for nd in cfg.get("nodes", []):
        if nd.get("collect") is False:
            continue                 # узел получает задачи, но не выгружает (gsa-02, 07.09.2026)
        base = nd["url"].rstrip("/")
        hdr = {"Authorization": "Bearer " + nd["token"],
               "Content-Type": "application/json"}

        def call(path, body=None):
            req = urllib.request.Request(
                base + path, data=(json.dumps(body).encode() if body else None),
                headers=hdr)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        try:
            jid = call("/run", {"action": "collect"}).get("job_id")
        except Exception as exc:
            print("  " + nd["name"] + ": обновить выгрузку не вышло ("
                  + type(exc).__name__ + "), считаем по прошлой")
            continue
        if not jid:
            print("  " + nd["name"] + ": агент не вернул номер задания")
            continue
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(5)
            try:
                st = call("/job/" + jid)
            except Exception:
                break
            if st.get("status") == "running":
                continue
            if st.get("rc") in (0, None):
                print("  " + nd["name"] + ": выгрузка обновлена")
            else:
                print("  " + nd["name"] + ": выгрузка вернула код "
                      + str(st.get("rc")) + ", считаем по прошлой")
            break


def load_tasks():
    """Принятые заявки. Нечитаемая строка попадает в жалобы, а не молчит."""
    rows, bad = [], 0
    if not TASKS.exists():
        return rows, bad
    for line in TASKS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    return rows, bad


def count_lines(path: Path) -> int:
    n = 0
    with path.open("rb") as fh:
        for _ in fh:
            n += 1
    return n


def published_index():
    """{имя проекта: (строк, машина)} плюс сведения о свежести выгрузок.

    Файл берётся по точному имени проекта: GSA называет файлы по имени проекта,
    и наши заявки создают проекты тем же именем.
    """
    per_project, nodes = {}, []
    if not SUCCESS.is_dir():
        return per_project, nodes, "каталога выгрузок нет: " + str(SUCCESS)
    now = datetime.now(timezone.utc)
    roots = [SUCCESS] + [r for r in SUCCESS_EXTRA if r.is_dir()]
    node_dirs = [d for r in roots for d in sorted(r.iterdir()) if d.is_dir()]
    for node_dir in node_dirs:
        files = sorted(node_dir.glob("*.success"))
        newest = max((f.stat().st_mtime for f in files), default=None)
        age_h = None if newest is None else (
            now - datetime.fromtimestamp(newest, timezone.utc)).total_seconds() / 3600
        nodes.append({"name": node_dir.name, "files": len(files), "age_h": age_h})
        for f in files:
            try:
                n = count_lines(f)
                # один проект может лежать и в отложенной, и в свежей выгрузке — берём большее
                if n >= per_project.get(f.stem, (0, ""))[0]:
                    per_project[f.stem] = (n, node_dir.name)
            except OSError as exc:
                nodes[-1].setdefault("errors", []).append(f.name + ": " + str(exc))
    return per_project, nodes, ""


def stage_index():
    """{имя проекта: 'в очереди'|'в работе'}. Проект, ушедший в imported, взят
    GSA в работу; оставшийся в pending ещё ждёт."""
    stages = {}
    for p in PENDING.glob("*.prj") if PENDING.is_dir() else []:
        stages[p.stem] = "в очереди"
    # imported важнее: если файл есть в обоих местах, работа уже началась
    for p in IMPORTED.glob("*.prj") if IMPORTED.is_dir() else []:
        stages[p.stem] = "в работе"
    return stages


def track_links(cfg, tasks, pub, stages, dry_run):
    """Учёт поставленных ссылок по каждому boost-проекту и очередь на архив.

    Пишет data/boost_links.json: проект → ссылок сейчас, машина, когда впервые
    замечены, когда счёт менялся. Проекты, набравшие boost_archive_after_links
    (конфиг; 0 или нет ключа = учёт без порога), попадают в очередь на архив на
    шаре — её забирает нода-исполнитель. Возвращает (учтено, в очереди на архив)."""
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    prev = {}
    if LINKS_FILE.exists():
        try:
            prev = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    threshold = cfg.get("boost_archive_after_links", 0)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        threshold = 0
    table, queue = {}, []
    for t in tasks:
        if t.get("status") == "invalid":
            continue
        prj = t.get("project") or ""
        if not prj:
            continue
        links, node = pub.get(prj, (0, ""))
        old = prev.get(prj, {})
        rec = {"task_id": t.get("task_id"), "url": t.get("url", ""),
               "links": links, "node": node, "stage": stages.get(prj, ""),
               "first_seen": old.get("first_seen") or (now if links > 0 else None),
               "changed": now if links != old.get("links") else old.get("changed"),
               "updated": now}
        table[prj] = rec
        if threshold and links >= threshold and stages.get(prj) == "в работе":
            queue.append({"project": prj, "links": links, "task_id": rec["task_id"],
                          "url": rec["url"], "node": node})
    top = sorted(table.items(), key=lambda kv: -kv[1]["links"])[:10]
    if top and top[0][1]["links"] > 0:
        print("\nссылок по проектам (верх 10): "
              + "; ".join(f"{k[:40]}={v['links']}" for k, v in top if v["links"] > 0))
    if threshold:
        print("порог архива %d ссылок: набрали %d проект(ов)" % (threshold, len(queue)))
    if not dry_run:
        LINKS_FILE.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
        if threshold:
            try:
                ARCHIVE_QUEUE.parent.mkdir(parents=True, exist_ok=True)
                ARCHIVE_QUEUE.write_text(json.dumps(
                    {"generated": now, "after_links": threshold, "projects": queue},
                    ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError as exc:
                print("  очередь на архив не записана: " + str(exc))
    return len(table), len(queue)


def tg_send(cfg, text):
    """Отправка через тот же путь, что и приём заявок: тот же ключ бота и тот же
    чат, повторы при таймауте (хост периодически не достукивается до
    api.telegram.org) и без parse_mode, как принято в intake."""
    sys.path.insert(0, str(ROOT))
    from intake import _tg_send
    return _tg_send(cfg, text)


def main():
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description="сводка по заявкам и сигнал о первых ссылках")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать текст сообщений, но не отправлять")
    ap.add_argument("--list", action="store_true", help="только сводка")
    ap.add_argument("--full", action="store_true",
                    help="печатать и заявки без движения")
    ap.add_argument("--no-refresh", action="store_true",
                    help="не просить машины обновить выгрузку")
    a = ap.parse_args()

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    if not a.no_refresh:
        refresh(cfg)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    seen = state.get("first_link", {})     # task_id -> когда замечена первая ссылка

    pub, nodes, fatal = published_index()
    if fatal:
        print("  " + fatal + " -- опубликованное НЕ ПРОВЕРЕНО ни по одной заявке")
    stale = []
    for nd in nodes:
        if nd["age_h"] is None:
            print("  " + nd["name"] + ": выгрузки нет -- его ссылки НЕ ВИДНЫ")
            stale.append(nd["name"])
            continue
        mark = ""
        if nd["age_h"] > STALE_HOURS:
            mark = "  УСТАРЕЛА, новее могло появиться"
            stale.append(nd["name"])
        print("  %s: файлов %d, выгрузке %.0f ч%s"
              % (nd["name"], nd["files"], nd["age_h"], mark))

    stages = stage_index()
    tasks, bad = load_tasks()
    if bad:
        print("  ВНИМАНИЕ: " + str(bad) + " строк журнала заявок не разобрано, "
              "сводка ниже посчитана без них")

    rows, fresh = [], []
    n_queued = n_work = n_pub = n_lost = tot_links = 0
    for t in tasks:
        if t.get("status") == "invalid":
            continue
        tid, prj, url = t.get("task_id", "?"), t.get("project") or "", t.get("url", "")
        links, node = pub.get(prj, (0, ""))
        stage = stages.get(prj, "")
        if links > 0:
            n_pub += 1
            tot_links += links
            note = node
        elif stage == "в работе":
            n_work += 1
            note = "в работе, ссылок пока не видно"
        elif stage == "в очереди":
            n_queued += 1
            note = "в очереди на импорт"
        else:
            n_lost += 1
            note = "в конвейере не найден"
        rows.append((tid, url, links, note))
        if links > 0 and tid not in seen:
            fresh.append((tid, url, links, node))

    track_links(cfg, tasks, pub, stages, a.dry_run)

    print("\n" + "заявка".ljust(13) + "опубликовано".rjust(13) + "  сайт / состояние")
    shown = 0
    for tid, url, links, note in rows:
        if not a.full and links == 0:
            continue
        shown += 1
        print(tid.ljust(13) + format(links, ",").rjust(13) + "  "
              + url[:34] + "  (" + note + ")")
    if not rows:
        print("  заявок нет")
    elif shown == 0:
        print("  ни по одной заявке опубликованных ссылок пока не видно")
    elif not a.full and len(rows) > shown:
        print("  скрыто заявок без ссылок: " + str(len(rows) - shown)
              + " (--full покажет)")

    print("\nПринято %d   в очереди %d   в работе %d   со ссылками %d (всего ссылок %s)"
          % (len(rows), n_queued, n_work, n_pub, format(tot_links, ",")))
    if n_lost:
        print("  " + str(n_lost) + " заявок в конвейере не найдено -- "
              "это не значит, что ссылок нет, это значит, что проекта не видно "
              "ни в очереди, ни в работе, ни в выгрузке")
    if stale:
        print("  выгрузка устарела или отсутствует у: " + ", ".join(stale)
              + " -- ноль по их проектам означает 'пока не видно', а не 'нет'")

    # Отметку о запуске пишем всегда, в том числе когда новостей нет: пустой
    # файл состояния иначе неотличим от "скрипт ни разу не отработал".
    now0 = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    if not a.dry_run:
        state["last_run"] = now0
        state["last_seen"] = {"принято": len(rows), "в очереди": n_queued,
                              "в работе": n_work, "со ссылками": n_pub,
                              "ссылок всего": tot_links}
        state.setdefault("first_link", seen)
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    if not fresh:
        return 0
    if a.list:
        print("\nзаявок с первыми ссылками: " + str(len(fresh))
              + " (отправка не запрашивалась)")
        return 0

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%d.%m.%Y %H:%M UTC")
    for tid, url, links, node in fresh:
        text = ("✅ Пошли ссылки\n"
                "Заявка: " + tid + "\n"
                "Сайт: " + url + "\n"
                "Опубликовано: " + format(links, ",") + "\n"
                "Машина: " + (node or "не указана") + "\n"
                "Замечено: " + stamp)
        if a.dry_run:
            print("\n[вхолостую] отправил бы:\n" + text)
            continue
        if tg_send(cfg, text):
            seen[tid] = stamp
            print("  по " + tid + " сообщение отправлено")
    if not a.dry_run:
        state["first_link"] = seen
        state["last_run"] = now.replace(microsecond=0).isoformat()
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
