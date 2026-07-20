#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_email_providers.py — какие GSA-генераторы почт живы и ПРИНИМАЮТ письма.

Генераторы = .email.ini с `pop3 server=WEB` (temp-mail: читают инбокс по web parse url,
без POP3/логина). По каждому: шлём письмо с уникальным ТОКЕНОМ в теме на <rand>@<домен>,
затем тянем инбокс по его URL и ищем токен. Токен НЕ равен адресу — чтобы эхо адреса на
странице не давало ложного «принимает».

Отправка тестов — с рабочего SMTP (по умолчанию gmail-креды из
/srv/share/gsa_email_providers/_smtp.json: {host,port,user,password}).

    python check_email_providers.py [--engines DIR] [--smtp FILE]
                                    [--only ПОДСТРОКА] [--timeout СЕК] [--include-disabled]
"""

import argparse
import json
import random
import smtplib
import ssl
import string
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEF_ENGINES = "/srv/share/Email_Engines"
DEF_SMTP = "/srv/share/gsa_email_providers/_smtp.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def parse_ini(path: Path) -> dict:
    d = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line[0] in ";[":
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip().lower()] = v.strip()
    return d


def is_generator(ini: dict) -> bool:
    return ini.get("pop3 server", "").upper() == "WEB" and bool(ini.get("web parse url"))


def domains_of(ini: dict) -> list:
    return [seg.strip().lstrip("*").lstrip("@")
            for seg in ini.get("emails match", "").split("|") if "@" in seg]


def rand(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def make_url(tmpl: str, addr: str, user: str, host: str) -> str:
    return (tmpl.replace("%email%", addr).replace("%emailuser%", user)
                .replace("%emailhost%", host))


def cookie_of(path: Path, addr: str, user: str, host: str):
    """Из .ini берём подсказку про cookie (GSA-строка `set cookies=…`, даже
    закомментированную `;`), подставляем плейсхолдеры. Напр. generator.email:
    `surl=%emailhost%/%emailuser%` — без неё инбокс не отдаётся."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        l = line.strip().lstrip(";").strip()
        if l.lower().startswith("set cookies="):
            return make_url(l.split("=", 1)[1].strip(), addr, user, host)
    return None


def fetch(url: str, timeout: int = 15, cookie: str = None):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # temp-mail сайты часто с кривыми сертами
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if cookie:
        headers["Cookie"] = cookie
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read(500000).decode("utf-8", "replace")
    except (HTTPError, URLError, ssl.SSLError, OSError, ValueError):
        return None


def send(smtp: dict, to: str, token: str) -> None:
    msg = MIMEText(f"GSA generator liveness test.\nToken: {token}\n")
    msg["Subject"] = token                    # токен = вся тема, легко найти в инбоксе
    msg["From"] = smtp["user"]
    msg["To"] = to
    s = smtplib.SMTP_SSL(smtp["host"], int(smtp["port"]), timeout=25,
                         context=ssl.create_default_context())
    s.login(smtp["user"], smtp["password"])
    s.sendmail(smtp["user"], [to], msg.as_string())
    s.quit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default=DEF_ENGINES)
    ap.add_argument("--smtp", default=DEF_SMTP)
    ap.add_argument("--only", help="тестить только .ini с этой подстрокой в имени")
    ap.add_argument("--timeout", type=int, default=150, help="сек ждать доставку письма")
    ap.add_argument("--include-disabled", action="store_true",
                    help="тестить и провайдеры с enabled=0 (по умолч. тоже тестим — флаг для явности)")
    args = ap.parse_args()

    smtp = json.loads(Path(args.smtp).read_text(encoding="utf-8"))
    eng = Path(args.engines)
    if not eng.is_dir():
        sys.exit(f"Папка движков не найдена: {eng}")

    gens = []
    for f in sorted(eng.glob("*.ini")):
        ini = parse_ini(f)
        if not is_generator(ini):
            continue
        if args.only and args.only.lower() not in f.name.lower():
            continue
        gens.append((f.stem, ini, f))
    if not gens:
        sys.exit("Генераторов (pop3 server=WEB) не найдено.")

    print(f"Генераторов к проверке: {len(gens)}. Отправляю тестовые письма…\n")
    # t = [name, gsa_enabled, addr, url, token, status, ever_fetched]
    tests = []
    for name, ini, path in gens:
        gsa_on = ini.get("enabled", "1") != "0"
        doms = domains_of(ini)
        if not doms:
            tests.append([name, gsa_on, None, None, None, "нет доменов", False, None])
            continue
        dom = doms[0]
        user = "gsa" + rand(7)
        token = "tkn" + rand(11)              # тема; НЕ совпадает с адресом
        addr = f"{user}@{dom}"
        url = make_url(ini["web parse url"], addr, user, dom)
        ck = cookie_of(path, addr, user, dom)  # напр. surl=… для generator.email
        fetch(url, timeout=10, cookie=ck)      # прогрев: некоторые создают ящик при заходе
        try:
            send(smtp, addr, token)
            tests.append([name, gsa_on, addr, url, token, "отправлено", False, ck])
            print(f"  → {name:<22} {addr}")
        except Exception as e:
            tests.append([name, gsa_on, addr, url, token,
                          f"send err: {type(e).__name__}", False, ck])
            print(f"  ✗ {name:<22} отправка не удалась: {type(e).__name__}", file=sys.stderr)

    print(f"\nЖду доставку (до {args.timeout} c), опрашиваю инбоксы…")
    deadline = time.time() + args.timeout
    pending = [t for t in tests if t[5] == "отправлено"]
    while pending and time.time() < deadline:
        time.sleep(12)
        for t in list(pending):
            body = fetch(t[3], cookie=t[7])
            if body is not None:
                t[6] = True
                if t[4] in body:
                    t[5] = "✓ ПРИНИМАЕТ"
                    pending.remove(t)
                    print(f"  ✓ {t[0]}")
    for t in pending:
        t[5] = "✗ письмо не дошло" if t[6] else "✗ URL недоступен"

    # отчёт
    print("\n" + "=" * 64)
    print(f"{'ГЕНЕРАТОР':<22} {'GSA':<5} РЕЗУЛЬТАТ")
    print("-" * 64)
    order = {"✓ ПРИНИМАЕТ": 0}
    for t in sorted(tests, key=lambda x: (order.get(x[5], 1), x[0].lower())):
        name, gsa_on, addr, url, token, status = t[:6]
        dom = addr.split("@")[-1] if addr else "—"
        print(f"{name:<22} {'вкл' if gsa_on else 'выкл':<5} {status}   ({dom})")
    alive = sum(1 for t in tests if t[5] == "✓ ПРИНИМАЕТ")
    print("-" * 64)
    print(f"Живых (принимают письма): {alive} из {len(tests)}")


if __name__ == "__main__":
    main()
