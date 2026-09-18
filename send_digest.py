#!/usr/bin/env python3
"""Разовая сводная отправка по накопленным за пять недель заявкам.

Зачем разово. Скрипт сводки шлёт сообщение по каждой заявке при первом
появлении ссылок. Обычно это по одной штуке, но узел gsa-02 не выгружал
результаты с 20 июля, и когда выгрузку восстановили, «первые ссылки» появились
у 145 заявок разом. Залп из 145 сообщений в чат неотменяем, да и Телеграм такие
пачки придерживает. Поэтому одно сводное, а все 145 помечаются оповещёнными —
дальше скрипт работает как задуман.

Запуск без --send ничего не отправляет, только показывает текст и числа.
"""
import argparse
import io
import json
import sys
from pathlib import Path

ROOT = Path("/root/gsa-checker")
sys.path.insert(0, str(ROOT))

import intake_watch as iw  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="разовая сводная отправка")
ap.add_argument("--send", action="store_true", help="действительно отправить")
a = ap.parse_args()

cfg = json.loads(iw.CFG.read_text(encoding="utf-8"))
state = json.loads(iw.STATE.read_text(encoding="utf-8")) if iw.STATE.exists() else {}
seen = state.get("first_link", {})

pub, nodes, fatal = iw.published_index()
if fatal:
    sys.exit("ОТКАЗ: " + fatal)
stages = iw.stage_index()
tasks, bad = iw.load_tasks()
if bad:
    print("ВНИМАНИЕ: %d строк журнала заявок не разобрано" % bad)

with_links, in_work_no_links, total_links = [], [], 0
for t in tasks:
    if t.get("status") == "invalid":
        continue
    tid, prj = t.get("task_id", "?"), t.get("project") or ""
    links, node = pub.get(prj, (0, ""))
    if links > 0:
        with_links.append(tid)
        total_links += links
    elif stages.get(prj) == "в работе":
        in_work_no_links.append(tid)

in_work = len(with_links) + len(in_work_no_links)
text = ("✅ Пошли ссылки — сводно\n\n"
        "Из %d заявок, взятых в работу:\n"
        "   %d уже со ссылками\n"
        "   %d пока без\n\n"
        "Всего опубликовано ссылок: %s\n\n"
        "Это накопленное за пять недель: узел gsa-02 не выгружал результаты "
        "с 20 июля, сегодня выгрузка восстановлена. Дальше сообщения пойдут "
        "по каждой заявке отдельно, когда у неё появятся первые ссылки."
        % (in_work, len(with_links), len(in_work_no_links), format(total_links, ",").replace(",", " ")))

print(text)
print("\n--- будет помечено оповещёнными: %d заявок ---" % len(with_links))

if not a.send:
    print("(отправка не запрашивалась: добавьте --send)")
    raise SystemExit(0)

if not iw.tg_send(cfg, text):
    sys.exit("ОТКАЗ: сообщение не отправлено — состояние НЕ меняем, "
             "иначе заявки останутся без оповещения навсегда")

stamp = "сводно 01.09.2026"
for tid in with_links:
    seen.setdefault(tid, stamp)
state["first_link"] = seen
iw.STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
print("отправлено; помечено оповещёнными: %d" % len(with_links))
