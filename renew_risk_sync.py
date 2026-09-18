#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""renew_risk_sync.py — забирает список renew-risk с go2.finvision-academy.com и ставит
заявки на прокачку в наш приём (intake API) по каждому домену, которого в заявках нет.
Крон раз в сутки (/etc/cron.d/gsa-renew-risk-sync). Только stdlib.

Две калитки, без которых ничего не отправляется:
  1) файл учётных данных /root/.finvision/basic (строка user:password, права 600) —
     скрипт его не печатает и не пишет в журнал;
  2) ключ renew_anchor_rule = "brand-country" в data/gsa_checker.config.json — якоря по
     правилу прошлых заявок (renew_anchors.py); пока ключа нет, заявки не отправляются,
     а подготовленный список кладётся на шару для просмотра. Домены, из имени которых
     бренд не выводится, в заявки не идут — они в renew-risk.review.txt для ручных якорей.

Идемпотентность: домен, у которого уже есть заявка (по домену из url), пропускается.
Страны списка (русские названия) → регионы приёма через ALIAS; нераспознанные — в отчёт.

  python3 renew_risk_sync.py --dry-run      # разобрать и показать, не отправлять
  python3 renew_risk_sync.py --from-file /srv/share/intake/renew-risk.json --dry-run
"""
import argparse, base64, json, os, sys, time, urllib.request, urllib.error
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path("/root/gsa-checker")
CFG = ROOT / "data" / "gsa_checker.config.json"
TASKS = ROOT / "data" / "intake_tasks.jsonl"
STATE = ROOT / "data" / "renew_risk_state.json"
AUTH_FILE = Path("/root/.finvision/basic")
API_URL = "https://go2.finvision-academy.com/api/renew-risk"
SHARE = Path("/srv/share/intake")
INTAKE = "http://127.0.0.1:8791"
LPD = 3

ALIAS = {
    "Саудовская Аравия": "Арабские страны", "Катар": "Арабские страны", "Иордания": "Арабские страны",
    "Египет": "Арабские страны", "Оман": "Арабские страны", "Кувейт": "Арабские страны",
    "Бахрейн": "Арабские страны", "Ирак": "Арабские страны", "Марокко": "Арабские страны",
    "Тунис": "Арабские страны", "Алжир": "Арабские страны", "ОАЭ": "Арабские страны", "Ливан": "Арабские страны",
    "Сирия": "Арабские страны", "Йемен": "Арабские страны",
    "ЮАР": "Африка", "Нигерия": "Африка", "Кения": "Африка", "Эфиопия": "Африка", "Гана": "Африка",
    "Танзания": "Африка", "Мозамбик": "Африка", "Уганда": "Африка", "Камерун": "Африка",
    "Никарагуа": "Латинская Америка", "Боливия": "Латинская Америка", "Гватемала": "Латинская Америка",
    "Парагвай": "Латинская Америка", "Венесуэла": "Латинская Америка", "Гондурас": "Латинская Америка",
    "Коста-Рика": "Латинская Америка", "Панама": "Латинская Америка", "Доминикана": "Латинская Америка",
    "Сальвадор": "Латинская Америка", "Куба": "Латинская Америка",
    "Узбекистан": "СНГ", "Казахстан": "СНГ", "Азербайджан": "СНГ", "Киргизия": "СНГ", "Грузия": "СНГ", "Армения": "СНГ",
    "Бангладеш": "Азия", "Шри-Ланка": "Азия", "Непал": "Азия", "Камбоджа": "Азия", "Тайвань": "Азия", "Гонконг": "Азия",
    "Новая Зеландия": "Австралия", "Нидерланды": "Европа", "Чехия": "Европа", "Румыния": "Европа", "Греция": "Европа",
    "Венгрия": "Европа", "Болгария": "Европа", "Швеция": "Европа", "Норвегия": "Европа", "Дания": "Европа",
    "Финляндия": "Европа", "Австрия": "Европа", "Швейцария": "Европа", "Бельгия": "Европа", "Ирландия": "Европа",
    "Канада": "США",
}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def host(u):
    try:
        h = (urlsplit(u).hostname or u).lower()
    except ValueError:
        h = u.lower()
    return h[4:] if h.startswith("www.") else h


def fetch_list():
    if not AUTH_FILE.is_file():
        return None, "нет файла учётных данных " + str(AUTH_FILE)
    cred = AUTH_FILE.read_text(encoding="utf-8").strip()
    req = urllib.request.Request(API_URL, headers={
        "Authorization": "Basic " + base64.b64encode(cred.encode()).decode(),
        "User-Agent": "gsa-renew-sync/1"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        return None, "API ответил %d" % e.code
    except (urllib.error.URLError, OSError) as e:
        return None, "API недоступен: %s" % e
    try:
        return json.loads(raw), ""
    except ValueError:
        return None, "ответ не JSON"


def known_domains():
    out = set()
    if TASKS.exists():
        for line in TASKS.read_text(encoding="utf-8").splitlines():
            try:
                t = json.loads(line)
            except ValueError:
                continue
            if t.get("status") != "invalid" and t.get("url"):
                out.add(host(t["url"]))
    return out


def valid_countries(cfg):
    sys.path.insert(0, str(ROOT))
    import intake
    return set(intake.valid_countries(cfg))


def build_tasks(rows, cfg, known):
    valid = valid_countries(cfg)
    rule = cfg.get("renew_anchor_rule")            # "brand-country" — правило из прошлых заявок
    from renew_anchors import anchors_for, needs_review
    tasks, unmapped, skipped = [], {}, 0
    review = []
    for r in rows:
        dom = host(r.get("domain", ""))
        if not dom or "." not in dom:
            continue
        if dom in known:
            skipped += 1
            continue
        c = str(r.get("country") or "")
        region = c if c in valid else ALIAS.get(c)
        if not region:
            unmapped[c] = unmapped.get(c, 0) + 1
            continue
        url = "https://" + dom + "/"
        anchors = anchors_for(dom, c) if rule == "brand-country" else []
        if needs_review(dom):
            review.append((dom, c, anchors[0] if anchors else ""))
            continue                                  # бренд из имени не вывести — якоря руками
        tasks.append({"url": url, "country": region, "anchors": anchors, "links_per_day": LPD,
                      "_src": {"refdomains": r.get("refdomains"), "flatDays": r.get("flatDays"),
                               "country": c, "expiry": r.get("expiry")}})
    head = "домены, для которых бренд из имени не выводится — якоря задать руками:"
    body = [head] + ["%s\t%s\t%s" % r for r in review]
    (SHARE / "renew-risk.review.txt").write_text("\n".join(body) + "\n", encoding="utf-8")
    return tasks, unmapped, skipped


def submit(cfg, tasks):
    tok = cfg.get("intake_token")
    if not tok:
        return 0, ["нет intake_token"]
    sent, errors = 0, []
    for i in range(0, len(tasks), 100):
        batch = [{k: v for k, v in t.items() if not k.startswith("_")} for t in tasks[i:i + 100]]
        req = urllib.request.Request(INTAKE + "/api/tasks", data=json.dumps(batch).encode(),
                                     headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                res = json.loads(r.read())
            sent += int(res.get("queued", 0))
            for x in res.get("results", []):
                if x.get("status") != "queued":
                    errors.append("%s: %s" % (x.get("project") or "?", x.get("error")))
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append("пачка %d: %s" % (i // 100 + 1, e))
    return sent, errors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-file", help="разобрать сохранённый JSON вместо запроса к API")
    a = ap.parse_args()
    cfg = json.loads(CFG.read_text(encoding="utf-8-sig"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    if a.from_file:
        data, err = json.loads(Path(a.from_file).read_text(encoding="utf-8")), ""
    else:
        data, err = fetch_list()
    if data is None:
        log("список не получен: " + err)
        state["last_error"] = err; state["last_run"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        return 1
    rows = data.get("rows", [])
    log("список: %d доменов, замер %s, hash %s" % (len(rows), data.get("measured"), str(data.get("hash"))[:12]))
    if not a.from_file:
        (SHARE / "renew-risk.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tasks, unmapped, skipped = build_tasks(rows, cfg, known_domains())
    log("уже в заявках: %d; новых к отправке: %d; страны без региона: %s" % (skipped, len(tasks), unmapped or "нет"))
    (SHARE / "renew-risk.tasks.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    if cfg.get("renew_anchor_rule") != "brand-country":
        log("правило якорей renew_anchor_rule не включено — заявки НЕ отправлены, список лежит в renew-risk.tasks.json")
        rc = 0
    elif a.dry_run:
        log("сухой прогон — не отправлялось")
        rc = 0
    elif tasks:
        sent, errors = submit(cfg, tasks)
        log("отправлено заявок: %d; ошибок: %d" % (sent, len(errors)))
        for e in errors[:20]:
            log("  " + e)
        rc = 0 if not errors else 2
    else:
        rc = 0
    state.update({"last_run": time.strftime("%Y-%m-%dT%H:%M:%S"), "last_hash": data.get("hash"),
                  "last_rows": len(rows), "last_new": len(tasks), "unmapped": unmapped, "last_error": ""})
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return rc


if __name__ == "__main__":
    sys.exit(main())
